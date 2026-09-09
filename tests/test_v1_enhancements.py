"""Unit tests for Iris v1 enhancements: Auto-timeout, Variable Delta, Task ID, and Pruning."""

import asyncio
import time
from pathlib import Path
import pytest

from iris.core.runtime import trace_context
from iris.storage.db import (
    arm_session,
    clean_db,
    get_session,
    inspect_execution_flow,
    query_call_tree,
)


def test_session_auto_timeout():
    """Verify that a session past its timeout automatically transitions to ABORTED_TIMEOUT."""
    sid = "test_timeout_sess"
    # Arm with 1 second timeout
    arm_session(session_id=sid, entry_file="test.py", entry_function="never_called", timeout_seconds=1)

    # Immediately check: should be ARMED
    sess = get_session(sid)
    assert sess["state"] == "ARMED"

    # Sleep 1.2s to exceed timeout
    time.sleep(1.2)

    # Check again: get_session should detect timeout and return ABORTED_TIMEOUT
    sess_after = get_session(sid)
    assert sess_after["state"] == "ABORTED_TIMEOUT"


def helper_delta_func():
    a = 1
    b = 2
    a = 10  # Only 'a' changes here
    c = 30  # 'c' is newly added here
    return a + b + c


def test_variable_delta_tracking():
    """Verify that LINE events record delta variables instead of redundant full snapshots."""
    current_file = Path(__file__).name

    with trace_context(entry_file=current_file, entry_function="helper_delta_func") as sid:
        res = helper_delta_func()
        assert res == 42

    tree = query_call_tree(sid)
    exec_id = tree[0]["execution_id"]
    flow = inspect_execution_flow(exec_id)

    line_events = [e for e in flow["events"] if e["event_type"] == "LINE"]
    assert len(line_events) >= 4

    deltas = [e.get("payload", {}).get("delta", {}) for e in line_events]
    print(f"Recorded deltas: {deltas}")

    # Check that later lines only record the changed variable in delta
    # line 'a = 10': delta should have 'a': 10 and not 'b'
    changed_a = any(d.get("a") == 10 and "b" not in d for d in deltas)
    assert changed_a, "Variable delta did not isolate changed variable 'a'!"

    # line 'c = 30': delta should have 'c': 30 and not 'a' or 'b'
    changed_c = any(d.get("c") == 30 and "b" not in d for d in deltas)
    assert changed_c, "Variable delta did not isolate newly introduced variable 'c'!"


async def sample_async_task():
    await asyncio.sleep(0.01)
    return 100


def test_async_task_id_capture():
    """Verify that async_task_id is recorded in executions when running inside an asyncio event loop."""
    current_file = Path(__file__).name

    async def run_coro():
        with trace_context(entry_file=current_file, entry_function="sample_async_task") as sid:
            task = asyncio.create_task(sample_async_task(), name="iris_test_worker_task")
            res = await task
            assert res == 100
            return sid

    sid = asyncio.run(run_coro())
    tree = query_call_tree(sid)
    assert len(tree) == 1
    root = tree[0]
    print(f"Recorded async_task_id: {root.get('async_task_id')}")
    assert root.get("async_task_id") is not None
    assert "iris_test_worker_task" in root.get("async_task_id")


def test_db_clean_pruning():
    """Verify that clean_db successfully prunes old sessions."""
    for i in range(10):
        arm_session(f"prune_test_{i}", "dummy.py", "dummy_fn")

    deleted = clean_db(keep_latest=3)
    assert deleted >= 7
