"""Thread-safe in-memory event queue with background batch writer for SQLite."""

from __future__ import annotations

import json
from pathlib import Path
import queue
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from iris.storage.db import get_connection, get_db_path

_POISON_PILL = object()


class EventQueue:
    """Manages an in-memory queue and a background daemon worker to flush trace events."""

    def __init__(self, db_path: Optional[Path] = None, batch_size: int = 100, flush_interval: float = 0.05):
        self.db_path = db_path or get_db_path()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="iris-event-flusher",
            daemon=True,
        )
        self._worker_thread.start()

    def put_execution_start(
        self,
        execution_id: str,
        session_id: str,
        parent_execution_id: Optional[str],
        function_name: str,
        file_path: str,
        start_time_mono_ns: int,
        async_task_id: Optional[str] = None,
    ) -> None:
        """Enqueue an execution start event."""
        self._queue.put(
            (
                "EXEC_START",
                (
                    execution_id,
                    session_id,
                    parent_execution_id,
                    function_name,
                    file_path,
                    start_time_mono_ns,
                    async_task_id,
                ),
            )
        )

    def put_execution_end(self, execution_id: str, end_time_mono_ns: int) -> None:
        """Enqueue an execution completion event."""
        self._queue.put(("EXEC_END", (end_time_mono_ns, execution_id)))

    def put_event(
        self,
        event_id: str,
        execution_id: str,
        sequence_number: int,
        event_type: str,
        line_number: int,
        branch_taken: Optional[int],
        payload: Optional[Dict[str, Any]],
    ) -> None:
        """Enqueue a trace event (LINE, BRANCH, RETURN, EXCEPTION)."""
        payload_str = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        self._queue.put(
            (
                "EVENT",
                (
                    event_id,
                    execution_id,
                    sequence_number,
                    event_type,
                    line_number,
                    branch_taken,
                    payload_str,
                ),
            )
        )

    def put_session_update(
        self,
        session_id: str,
        state: str,
        start_time_mono_ns: Optional[int] = None,
        end_time_mono_ns: Optional[int] = None,
        total_events: Optional[int] = None,
    ) -> None:
        """Enqueue a session state update."""
        self._queue.put(
            (
                "SESSION_UPDATE",
                (state, start_time_mono_ns, end_time_mono_ns, total_events, session_id),
            )
        )

    def _worker_loop(self) -> None:
        """Background thread loop draining events and committing in batches."""
        conn = get_connection(self.db_path)
        batch: List[Tuple[str, Any]] = []
        last_flush_time = time.time()

        try:
            while True:
                try:
                    # Timeout check for periodic flush
                    item = self._queue.get(timeout=self.flush_interval)
                    if item is _POISON_PILL:
                        if batch:
                            self._flush_batch(conn, batch)
                        break
                    batch.append(item)
                    if len(batch) >= self.batch_size:
                        self._flush_batch(conn, batch)
                        batch.clear()
                        last_flush_time = time.time()
                except queue.Empty:
                    if batch and (time.time() - last_flush_time >= self.flush_interval):
                        self._flush_batch(conn, batch)
                        batch.clear()
                        last_flush_time = time.time()
        finally:
            if batch:
                try:
                    self._flush_batch(conn, batch)
                except Exception:
                    pass
            conn.close()

    def _flush_batch(self, conn: sqlite3.Connection, batch: List[Tuple[str, Any]]) -> None:
        """Commit a batch of heterogeneous items in a single transaction."""
        exec_starts = []
        exec_ends = []
        events = []
        session_updates = []

        for item_type, data in batch:
            if item_type == "EXEC_START":
                exec_starts.append(data)
            elif item_type == "EXEC_END":
                exec_ends.append(data)
            elif item_type == "EVENT":
                events.append(data)
            elif item_type == "SESSION_UPDATE":
                session_updates.append(data)

        with conn:
            if exec_starts:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO executions (
                        execution_id, session_id, parent_execution_id,
                        function_name, file_path, start_time_mono_ns, async_task_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    exec_starts,
                )
            if exec_ends:
                conn.executemany(
                    """
                    UPDATE executions
                    SET end_time_mono_ns = ?
                    WHERE execution_id = ?
                    """,
                    exec_ends,
                )
            if events:
                conn.executemany(
                    """
                    INSERT INTO events (
                        event_id, execution_id, sequence_number,
                        event_type, line_number, branch_taken, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    events,
                )
            if session_updates:
                for state, start_ns, end_ns, tot_ev, sid in session_updates:
                    conn.execute(
                        """
                        UPDATE sessions
                        SET state = ?,
                            start_time_mono_ns = COALESCE(?, start_time_mono_ns),
                            end_time_mono_ns = COALESCE(?, end_time_mono_ns),
                            total_events = COALESCE(?, total_events)
                        WHERE session_id = ?
                        """,
                        (state, start_ns, end_ns, tot_ev, sid),
                    )

    def flush(self) -> None:
        """Block until the queue is currently empty and flushed."""
        # Simple wait loop until queue is empty
        while not self._queue.empty():
            time.sleep(0.01)
        time.sleep(self.flush_interval * 1.5)

    def stop(self) -> None:
        """Signal worker to stop, wait for thread termination."""
        self._queue.put(_POISON_PILL)
        self._worker_thread.join(timeout=3.0)
