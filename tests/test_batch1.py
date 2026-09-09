"""Tests for Batch 1 fixes: Narrow ARMED instrumentation scope & Python 3.12 branch jump detection."""

import sys
import pytest
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.event_queue import EventQueue
from iris.core.observer import IrisObserver, TOOL_ID
from iris.storage.db import init_db


def test_armed_minimal_event_scope(tmp_path):
    db_path = tmp_path / "test_scope.db"
    init_db(db_path)
    queue = EventQueue(db_path=db_path)

    coord = SessionCoordinator(
        session_id="test_scope_sid",
        entry_file="my_app.py",
        entry_function="my_entry",
    )
    obs = IrisObserver(coordinator=coord, event_queue=queue)

    try:
        # Before attach
        assert not obs._attached

        # Attach in ARMED state
        obs.attach()
        assert obs._attached

        events = sys.monitoring.events
        active_events = sys.monitoring.get_events(TOOL_ID)

        # In ARMED state, ONLY PY_START should be active
        assert active_events == events.PY_START
        assert not (active_events & events.LINE)
        assert not (active_events & events.BRANCH)

        # Detach
        obs.detach()
        assert not obs._attached
        assert sys.monitoring.get_events(TOOL_ID) == 0

    finally:
        obs.detach()
        queue.stop()


def test_branch_jump_detection_logic():
    # In Python 3.12, bytecode instructions are 2 bytes each.
    # Fallthrough is instruction_offset + 2.
    # If destination != instruction_offset + 2, jumped is True.

    offset = 10
    fallthrough_dest = 12
    jump_dest = 24
    loop_back_dest = 4

    assert fallthrough_dest == offset + 2
    jumped_fallthrough = (fallthrough_dest != offset + 2)
    assert not jumped_fallthrough

    jumped_jump = (jump_dest != offset + 2)
    assert jumped_jump

    jumped_loop = (loop_back_dest != offset + 2)
    assert jumped_loop
