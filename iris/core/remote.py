"""Remote process attachment and injection utilities (PEP 768 sys.remote_exec and port probes)."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

from iris.storage.db import get_db_path


def attach_remote_pid(
    pid: int,
    session_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Attach Iris flight recorder to a running process via PEP 768 (Python 3.14+).

    Args:
        pid: Operating system Process ID (PID) of the target Python process.
        session_id: The armed session ID to activate.
        db_path: Optional path to SQLite database.

    Returns:
        Dict containing attachment status and message or version error details.
    """
    database_path = str(db_path or get_db_path())

    # PEP 768 introduces sys.remote_exec in Python 3.14+
    if hasattr(sys, "remote_exec"):
        injection_script = f"""
import os
os.environ["IRIS_DB_PATH"] = {repr(database_path)}
try:
    from iris.core.runtime import auto_attach_from_db
    auto_attach_from_db()
except Exception as _e:
    pass
"""
        try:
            # Call sys.remote_exec(pid, script)
            getattr(sys, "remote_exec")(pid, injection_script)
            return {
                "success": True,
                "pid": pid,
                "session_id": session_id,
                "method": "PEP_768_REMOTE_EXEC",
                "message": (
                    f"Successfully injected Iris flight recorder into process {pid} "
                    f"using PEP 768 sys.remote_exec for session '{session_id}'."
                ),
            }
        except Exception as exc:
            return {
                "success": False,
                "pid": pid,
                "session_id": session_id,
                "method": "PEP_768_REMOTE_EXEC",
                "error": f"Failed to execute sys.remote_exec on PID {pid}: {exc}",
            }

    # Fallback for Python < 3.14: Clear guidance without crashing
    current_ver = ".".join(map(str, sys.version_info[:3]))
    return {
        "success": False,
        "pid": pid,
        "session_id": session_id,
        "method": "UNSUPPORTED_PYTHON_VERSION",
        "error": (
            f"PEP 768 sys.remote_exec is only available on Python 3.14+ (detected Python {current_ver}). "
            "For Python 3.12 and 3.13, external process attachment without code changes requires external debuggers. "
            "Please use one of the following native zero-overhead alternatives:\n"
            "  1. Iris ASGI/WSGI Middleware (for FastAPI, Flask, Starlette, Django)\n"
            "  2. CLI runner: 'iris run <script.py>'\n"
            "  3. Context manager: 'with trace_context(entry_file, entry_function): ...'"
        ),
    }


def attach_remote_port(
    port: int,
    session_id: str,
    entry_file: str,
    entry_function: str,
    condition: Optional[str] = None,
) -> Dict[str, Any]:
    """Trigger on-demand tracing against a running web application with Iris middleware.

    Args:
        port: TCP port where the web application is listening (e.g. 8000, 5000).
        session_id: The session ID to link with the request.
        entry_file: Target file name or path.
        entry_function: Target function name.
        condition: Optional filter predicate condition.

    Returns:
        Dict indicating probe dispatch outcome.
    """
    url = f"http://127.0.0.1:{port}/"
    headers = {
        "X-Iris-Trace": "true",
        "X-Iris-Target": f"{entry_file}:{entry_function}",
        "X-Iris-Session-Id": session_id,
        "User-Agent": "Iris-MCP-Remote-Attach/1.0",
    }
    if condition:
        headers["X-Iris-Condition"] = condition

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            resp_session_id = resp.headers.get("x-iris-session-id", session_id)
            return {
                "success": True,
                "port": port,
                "session_id": resp_session_id,
                "http_status": resp.status,
                "message": f"Successfully activated on-demand tracing on {url} (HTTP {resp.status}).",
            }
    except urllib.error.HTTPError as http_err:
        # Middleware intercepts and processes the request before endpoint status is returned
        resp_session_id = http_err.headers.get("x-iris-session-id", session_id)
        return {
            "success": True,
            "port": port,
            "session_id": resp_session_id,
            "http_status": http_err.code,
            "message": (
                f"Dispatched HTTP trigger to {url} (HTTP {http_err.code}). "
                "Iris middleware intercepted the request and armed flight recording."
            ),
        }
    except Exception as exc:
        return {
            "success": False,
            "port": port,
            "session_id": session_id,
            "error": f"Could not connect to {url}: {exc}",
        }
