"""Real-world demonstration: Tracing the official Flaskr tutorial repository with Iris."""

import json
import subprocess
import sys
from pathlib import Path

from iris.storage.db import (
    arm_session,
    get_session,
    inspect_execution_flow,
    query_call_tree,
)


def test_trace_realworld_flask():
    print("\n" + "=" * 70)
    print("STEP 1: Arming 'register' in cloned Flask tutorial (flaskr/auth.py)")
    print("=" * 70)

    sid = "flask_demo_register"
    armed = arm_session(
        session_id=sid,
        entry_file="auth.py",
        entry_function="register",
        timeout_seconds=60,
    )
    print(f"Session armed: {armed['session_id']}")
    print(f"Target: {armed['entry_function']} in {armed['entry_file']}")

    print("\n" + "=" * 70)
    print("STEP 2: Running Flask Test Suite via Pytest (without modifying any Flask code)")
    print("=" * 70)

    repo_root = Path(__file__).resolve().parent.parent
    cloned_flask_dir = repo_root / "examples" / "cloned_flask"
    tutorial_dir = cloned_flask_dir / "examples" / "tutorial"
    if not tutorial_dir.exists():
        print("Cloning official Pallets/Flask repository for demonstration...")
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/pallets/flask.git", str(cloned_flask_dir)],
            check=True,
        )

    try:
        import flask
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "flask"], check=True)

    import os
    env = dict(os.environ)
    from iris.storage.db import get_db_path
    env["IRIS_DB_PATH"] = str(get_db_path())

    res = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_auth.py::test_register", "-s"],
        cwd=str(tutorial_dir),
        capture_output=True,
        text=True,
        env=env,
    )
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
    assert res.returncode == 0, f"Pytest failed: {res.stderr}"

    print("\n" + "=" * 70)
    print("STEP 3: Querying Session Status via Iris")
    print("=" * 70)

    session = get_session(sid)
    print(f"Session status: {session['state']}")
    print(f"Total trace events captured: {session['total_events']}")
    assert session["state"] in ("COMPLETED", "COMPLETED_TRUNCATED")
    assert session["total_events"] > 0

    print("\n" + "=" * 70)
    print("STEP 4: Querying Hierarchical Call Tree of Flask's register()")
    print("=" * 70)

    tree = query_call_tree(sid, depth_limit=3)

    def print_node(node, indent=0):
        print(f"{'  ' * indent}- {node['function_name']}() [{node['execution_id']}] ({Path(node['file_path']).name})")
        for child in node.get("children", []):
            print_node(child, indent + 1)

    for root in tree:
        print_node(root)

    assert len(tree) >= 1
    root_exec = tree[0]
    assert root_exec["function_name"] == "register"

    print("\n" + "=" * 70)
    print("STEP 5: Inspecting Execution Flow & Variable Mutations in register()")
    print("=" * 70)

    flow = inspect_execution_flow(root_exec["execution_id"], limit=20)
    for ev in flow["events"]:
        if ev["event_type"] == "LINE":
            delta_str = json.dumps(ev.get("payload", {}).get("delta", {}))
            print(f"  Line {ev['line_number']:2d}: {ev.get('source_line', '').strip()}  | Delta: {delta_str}")
        elif ev["event_type"] == "RETURN":
            print(f"  RETURN: {ev.get('payload', {}).get('return_value')}")

    print("\n" + "=" * 70)
    print("SUCCESS: Iris successfully traced real-world Flask application!")
    print("=" * 70)


if __name__ == "__main__":
    test_trace_realworld_flask()
