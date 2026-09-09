"""Unit and integration tests for Iris FSM Coordinator, Observer, and Call Tree."""

import asyncio
import os
from pathlib import Path
import sys

from iris.core.runtime import trace_context
from iris.storage.db import (
    get_session,
    inspect_execution_flow,
    query_call_tree,
)


def sub_helper(x: int) -> int:
    y = x * 2
    return y


def sample_entrypoint(n: int) -> int:
    total = 0
    if n > 0:
        total = sub_helper(n)
    else:
        total = -100
    return total


async def async_child(val: int) -> int:
    await asyncio.sleep(0.01)
    res = val + 10
    return res


async def async_entrypoint(val: int) -> int:
    computed = await async_child(val)
    return computed * 2


def test_sync_observation():
    print("\n--- Testing Sync Tracing with Sub-calls and Branches ---")
    current_file = Path(__file__).name

    with trace_context(entry_file=current_file, entry_function="sample_entrypoint") as sid:
        result = sample_entrypoint(21)
        assert result == 42

    session = get_session(sid)
    print(f"Session: {session}")
    assert session is not None
    assert session["state"] == "COMPLETED"
    assert session["total_events"] > 0

    # Query call tree
    call_tree = query_call_tree(sid, depth_limit=2)
    print(f"Call Tree: {call_tree}")
    assert len(call_tree) == 1
    root_node = call_tree[0]
    assert root_node["function_name"] == "sample_entrypoint"
    assert len(root_node["children"]) == 1
    child_node = root_node["children"][0]
    assert child_node["function_name"] == "sub_helper"

    # Inspect execution flow of root
    root_flow = inspect_execution_flow(root_node["execution_id"])
    events = root_flow["events"]
    print(f"Root Execution Events Count: {len(events)}")
    assert len(events) > 0

    event_types = [e["event_type"] for e in events]
    assert "LINE" in event_types
    assert "BRANCH" in event_types
    assert "RETURN" in event_types

    # Inspect source lines
    for ev in events:
        if ev["event_type"] == "LINE":
            print(f"  Line {ev['line_number']}: {ev.get('source_line')} | Locals: {ev.get('payload', {}).get('locals')}")
            assert ev.get("source_line") is not None


def test_async_observation():
    print("\n--- Testing Async Tracing with ContextVars & asyncio ---")
    current_file = Path(__file__).name

    async def run_async():
        with trace_context(entry_file=current_file, entry_function="async_entrypoint") as sid:
            res = await async_entrypoint(5)
            assert res == 30
            return sid

    sid = asyncio.run(run_async())
    session = get_session(sid)
    assert session is not None
    assert session["state"] == "COMPLETED"

    call_tree = query_call_tree(sid, depth_limit=2)
    print(f"Async Call Tree: {call_tree}")
    assert len(call_tree) == 1
    root_node = call_tree[0]
    assert root_node["function_name"] == "async_entrypoint"
    assert len(root_node["children"]) >= 1
    child_func_names = [c["function_name"] for c in root_node["children"]]
    assert "async_child" in child_func_names


if __name__ == "__main__":
    test_sync_observation()
    test_async_observation()
    print("\n=== ALL OBSERVER AND FSM TESTS PASSED SUCCESSFULLY! ===")
