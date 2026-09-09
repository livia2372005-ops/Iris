"""Tests for Multi-Session concurrent observation model in Iris."""

from pathlib import Path
import pytest

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.observer import IrisObserver
from iris.storage.db import arm_session, get_session, init_db, query_call_tree


def fn_alpha(x: int) -> int:
    return x * 10


def fn_beta(y: int) -> int:
    return y + 99


def test_multisession_concurrent_support(tmp_path):
    db_path = tmp_path / "multi_session.db"
    init_db(db_path)
    queue = EventQueue(db_path=db_path)

    sid_a = "session_alpha"
    sid_b = "session_beta"
    current_file = Path(__file__).name

    arm_session(sid_a, current_file, "fn_alpha", db_path=db_path)
    arm_session(sid_b, current_file, "fn_beta", db_path=db_path)

    coord_a = SessionCoordinator(sid_a, current_file, "fn_alpha")
    coord_b = SessionCoordinator(sid_b, current_file, "fn_beta")

    observer = IrisObserver(coordinators=[coord_a, coord_b], event_queue=queue)
    observer.attach()

    try:
        # Execute target A
        res_a = fn_alpha(5)
        assert res_a == 50

        # Execute target B
        res_b = fn_beta(1)
        assert res_b == 100

        queue.flush()

        # Verify Session A
        sess_a = get_session(sid_a, db_path=db_path)
        assert sess_a is not None
        assert sess_a["state"] == "COMPLETED"
        tree_a = query_call_tree(sid_a, db_path=db_path)
        assert len(tree_a) == 1
        assert tree_a[0]["function_name"] == "fn_alpha"

        # Verify Session B
        sess_b = get_session(sid_b, db_path=db_path)
        assert sess_b is not None
        assert sess_b["state"] == "COMPLETED"
        tree_b = query_call_tree(sid_b, db_path=db_path)
        assert len(tree_b) == 1
        assert tree_b[0]["function_name"] == "fn_beta"

    finally:
        observer.detach()
        queue.stop()
