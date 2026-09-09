"""Tests for Feature 2: Smart Trace Diagnosis & Event Compression."""

from pathlib import Path
import pytest

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator
from iris.core.observer import IrisObserver
from iris.mcp.server import create_server
from iris.storage.db import (
    arm_session,
    diagnose_session,
    get_session,
    init_db,
)


def buggy_pipeline(items: list) -> int:
    total = 0
    # 1. Repetitive loop
    for x in items:
        total += x * 2

    # 2. Unexpected type mutation
    user_data = {"id": 123, "active": True}
    if total > 50:
        user_data = None  # Mutated from dict to None!

    # 3. Crash caused by previous mutation
    try:
        status = user_data["active"]
    except Exception as ex:
        # Crash
        raise ex

    return total


def test_smart_diagnosis_loop_and_root_cause(tmp_path):
    db_path = tmp_path / "diag.db"
    init_db(db_path)
    queue = EventQueue(db_path=db_path)

    sid = "diag_session_1"
    current_file = Path(__file__).name

    arm_session(sid, current_file, "buggy_pipeline", db_path=db_path)
    coord = SessionCoordinator(sid, current_file, "buggy_pipeline")
    observer = IrisObserver(coordinator=coord, event_queue=queue)
    observer.attach()

    try:
        try:
            # 30 items -> loop runs 30 times, total > 50 -> user_data becomes None -> crashes!
            buggy_pipeline(list(range(30)))
        except TypeError:
            pass

        queue.flush()

        # Run diagnosis
        diagnosis = diagnose_session(sid, db_path=db_path)
        assert diagnosis is not None
        assert diagnosis["session_id"] == sid

        # 1. Check Loop Collapsing
        executions = diagnosis["executions"]
        assert len(executions) >= 1
        ex0 = executions[0]
        assert len(ex0["loops_detected"]) >= 1
        loop = ex0["loops_detected"][0]
        assert loop["iterations"] >= 3

        # 2. Check Type Mutation Detection
        assert len(ex0["type_mutations"]) >= 1
        mut = ex0["type_mutations"][0]
        assert mut["variable"] == "user_data"
        assert mut["from_type"] in ("dict", "Dict")
        assert mut["to_type"] in ("NoneType", "none", "None")

        # 3. Check Root Cause Analysis
        assert ex0["exception"] is not None
        assert ex0["root_cause"] is not None
        assert ex0["root_cause"]["variable"] == "user_data"
        assert ex0["root_cause"]["confidence"] == "HIGH"

        # 4. Check Markdown Report
        report = diagnosis["report_markdown"]
        assert "Collapsed Loops" in report
        assert "Unexpected Variable Type Mutations" in report
        assert "Probable Root Cause" in report
        print("\n" + report)

    finally:
        observer.detach()
        queue.stop()


def test_mcp_diagnose_tool_integration(tmp_path, monkeypatch):
    db_path = tmp_path / "mcp_diag.db"
    init_db(db_path)
    monkeypatch.setenv("IRIS_DB_PATH", str(db_path))

    import asyncio
    server = create_server()
    tools_res = asyncio.run(server.list_tools())
    tools = [t.name for t in tools_res]
    assert "iris_diagnose_anomaly" in tools
    assert "iris_arm_entry" in tools
