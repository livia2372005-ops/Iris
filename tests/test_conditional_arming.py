"""Tests for Feature 1: Conditional Arming with predicate expressions."""

from pathlib import Path
import pytest

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.observer import IrisObserver
from iris.storage.db import arm_session, get_session, init_db, inspect_execution_flow, query_call_tree


def process_order(user_id: int, amount: float, tier: str = "standard") -> dict:
    discount = 0.0
    if tier == "vip" and amount > 500:
        discount = amount * 0.1
    final_amount = amount - discount
    return {"user_id": user_id, "final_amount": final_amount}


def test_conditional_arming_filter_and_trigger(tmp_path):
    db_path = tmp_path / "conditional.db"
    init_db(db_path)
    queue = EventQueue(db_path=db_path)

    sid = "cond_session_1"
    current_file = Path(__file__).name

    # Condition: only trace VIP orders with amount > 500
    cond = "tier == 'vip' and amount > 500"

    arm_session(sid, current_file, "process_order", condition=cond, db_path=db_path)

    coord = SessionCoordinator(sid, current_file, "process_order", condition=cond)
    observer = IrisObserver(coordinator=coord, event_queue=queue)
    observer.attach()

    try:
        # Call 1: standard order, amount 100 -> Condition is FALSE!
        res1 = process_order(1, 100.0, tier="standard")
        assert res1["final_amount"] == 100.0

        # Session should STILL be in ARMED state!
        assert coord.state == SessionState.ARMED

        # Call 2: VIP order, but amount 200 -> Condition is FALSE!
        res2 = process_order(2, 200.0, tier="vip")
        assert res2["final_amount"] == 200.0

        # Session should STILL be in ARMED state!
        assert coord.state == SessionState.ARMED

        # Call 3: VIP order with amount 1000 -> Condition is TRUE!
        res3 = process_order(3, 1000.0, tier="vip")
        assert res3["final_amount"] == 900.0

        # Session should now be COMPLETED!
        queue.flush()
        assert coord.state == SessionState.COMPLETED

        sess = get_session(sid, db_path=db_path)
        assert sess is not None
        assert sess["state"] == "COMPLETED"
        assert sess["condition"] == cond

        tree = query_call_tree(sid, db_path=db_path)
        assert len(tree) == 1
        assert tree[0]["function_name"] == "process_order"

        # Check inspected flow
        flow = inspect_execution_flow(tree[0]["execution_id"], db_path=db_path)
        assert len(flow["events"]) > 0

    finally:
        observer.detach()
        queue.stop()
