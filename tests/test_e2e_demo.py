"""End-to-End realistic debugging scenario via Iris MCP tools."""

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict


def test_e2e_investigation():
    print("=================================================================")
    print("STEP 1: AI Agent Arms the Target Entry Point (routes.py::handle_chat)")
    print("=================================================================")

    # Start MCP server subprocess
    proc = subprocess.Popen(
        [sys.executable, "-m", "iris.mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    def rpc_call(method: str, params: Dict[str, Any], req_id: int) -> Dict[str, Any]:
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.flush()

        while True:
            out_line = proc.stdout.readline().decode("utf-8").strip()
            if not out_line:
                continue
            res = json.loads(out_line)
            if res.get("id") == req_id:
                return res

    try:
        # Initialize
        rpc_call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "antigravity-agent", "version": "1.0"},
            },
            1,
        )

        # Call iris_arm_entry
        arm_res = rpc_call(
            "tools/call",
            {
                "name": "iris_arm_entry",
                "arguments": {
                    "entry_file": "routes.py",
                    "entry_function": "handle_chat",
                    "timeout_seconds": 60,
                },
            },
            2,
        )
        arm_content = json.loads(arm_res["result"]["content"][0]["text"])
        session_id = arm_content["session_id"]
        print(f"[Agent Tool Output] Session Armed: {session_id}")
        print(f"  Target: {arm_content['entry_function']} in {arm_content['entry_file']}")
        print(f"  Status: {arm_content['state']}")

        print("\n=================================================================")
        print("STEP 2: Triggering Application (examples/chat_service/run_test.py)")
        print("=================================================================")

        import os
        repo_root = str(Path(__file__).resolve().parent.parent)
        sub_env = dict(os.environ)
        sub_env["PYTHONPATH"] = repo_root

        app_run = subprocess.run(
            [sys.executable, "examples/chat_service/run_test.py"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=sub_env,
        )
        print(f"[Application Output]:\n{app_run.stdout}")
        if app_run.stderr:
            print(f"[Application Stderr]:\n{app_run.stderr}")

        print("=================================================================")
        print("STEP 3: Agent Checks Session Status via iris_get_session_status")
        print("=================================================================")

        status_res = rpc_call(
            "tools/call",
            {
                "name": "iris_get_session_status",
                "arguments": {"session_id": session_id},
            },
            3,
        )
        status_data = json.loads(status_res["result"]["content"][0]["text"])
        print(f"[Agent Tool Output] Status: {status_data['state']}")
        print(f"  Total Events Captured: {status_data['total_events']}")
        assert status_data["state"] == "COMPLETED"
        assert status_data["total_events"] > 0

        print("\n=================================================================")
        print("STEP 4: Agent Queries Call Tree via iris_query_call_tree")
        print("=================================================================")

        tree_res = rpc_call(
            "tools/call",
            {
                "name": "iris_query_call_tree",
                "arguments": {"session_id": session_id, "depth_limit": 3},
            },
            4,
        )
        tree_data = json.loads(tree_res["result"]["content"][0]["text"])
        call_tree = tree_data["call_tree"]
        print(f"[Agent Tool Output] Hierarchical Call Tree:")

        def print_tree(nodes, indent=0):
            for n in nodes:
                print(f"{'  ' * indent}- {n['function_name']}() [id={n['execution_id']}] ({n['file_path']})")
                print_tree(n.get("children", []), indent + 1)

        print_tree(call_tree)

        root = call_tree[0]
        child_map = {c["function_name"]: c for c in root["children"]}
        assert "validate_input" in child_map
        assert "strip_whitespace" in child_map
        assert "search_knowledge_base" in child_map

        print("\n=================================================================")
        print("STEP 5: Agent Inspects Execution Flow of handle_chat and strip_whitespace")
        print("=================================================================")

        # 5a. Inspect handle_chat (verify secret token is sanitized)
        root_flow_res = rpc_call(
            "tools/call",
            {
                "name": "iris_inspect_execution_flow",
                "arguments": {"execution_id": root["execution_id"]},
            },
            5,
        )
        root_flow = root_flow_res["result"]
        # FastMCP / MCPServer might return structured dict or json text
        if "content" in root_flow:
            root_flow_data = json.loads(root_flow["content"][0]["text"])
        else:
            root_flow_data = root_flow

        print(f"--- Flow of handle_chat ---")
        for ev in root_flow_data["events"]:
            if ev["event_type"] == "LINE":
                print(f"  Line {ev['line_number']}: {ev.get('source_line')} | Locals: {ev.get('payload', {}).get('locals')}")
                locals_dict = ev.get("payload", {}).get("locals", {})
                if "auth_token" in locals_dict:
                    assert "[REDACTED_SECRET" in locals_dict["auth_token"], "Secret auth_token was not sanitized!"

        # 5b. Inspect strip_whitespace
        strip_exec_id = child_map["strip_whitespace"]["execution_id"]
        strip_flow_res = rpc_call(
            "tools/call",
            {
                "name": "iris_inspect_execution_flow",
                "arguments": {"execution_id": strip_exec_id},
            },
            6,
        )
        strip_flow = strip_flow_res["result"]
        if "content" in strip_flow:
            strip_flow_data = json.loads(strip_flow["content"][0]["text"])
        else:
            strip_flow_data = strip_flow

        print(f"\n--- Flow of strip_whitespace ---")
        for ev in strip_flow_data["events"]:
            print(f"  Line {ev['line_number']} [{ev['event_type']}]: {ev.get('source_line')} | Payload: {ev.get('payload')}")
            if ev["event_type"] == "RETURN":
                ret_val = ev.get("payload", {}).get("return_value")
                print(f"\n>>> RETURN VALUE OF strip_whitespace: '{ret_val}'")
                assert ret_val == "", "Expected strip_whitespace return value to be empty string due to regex bug!"

        print("\n=================================================================")
        print("STEP 6: Agent Root-Cause Conclusion:")
        print("=================================================================")
        print("Empirical Finding:")
        print("1. 'handle_chat' received query: 'How do I use Antigravity with Iris?'")
        print("2. 'strip_whitespace' executed line: 'cleaned = re.sub(r\"[a-zA-Z0-9\\s]+\", \"\", query)'")
        print("3. As a result, 'cleaned' became '' (empty string) and strip_whitespace returned ''.")
        print("4. Consequently, 'search_knowledge_base' received '' and returned empty response ''.")
        print("5. Root Cause: The regex in strip_whitespace accidentally matched and stripped all alphanumeric characters.")
        print("=================================================================")
        print("=== E2E REALISTIC DEBUGGING SCENARIO SUCCEEDED 100%! ===")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    test_e2e_investigation()
