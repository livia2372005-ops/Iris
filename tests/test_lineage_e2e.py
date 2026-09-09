"""End-to-end integration test for variable-level Data Lineage Graph and causality."""

from pathlib import Path
import tempfile
import pytest

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator
from iris.core.observer import IrisObserver
from iris.storage.db import arm_session, get_connection, init_db, query_data_lineage

GLOBAL_OFFSET = 5


def sample_data_pipeline(raw_num):
    doubled = raw_num * 2
    shifted = doubled + GLOBAL_OFFSET
    return shifted


def test_e2e_data_lineage_graph():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "lineage_e2e.db"
        init_db(db_path)

        session_id = "sess_lineage_e2e"
        arm_session(session_id, "test_lineage_e2e.py", "sample_data_pipeline", db_path=db_path)

        queue = EventQueue(db_path=db_path, flush_interval=0.01)
        coord = SessionCoordinator(
            session_id=session_id,
            entry_file="test_lineage_e2e.py",
            entry_function="sample_data_pipeline",
        )
        observer = IrisObserver(coordinator=coord, event_queue=queue)
        observer.attach()

        try:
            res = sample_data_pipeline(10)
            assert res == 25
        finally:
            observer.detach()
            queue.flush()
            queue.stop()

        # Query database directly for value_refs
        conn = get_connection(db_path)
        try:
            vrefs = conn.execute(
                """
                SELECT value_ref_id, variable_name, version, line_number, value_snapshot, epistemic_status
                FROM value_refs
                ORDER BY version ASC
                """
            ).fetchall()
            assert len(vrefs) >= 3

            vref_map = {r["variable_name"]: dict(r) for r in vrefs}
            assert "raw_num" in vref_map
            assert "doubled" in vref_map
            assert "shifted" in vref_map

            assert vref_map["raw_num"]["epistemic_status"] == "Observed"
            assert vref_map["doubled"]["epistemic_status"] == "Observed"
            assert vref_map["shifted"]["epistemic_status"] == "Observed"

            # Check if GLOBAL_OFFSET static constant was captured
            if "GLOBAL_OFFSET" in vref_map:
                assert vref_map["GLOBAL_OFFSET"]["epistemic_status"] == "Static"

            # Query backward lineage starting from shifted
            shifted_id = vref_map["shifted"]["value_ref_id"]
            lineage_res = query_data_lineage(shifted_id, direction="BACKWARD", depth_limit=5, db_path=db_path)

            assert lineage_res["root_value_ref_id"] == shifted_id
            assert lineage_res["direction"] == "BACKWARD"
            assert len(lineage_res["edges"]) >= 1

            # Verify ASCII flow diagram is generated
            ascii_flow = lineage_res["ascii_flow"]
            assert "Root: shifted" in ascii_flow
            assert "doubled" in ascii_flow

            # Query forward lineage starting from raw_num
            raw_id = vref_map["raw_num"]["value_ref_id"]
            forward_res = query_data_lineage(raw_id, direction="FORWARD", depth_limit=5, db_path=db_path)
            assert forward_res["root_value_ref_id"] == raw_id
            assert len(forward_res["edges"]) >= 1
            assert "Root: raw_num" in forward_res["ascii_flow"]
            assert "doubled" in forward_res["ascii_flow"]
        finally:
            conn.close()
