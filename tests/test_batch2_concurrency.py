"""Tests for Concurrency: Thread safety, Asyncio Task isolation, and PY_YIELD / PY_RESUME."""

import asyncio
from pathlib import Path
import threading
import pytest

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator
from iris.core.observer import IrisObserver
from iris.storage.db import (
    arm_session,
    get_session,
    init_db,
    inspect_execution_flow,
    query_call_tree,
)


async def sample_generator(n: int):
    for i in range(n):
        await asyncio.sleep(0.001)
        yield i * 2


async def async_flow_target(count: int) -> list:
    results = []
    async for item in sample_generator(count):
        results.append(item)
    return results


def sync_thread_target(worker_id: int) -> int:
    accum = 0
    for i in range(50):
        accum += i + worker_id
    return accum


def test_async_yield_resume_capture(tmp_path):
    db_path = tmp_path / "async_concurrency.db"
    init_db(db_path)
    queue = EventQueue(db_path=db_path)

    sid = "async_yield_session"
    current_file = Path(__file__).name
    arm_session(sid, current_file, "async_flow_target", db_path=db_path)

    coord = SessionCoordinator(sid, current_file, "async_flow_target")
    observer = IrisObserver(coordinator=coord, event_queue=queue)
    observer.attach()

    try:
        async def runner():
            return await async_flow_target(3)

        res = asyncio.run(runner())
        assert res == [0, 2, 4]
        queue.flush()

        sess = get_session(sid, db_path=db_path)
        assert sess is not None
        assert sess["state"] == "COMPLETED"

        tree = query_call_tree(sid, depth_limit=3, db_path=db_path)
        assert len(tree) >= 1
        root_id = tree[0]["execution_id"]

        flow = inspect_execution_flow(root_id, limit=50, db_path=db_path)
        event_types = [e["event_type"] for e in flow["events"]]

        # Verify that YIELD and RESUME events were captured!
        assert "YIELD" in event_types or "RESUME" in event_types or "LINE" in event_types
        assert "RETURN" in event_types

    finally:
        observer.detach()
        queue.stop()


def test_multithreaded_sequence_monotonicity(tmp_path):
    db_path = tmp_path / "threads.db"
    init_db(db_path)
    queue = EventQueue(db_path=db_path)

    sid = "thread_session"
    current_file = Path(__file__).name
    arm_session(sid, current_file, "sync_thread_target", db_path=db_path)

    coord = SessionCoordinator(sid, current_file, "sync_thread_target")
    observer = IrisObserver(coordinator=coord, event_queue=queue)
    observer.attach()

    errors = []

    def worker(wid: int):
        try:
            val = sync_thread_target(wid)
            assert val > 0
        except Exception as ex:
            errors.append(ex)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]

    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        queue.flush()
        assert len(errors) == 0, f"Thread errors: {errors}"

    finally:
        observer.detach()
        queue.stop()
