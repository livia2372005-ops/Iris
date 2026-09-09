"""Iris Model Context Protocol (MCP) stdio server."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from iris.storage.db import (
    arm_session,
    diagnose_session,
    get_db_path,
    get_session,
    init_db,
    inspect_execution_flow as db_inspect_execution_flow,
    query_call_tree as db_query_call_tree,
)


def create_server() -> MCPServer:
    """Create and configure the Iris MCP server instance."""
    server = MCPServer(
        name="iris-flight-recorder",
        version="0.1.0",
        description="Iris: Flight recorder for CPython code via PEP 669 & SQLite",
    )

    @server.tool(
        name="iris_arm_entry",
        description=(
            "Arm an entry point (file and function) for observation. "
            "Supports optional predicate condition evaluated at function entry (PY_START). "
            "When the target application or test reaches this entry point, "
            "Iris will record the execution tree without pausing execution."
        ),
    )
    def iris_arm_entry(
        entry_file: str,
        entry_function: str,
        condition: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Arm an entry point for tracing.

        Args:
            entry_file: Path or filename of the target entry script (e.g. 'app/routes.py').
            entry_function: Name of the target entry function (e.g. 'handle_chat').
            condition: Optional Python expression evaluated against function arguments (e.g. 'user_id == 42').
            timeout_seconds: Maximum duration in seconds to wait for entry trigger.

        Returns:
            Dict containing session_id, state ('ARMED'), and target details.
        """
        init_db()
        session_id = f"iris_{uuid.uuid4().hex[:12]}"
        record = arm_session(
            session_id=session_id,
            entry_file=entry_file,
            entry_function=entry_function,
            condition=condition,
            timeout_seconds=timeout_seconds,
        )
        return {
            "session_id": session_id,
            "entry_file": entry_file,
            "entry_function": entry_function,
            "condition": condition,
            "state": "ARMED",
            "timeout_seconds": timeout_seconds,
            "database_path": str(get_db_path()),
            "message": (
                f"Session '{session_id}' is now ARMED for {entry_function} in {entry_file}"
                + (f" with condition [{condition}]." if condition else ".")
                + " Trigger your application or test suite now."
            ),
        }

    @server.tool(
        name="iris_get_session_status",
        description=(
            "Check the status of an observation session (ARMED, TRACING, COMPLETED, "
            "ABORTED, or COMPLETED_TRUNCATED) along with event counters."
        ),
    )
    def iris_get_session_status(session_id: str) -> Dict[str, Any]:
        """Get the current execution and capture status of a session.

        Args:
            session_id: The ID of the session returned by iris_arm_entry.

        Returns:
            Dict containing the current session state and summary statistics.
        """
        session = get_session(session_id)
        if not session:
            return {
                "error": f"Session '{session_id}' not found",
                "session_id": session_id,
                "state": "UNKNOWN",
            }
        return {
            "session_id": session["session_id"],
            "entry_file": session["entry_file"],
            "entry_function": session["entry_function"],
            "state": session["state"],
            "start_time_mono_ns": session.get("start_time_mono_ns"),
            "end_time_mono_ns": session.get("end_time_mono_ns"),
            "total_events": session.get("total_events", 0),
        }

    @server.tool(
        name="iris_query_call_tree",
        description=(
            "Query the hierarchical call tree of functions invoked during the session. "
            "Use this to pinpoint which sub-function failed or produced unexpected behavior."
        ),
    )
    def iris_query_call_tree(
        session_id: str,
        depth_limit: int = 2,
    ) -> Dict[str, Any]:
        """Query the call tree for a session up to a maximum depth.

        Args:
            session_id: The ID of the session to query.
            depth_limit: Maximum call depth to return (default 2).

        Returns:
            Dict containing the hierarchical tree of function executions.
        """
        session = get_session(session_id)
        if not session:
            return {
                "error": f"Session '{session_id}' not found",
                "session_id": session_id,
                "call_tree": [],
            }

        tree = db_query_call_tree(session_id, depth_limit=depth_limit)
        return {
            "session_id": session_id,
            "depth_limit": depth_limit,
            "state": session["state"],
            "call_tree": tree,
        }

    @server.tool(
        name="iris_inspect_execution_flow",
        description=(
            "Inspect the detailed line-by-line execution flow and variable changes "
            "for a specific function execution ID. Includes pagination."
        ),
    )
    def iris_inspect_execution_flow(
        execution_id: str,
        limit: int = 50,
        cursor: int = 0,
    ) -> Dict[str, Any]:
        """Inspect the detailed execution flow of an execution record.

        Args:
            execution_id: ID of the function execution to inspect.
            limit: Maximum number of events to return per page (default 50).
            cursor: Starting sequence number for pagination (default 0).

        Returns:
            Dict containing execution metadata, ordered line/branch events, and next_cursor.
        """
        return db_inspect_execution_flow(
            execution_id=execution_id,
            limit=limit,
            cursor=cursor,
        )

    @server.tool(
        name="iris_diagnose_anomaly",
        description=(
            "Diagnose anomalies, collapse repetitive loops, detect unexpected variable "
            "type mutations, and locate exception root causes in a recorded session. "
            "Returns a token-optimized markdown summary for rapid AI root-cause reasoning."
        ),
    )
    def iris_diagnose_anomaly(session_id: str) -> Dict[str, Any]:
        """Run intelligent trace diagnostics and loop compression on a session.

        Args:
            session_id: The ID of the session to diagnose.

        Returns:
            Dict containing session diagnostics, compressed loops, and markdown report.
        """
        return diagnose_session(session_id=session_id)

    return server


def main() -> None:
    """Run the Iris MCP stdio server."""
    # Ensure database tables exist at server startup
    init_db()
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
