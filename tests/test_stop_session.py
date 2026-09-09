"""Unit test for active session cancellation (iris_stop_session)."""

from pathlib import Path
import tempfile
import pytest

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.observer import IrisObserver
from iris.storage.db import get_connection, init_db
from iris.mcp.server import create_server


def test_observer_stop_session_active():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "stop_test.db"
        init_db(db_path)

        session_id = "sess_stop_target"
        queue = EventQueue(db_path=db_path, flush_interval=0.01)
        coord = SessionCoordinator(
            session_id=session_id,
            entry_file="sample.py",
            entry_function="target_func",
        )
        observer = IrisObserver(coordinator=coord, event_queue=queue)
        observer.attach()
        assert observer._attached is True

        conn = get_connection(db_path)
        with conn:
            conn.execute(
                "INSERT INTO sessions (session_id, entry_file, entry_function, state) VALUES (?, ?, ?, ?)",
                (session_id, "sample.py", "target_func", "ARMED"),
            )
        conn.close()

        # Actively stop session
        stop_res = observer.stop_session(session_id)
        assert stop_res["state"] == "STOPPED"
        assert coord.state == SessionState.STOPPED
        assert observer._attached is False

        # Verify DB updated
        conn = get_connection(db_path)
        row = conn.execute("SELECT state FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        conn.close()
        assert row["state"] == "STOPPED"
        queue.stop()


def test_mcp_server_stop_session_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mcp_stop.db"
        init_db(db_path)

        server = create_server()
        # Find the iris_stop_session and iris_arm_entry tools
        arm_tool = None
        stop_tool = None
        lineage_tool = None

        for t in server._tool_manager.list_tools():
            if t.name == "iris_arm_entry":
                arm_tool = t
            elif t.name == "iris_stop_session":
                stop_tool = t
            elif t.name == "iris_trace_data_lineage":
                lineage_tool = t

        assert arm_tool is not None
        assert stop_tool is not None
        assert lineage_tool is not None
