"""Tests for Feature 13.5: Terminal Boundary Detection in AST and Execution Flow."""

import tempfile
from pathlib import Path
from iris.analysis.ast_lineage import ASTLineageAnalyzer
from iris.core.runtime import trace_context
from iris.storage.db import (
    init_db,
    clean_db,
    inspect_execution_flow,
    query_call_tree,
)


def sample_boundary_code():
    # Simulated dummy functions to represent boundary calls
    class DummyClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return {"choices": [{"message": {"content": "hello"}}]}

    class DummyRequests:
        @staticmethod
        def get(url):
            return {"status": 200}

    class DummyCursor:
        @staticmethod
        def execute(sql):
            return 1

    openai = DummyClient()
    requests = DummyRequests()
    cursor = DummyCursor()

    # Boundary 1: LLM API
    res_llm = openai.chat.completions.create(model="gpt-4o", prompt="test")

    # Boundary 2: HTTP Network
    res_http = requests.get("https://api.example.com/data")

    # Boundary 3: Database IO
    row_count = cursor.execute("SELECT * FROM users")

    return res_llm, res_http, row_count


def test_ast_boundary_analyzer_static():
    """Verify ASTLineageAnalyzer statically classifies external terminal boundary calls."""
    sample_source = """
import openai
import requests
import sqlite3
import subprocess

def run_pipeline():
    # LLM Call
    ans = openai.chat.completions.create(model="gpt-4o", messages=[])
    
    # HTTP Call
    resp = requests.post("https://api.github.com", json={})
    
    # DB Call
    cur.execute("UPDATE telemetry SET val = 1")
    
    # Subprocess Call
    res = subprocess.run(["git", "status"], capture_output=True)
    
    # Normal computation
    final_score = 42 * 2
    return final_score
"""
    ASTLineageAnalyzer.clear_cache()
    lines_info = ASTLineageAnalyzer.analyze_source(sample_source, file_path="dummy_pipeline.py")

    # Line 8: openai call
    llm_line = lines_info.get(9)
    assert llm_line is not None
    assert llm_line.boundary_type == "LLM_API"
    assert "openai" in llm_line.boundary_target
    assert "Terminal boundary reached" in llm_line.boundary_notice

    # Line 12: requests call
    http_line = lines_info.get(12)
    assert http_line is not None
    assert http_line.boundary_type == "HTTP_NETWORK"
    assert "requests.post" in http_line.boundary_target

    # Line 15: cur.execute
    db_line = lines_info.get(15)
    assert db_line is not None
    assert db_line.boundary_type == "DATABASE_IO"
    assert "cur.execute" in db_line.boundary_target

    # Line 18: subprocess.run
    proc_line = lines_info.get(18)
    assert proc_line is not None
    assert proc_line.boundary_type == "EXTERNAL_PROCESS"
    assert "subprocess.run" in proc_line.boundary_target

    # Line 21: normal computation
    calc_line = lines_info.get(21)
    assert calc_line is not None
    assert calc_line.boundary_type is None


def test_inspect_execution_flow_boundary_enrichment():
    """Verify inspect_execution_flow includes boundary_type when observing a function with boundaries."""
    clean_db()
    init_db()

    current_file = Path(__file__).name
    with trace_context(entry_file=current_file, entry_function="sample_boundary_code") as sid:
        sample_boundary_code()

    # Query call tree
    tree = query_call_tree(sid, depth_limit=1)
    assert len(tree) >= 1
    root_exec_id = tree[0]["execution_id"]

    # Inspect flow
    flow = inspect_execution_flow(root_exec_id)
    assert "boundaries_detected" in flow
    boundaries = flow["boundaries_detected"]
    assert len(boundaries) >= 3

    types = [b["boundary_type"] for b in boundaries]
    assert "LLM_API" in types
    assert "HTTP_NETWORK" in types
    assert "DATABASE_IO" in types

    # Check that individual events on those lines carry boundary_type
    boundary_events = [ev for ev in flow["events"] if ev.get("boundary_type")]
    assert len(boundary_events) >= 3
