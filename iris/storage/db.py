"""SQLite WAL storage manager for Iris."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from iris.storage.resolver import SourceResolver

DEFAULT_DB_FILENAME = "iris_trace.db"


def get_db_path() -> Path:
    """Resolve the path to the SQLite database file, traversing upwards to find workspace root."""
    custom_path = os.getenv("IRIS_DB_PATH")
    if custom_path:
        return Path(custom_path).resolve()

    # Search upwards from CWD to find workspace root containing .agents, .git, or pyproject.toml
    curr = Path.cwd().resolve()
    for directory in [curr] + list(curr.parents):
        if (directory / ".agents").exists() or (directory / ".git").exists() or (directory / "pyproject.toml").exists():
            return directory / DEFAULT_DB_FILENAME

    return curr / DEFAULT_DB_FILENAME


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create a thread-safe connection to SQLite in WAL mode."""
    target_path = db_path or get_db_path()
    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize database tables and indexes if they do not exist."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    entry_file TEXT NOT NULL,
                    entry_function TEXT NOT NULL,
                    condition TEXT,
                    state TEXT NOT NULL,
                    created_at_mono_ns INTEGER,
                    timeout_seconds INTEGER DEFAULT 60,
                    start_time_mono_ns INTEGER,
                    end_time_mono_ns INTEGER,
                    total_events INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    parent_execution_id TEXT,
                    function_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_time_mono_ns INTEGER NOT NULL,
                    end_time_mono_ns INTEGER,
                    async_task_id TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    branch_taken INTEGER,
                    payload_json TEXT,
                    FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
                );

                CREATE INDEX IF NOT EXISTS idx_executions_session_parent
                    ON executions(session_id, parent_execution_id);

                CREATE INDEX IF NOT EXISTS idx_events_exec_seq
                    ON events(execution_id, sequence_number);
                """
            )
            # Ensure columns exist if table was created earlier without them
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN created_at_mono_ns INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN timeout_seconds INTEGER DEFAULT 60")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN condition TEXT")
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()


def arm_session(
    session_id: str,
    entry_file: str,
    entry_function: str,
    condition: Optional[str] = None,
    timeout_seconds: int = 60,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Store an ARMED session record in SQLite with condition, creation time, and timeout."""
    conn = get_connection(db_path)
    import time
    now_mono = time.monotonic_ns()
    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    session_id, entry_file, entry_function, condition, state,
                    created_at_mono_ns, timeout_seconds, total_events
                ) VALUES (?, ?, ?, ?, 'ARMED', ?, ?, 0)
                """,
                (session_id, entry_file, entry_function, condition, now_mono, timeout_seconds),
            )
        return {
            "session_id": session_id,
            "entry_file": entry_file,
            "entry_function": entry_function,
            "condition": condition,
            "state": "ARMED",
            "timeout_seconds": timeout_seconds,
        }
    finally:
        conn.close()


def get_session(
    session_id: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve session details by ID, checking for automatic timeout expiry."""
    conn = get_connection(db_path)
    import time
    try:
        cursor = conn.execute(
            """
            SELECT session_id, entry_file, entry_function, condition, state,
                   created_at_mono_ns, timeout_seconds,
                   start_time_mono_ns, end_time_mono_ns, total_events
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        sess = dict(row)

        # Auto-timeout verification for ARMED sessions
        if sess["state"] == "ARMED" and sess.get("created_at_mono_ns") and sess.get("timeout_seconds"):
            elapsed_sec = (time.monotonic_ns() - sess["created_at_mono_ns"]) / 1e9
            if elapsed_sec > sess["timeout_seconds"]:
                with conn:
                    conn.execute(
                        "UPDATE sessions SET state = 'ABORTED_TIMEOUT' WHERE session_id = ?",
                        (session_id,),
                    )
                sess["state"] = "ABORTED_TIMEOUT"

        return sess
    finally:
        conn.close()


def clean_db(
    keep_latest: int = 5,
    all_sessions: bool = False,
    db_path: Optional[Path] = None,
) -> int:
    """Delete old sessions and cascades executions/events to keep DB clean."""
    conn = get_connection(db_path)
    try:
        with conn:
            if all_sessions:
                conn.execute("DELETE FROM events")
                conn.execute("DELETE FROM executions")
                cursor = conn.execute("DELETE FROM sessions")
                deleted = cursor.rowcount
            else:
                # Find sessions outside the latest N
                cursor = conn.execute(
                    """
                    SELECT session_id FROM sessions
                    ORDER BY rowid DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (keep_latest,),
                )
                old_ids = [r[0] for r in cursor.fetchall()]
                deleted = len(old_ids)
                if old_ids:
                    placeholders = ",".join("?" * len(old_ids))
                    conn.execute(
                        f"""
                        DELETE FROM events WHERE execution_id IN (
                            SELECT execution_id FROM executions WHERE session_id IN ({placeholders})
                        )
                        """,
                        old_ids,
                    )
                    conn.execute(
                        f"DELETE FROM executions WHERE session_id IN ({placeholders})",
                        old_ids,
                    )
                    conn.execute(
                        f"DELETE FROM sessions WHERE session_id IN ({placeholders})",
                        old_ids,
                    )
        conn.execute("VACUUM")
        return deleted
    finally:
        conn.close()


