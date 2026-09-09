"""Unit tests for value_refs and lineage_edges storage and graph queries."""

import os
from pathlib import Path
import tempfile
import pytest

from iris.core.event_queue import EventQueue
from iris.storage.db import get_connection, init_db, query_data_lineage, stop_session


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_lineage.db"
        init_db(db_path)
        yield db_path


def test_lineage_tables_created(temp_db):
    conn = get_connection(temp_db)
    try:
        tables = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "value_refs" in tables
        assert "lineage_edges" in tables
    finally:
        conn.close()


def test_event_queue_batch_flushes_lineage(temp_db):
    queue = EventQueue(db_path=temp_db, batch_size=2, flush_interval=0.01)
    try:
        # Create execution first for foreign key
        conn = get_connection(temp_db)
        with conn:
            conn.execute(
                "INSERT INTO sessions (session_id, entry_file, entry_function, state) VALUES ('s1', 'f.py', 'fn', 'TRACING')"
            )
            conn.execute(
                "INSERT INTO executions (execution_id, session_id, function_name, file_path, start_time_mono_ns) VALUES ('ex1', 's1', 'fn', 'f.py', 100)"
            )
        conn.close()

        queue.put_value_ref(
            value_ref_id="vref_1",
            execution_id="ex1",
            variable_name="x",
            version=1,
            line_number=10,
            value_snapshot='"initial"',
            epistemic_status="Observed",
        )
        queue.put_value_ref(
            value_ref_id="vref_2",
            execution_id="ex1",
            variable_name="y",
            version=1,
            line_number=12,
            value_snapshot='"derived"',
            epistemic_status="Observed",
        )
        queue.put_lineage_edge(
            edge_id="edge_1",
            source_value_ref_id="vref_1",
            target_value_ref_id="vref_2",
            operation="call:transform",
        )
        queue.flush()

        res_backward = query_data_lineage(value_ref_id="vref_2", direction="BACKWARD", db_path=temp_db)
        assert res_backward["root_value_ref_id"] == "vref_2"
        assert len(res_backward["nodes"]) == 2
        assert len(res_backward["edges"]) == 1
        assert res_backward["edges"][0]["source_value_ref_id"] == "vref_1"
        assert res_backward["edges"][0]["operation"] == "call:transform"
        assert "Root: y (v1)" in res_backward["ascii_flow"]
        assert "vref_1" in [n["value_ref_id"] for n in res_backward["nodes"]]

        res_forward = query_data_lineage(value_ref_id="vref_1", direction="FORWARD", db_path=temp_db)
        assert res_forward["root_value_ref_id"] == "vref_1"
        assert len(res_forward["nodes"]) == 2
        assert len(res_forward["edges"]) == 1
        assert res_forward["edges"][0]["target_value_ref_id"] == "vref_2"
        assert "Root: x (v1)" in res_forward["ascii_flow"]
    finally:
        queue.stop()


def test_stop_session_storage(temp_db):
    conn = get_connection(temp_db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (session_id, entry_file, entry_function, state) VALUES ('s_active', 'f.py', 'fn', 'ARMED')"
        )
    conn.close()

    res = stop_session("s_active", db_path=temp_db)
    assert res["state"] == "STOPPED"
    assert "stopped_at_mono_ns" in res

    # Calling again on already inactive session
    res_again = stop_session("s_active", db_path=temp_db)
    assert res_again["state"] == "STOPPED"
    assert "already inactive" in res_again.get("message", "")
