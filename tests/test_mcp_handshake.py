"""Verification test for Iris MCP server JSON-RPC stdio handshake."""

import json
import subprocess
import sys
import time
from typing import Any, Dict


def send_message(proc: subprocess.Popen, msg: Dict[str, Any]) -> None:
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def read_message(proc: subprocess.Popen, timeout: float = 5.0) -> Dict[str, Any]:
    start = time.time()
    while time.time() - start < timeout:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        text = line.decode("utf-8").strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(f"[RAW LOG] {text}")
            continue
    raise TimeoutError("Timed out waiting for JSON-RPC response from MCP server")


def test_mcp_server() -> None:
    print("=== Starting Iris MCP stdio Server Test ===")

    cmd = [sys.executable, "-m", "iris.mcp.server"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    try:
        # 1. Initialize
        print("[1] Sending 'initialize' request...")
        send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            },
        )
        init_res = read_message(proc)
        print(f"    Received init response: {init_res.get('result', {}).get('serverInfo', {})}")
        assert "result" in init_res, f"Init failed: {init_res}"

        # 2. Initialized Notification
        print("[2] Sending 'notifications/initialized'...")
        send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        # 3. Tools List
        print("[3] Sending 'tools/list' request...")
        send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        tools_res = read_message(proc)
        tools = tools_res.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        print(f"    Discovered {len(tools)} tools: {tool_names}")

        expected_tools = [
            "iris_arm_entry",
            "iris_get_session_status",
            "iris_query_call_tree",
            "iris_inspect_execution_flow",
            "iris_diagnose_anomaly",
        ]
        for exp in expected_tools:
            assert exp in tool_names, f"Expected tool '{exp}' missing from tools/list"

        # 4. Call iris_arm_entry
        print("[4] Calling 'iris_arm_entry' tool...")
        send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "iris_arm_entry",
                    "arguments": {
                        "entry_file": "app/services.py",
                        "entry_function": "compute_total",
                        "timeout_seconds": 30,
                    },
                },
            },
        )
        arm_res = read_message(proc)
        print(f"    Arm result: {arm_res.get('result')}")
        content_items = arm_res.get("result", {}).get("content", [])
        assert len(content_items) > 0, "No content returned from iris_arm_entry"

        arm_data = json.loads(content_items[0]["text"]) if "text" in content_items[0] else content_items[0]
        session_id = arm_data.get("session_id")
        assert session_id, "Missing session_id in arm result"
        assert arm_data.get("state") == "ARMED"
        print(f"    Session successfully created with ID: {session_id}")

        # 5. Call iris_get_session_status
        print(f"[5] Calling 'iris_get_session_status' for session: {session_id}...")
        send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "iris_get_session_status",
                    "arguments": {"session_id": session_id},
                },
            },
        )
        status_res = read_message(proc)
        status_items = status_res.get("result", {}).get("content", [])
        status_data = json.loads(status_items[0]["text"]) if "text" in status_items[0] else status_items[0]
        print(f"    Status result: {status_data}")
        assert status_data.get("state") == "ARMED"
        assert status_data.get("entry_function") == "compute_total"

        print("=== ALL MCP STDIO TESTS PASSED SUCCESSFULLY! ===")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    test_mcp_server()
