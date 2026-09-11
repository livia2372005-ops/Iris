"""Tests for Feature 13.4: PEP 768 Attach-Mode and Remote Injection."""

import http.server
import socketserver
import sys
import threading
from unittest.mock import patch

from iris.core.remote import attach_remote_pid, attach_remote_port
from iris.mcp.server import create_server
from iris.storage.db import arm_session, clean_db, get_session, init_db


def test_arm_session_with_target_pid_and_port():
    """Verify arm_session and get_session persist target_pid and target_port."""
    clean_db()
    init_db()

    sid = "remote_test_sess_1"
    record = arm_session(
        session_id=sid,
        entry_file="server.py",
        entry_function="handle_request",
        condition="req_id > 100",
        timeout_seconds=45,
        target_pid=12345,
        target_port=8080,
    )

    assert record["session_id"] == sid
    assert record["target_pid"] == 12345
    assert record["target_port"] == 8080

    sess = get_session(sid)
    assert sess is not None
    assert sess["session_id"] == sid
    assert sess["target_pid"] == 12345
    assert sess["target_port"] == 8080


def test_attach_remote_pid_fallback_on_py_pre314():
    """Verify attach_remote_pid provides clear guidance when sys.remote_exec is absent."""
    had_remote = hasattr(sys, "remote_exec")
    old_remote = getattr(sys, "remote_exec", None)
    try:
        if had_remote:
            delattr(sys, "remote_exec")

        res = attach_remote_pid(pid=99999, session_id="test_fallback_sess")
        assert res["success"] is False
        assert res["method"] == "UNSUPPORTED_PYTHON_VERSION"
        assert "PEP 768 sys.remote_exec is only available on Python 3.14+" in res["error"]
        assert "Iris ASGI/WSGI Middleware" in res["error"]
    finally:
        if had_remote:
            setattr(sys, "remote_exec", old_remote)


def test_attach_remote_pid_with_mock_py314():
    """Simulate Python 3.14+ PEP 768 sys.remote_exec behavior."""
    calls = []

    def mock_remote_exec(pid: int, script: str):
        calls.append((pid, script))

    with patch.object(sys, "remote_exec", mock_remote_exec, create=True):
        res = attach_remote_pid(pid=88888, session_id="test_py314_sess")
        assert res["success"] is True
        assert res["method"] == "PEP_768_REMOTE_EXEC"
        assert "Successfully injected Iris flight recorder" in res["message"]
        assert len(calls) == 1
        assert calls[0][0] == 88888
        assert "auto_attach_from_db" in calls[0][1]


def test_attach_remote_port_live_mock_server():
    """Verify attach_remote_port successfully probes an HTTP server."""
    class MockHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # Echo back X-Iris-Session-Id in response
            incoming_sid = self.headers.get("X-Iris-Session-Id", "unknown")
            self.send_response(200)
            self.send_header("x-iris-session-id", incoming_sid)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]

    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    try:
        res = attach_remote_port(
            port=port,
            session_id="port_test_sess",
            entry_file="api.py",
            entry_function="get_users",
            condition="active == True",
        )
        assert res["success"] is True
        assert res["port"] == port
        assert res["http_status"] == 200
        assert res["session_id"] == "port_test_sess"
    finally:
        server.server_close()


def test_attach_remote_port_unreachable_fails_gracefully():
    """Verify attach_remote_port handles unreachable ports gracefully without raising exceptions."""
    res = attach_remote_port(
        port=59999,  # Unused port
        session_id="unreachable_sess",
        entry_file="api.py",
        entry_function="dummy",
    )
    assert res["success"] is False
    assert "Could not connect to" in res["error"]


def test_mcp_arm_entry_with_target_pid_and_port():
    """Verify iris_arm_entry MCP tool returns target_pid, target_port and remote attachment details."""
    import asyncio
    clean_db()
    init_db()

    server = create_server()
    call_res = asyncio.run(
        server.call_tool(
            "iris_arm_entry",
            {
                "entry_file": "worker.py",
                "entry_function": "process_task",
                "target_pid": 77777,
            },
        )
    )
    res_pid = call_res.structured_content["result"]

    assert res_pid["state"] == "ARMED"
    assert res_pid["target_pid"] == 77777
    assert "remote_attachment" in res_pid
    assert "targeted PID: 77777" in res_pid["message"]