def get_latest_armed_session(
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve the latest pending session that is in ARMED state."""
    all_armed = get_all_armed_sessions(db_path)
    return all_armed[-1] if all_armed else None


def get_all_armed_sessions(
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all pending sessions that are in ARMED state."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT session_id, entry_file, entry_function, condition, state
            FROM sessions
            WHERE state = 'ARMED'
            ORDER BY rowid ASC
            """
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_call_tree(
    session_id: str,
    depth_limit: int = 2,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Recursively reconstruct the call tree up to depth_limit."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT execution_id, parent_execution_id, function_name,
                   file_path, start_time_mono_ns, end_time_mono_ns, async_task_id
            FROM executions
            WHERE session_id = ?
            ORDER BY start_time_mono_ns ASC
            """,
            (session_id,),
        )
        all_execs = [dict(r) for r in cursor.fetchall()]

        by_parent: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for ex in all_execs:
            p_id = ex["parent_execution_id"]
            by_parent.setdefault(p_id, []).append(ex)

        def build_nodes(parent_id: Optional[str], current_depth: int) -> List[Dict[str, Any]]:
            if current_depth > depth_limit:
                return []
            nodes = []
            for child in by_parent.get(parent_id, []):
                node = dict(child)
                if current_depth < depth_limit:
                    node["children"] = build_nodes(child["execution_id"], current_depth + 1)
                else:
                    node["children"] = []
                nodes.append(node)
            return nodes

        return build_nodes(None, 1)
    finally:
        conn.close()


def inspect_execution_flow(
    execution_id: str,
    limit: int = 50,
    cursor: int = 0,
    include_source: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve ordered execution events with source line context."""
    conn = get_connection(db_path)
    try:
        ex_row = conn.execute(
            """
            SELECT execution_id, session_id, function_name, file_path
            FROM executions
            WHERE execution_id = ?
            """,
            (execution_id,),
        ).fetchone()

        if not ex_row:
            return {
                "error": f"Execution {execution_id} not found",
                "events": [],
                "has_more": False,
                "next_cursor": None,
            }

        ex_meta = dict(ex_row)
        file_path = ex_meta.get("file_path", "")

        query = """
            SELECT event_id, execution_id, sequence_number, event_type,
                   line_number, branch_taken, payload_json
            FROM events
            WHERE execution_id = ? AND sequence_number >= ?
            ORDER BY sequence_number ASC
            LIMIT ?
        """
        rows = conn.execute(query, (execution_id, cursor, limit + 1)).fetchall()

        events: List[Dict[str, Any]] = []
        for r in rows[:limit]:
            ev = dict(r)
            if ev.get("payload_json"):
                try:
                    ev["payload"] = json.loads(ev["payload_json"])
                except Exception:
                    ev["payload"] = ev["payload_json"]
            else:
                ev["payload"] = None

            if include_source and file_path and ev.get("line_number"):
                ev["source_line"] = SourceResolver.resolve_line(file_path, ev["line_number"])

            events.append(ev)

        has_more = len(rows) > limit
        next_cursor = rows[limit]["sequence_number"] if has_more else None

        return {
            "execution": ex_meta,
            "events": events,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }
    finally:
        conn.close()


def diagnose_session(
    session_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Perform smart trace diagnosis, loop collapsing, and root cause analysis."""
    from iris.analysis.diagnoser import TraceDiagnoser
    diagnoser = TraceDiagnoser(session_id=session_id, db_path=db_path)
    return diagnoser.diagnose()
