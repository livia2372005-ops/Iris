"""Iris Observer using PEP 669 (sys.monitoring) for non-intrusive flight recording."""

from __future__ import annotations

import contextvars
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any, Dict, Optional

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.sanitizer import DataSanitizer

TOOL_ID = sys.monitoring.DEBUGGER_ID
TOOL_NAME = "iris"


class IrisObserver:
    """Attaches PEP 669 hooks to CPython to record execution events into EventQueue."""

    def __init__(self, coordinator: SessionCoordinator, event_queue: EventQueue):
        self.coordinator = coordinator
        self.queue = event_queue
        self._seq_counter = 0
        self._attached = False
        self._root_token: Optional[contextvars.Token] = None

        # Context variable tracking current execution ID in current async/sync stack
        self._current_exec_var: contextvars.ContextVar[Optional[str]] = (
            contextvars.ContextVar("iris_current_exec_id", default=None)
        )
        self._parent_map: Dict[str, Optional[str]] = {}
        self._prev_locals: Dict[str, Dict[str, Any]] = {}
        self._iris_dir = str(Path(__file__).resolve().parent.parent).lower()
        import sysconfig
        self._stdlib_dir = str(Path(sysconfig.get_path("stdlib")).resolve()).lower()

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    def _get_async_task_id(self) -> Optional[str]:
        """Extract asyncio current task name/id if within an event loop."""
        try:
            import asyncio
            task = asyncio.current_task()
            if task is not None:
                return task.get_name()
        except Exception:
            pass
        return None

    def _is_internal(self, filename: str) -> bool:
        """Check if file belongs to Iris internally or standard library internals."""
        if not filename:
            return True
        norm = str(Path(filename).resolve()).lower()
        if self._iris_dir in norm:
            return True
        # Ignore Python stdlib internals (encodings, json, os, queue, linecache, etc.)
        if norm.startswith(self._stdlib_dir) and "site-packages" not in norm:
            return True
        base = os.path.basename(norm)
        if base in ("queue.py", "threading.py", "linecache.py", "contextvars.py"):
            return True
        return False

    def attach(self) -> None:
        """Register sys.monitoring callbacks and activate monitoring."""
        if self._attached:
            return

        events = sys.monitoring.events
        try:
            sys.monitoring.use_tool_id(TOOL_ID, TOOL_NAME)
        except ValueError:
            # Tool ID might already be registered in this process
            pass

        sys.monitoring.register_callback(TOOL_ID, events.PY_START, self._on_py_start)
        sys.monitoring.register_callback(TOOL_ID, events.PY_RETURN, self._on_py_return)
        sys.monitoring.register_callback(TOOL_ID, events.PY_UNWIND, self._on_py_unwind)
        sys.monitoring.register_callback(TOOL_ID, events.LINE, self._on_line)
        sys.monitoring.register_callback(TOOL_ID, events.BRANCH, self._on_branch)

        # Listen to all 5 event types
        all_events = (
            events.PY_START
            | events.PY_RETURN
            | events.PY_UNWIND
            | events.LINE
            | events.BRANCH
        )
        sys.monitoring.set_events(TOOL_ID, all_events)
        self._attached = True

    def detach(self) -> None:
        """Disable events and free the tool ID."""
        if not self._attached:
            return

        try:
            sys.monitoring.set_events(TOOL_ID, 0)
        except Exception:
            pass

        try:
            sys.monitoring.free_tool_id(TOOL_ID)
        except Exception:
            pass

        self._attached = False

    def _on_py_start(self, code: Any, instruction_offset: int) -> None:
        """Called when a Python function call starts."""
        filename = code.co_filename
        func_name = code.co_name

        if self._is_internal(filename):
            return

        task_id = self._get_async_task_id()

        # Case 1: Currently ARMED, check if this matches entrypoint
        if self.coordinator.state == SessionState.ARMED:
            if self.coordinator.matches_entry(filename, func_name):
                root_id = f"exec_{uuid.uuid4().hex[:12]}"
                self.coordinator.transition_to_tracing(root_id)

                self._parent_map[root_id] = None
                self._current_exec_var.set(root_id)
                self._prev_locals[root_id] = {}

                now_ns = time.monotonic_ns()
                self.queue.put_execution_start(
                    execution_id=root_id,
                    session_id=self.coordinator.session_id,
                    parent_execution_id=None,
                    function_name=func_name,
                    file_path=filename,
                    start_time_mono_ns=now_ns,
                    async_task_id=task_id,
                )
                self.queue.put_session_update(
                    session_id=self.coordinator.session_id,
                    state=SessionState.TRACING.value,
                    start_time_mono_ns=now_ns,
                )
            return

        # Case 2: Currently TRACING, record nested sub-call
        if self.coordinator.state == SessionState.TRACING:
            parent_id = self._current_exec_var.get()
            if parent_id is not None:
                child_id = f"exec_{uuid.uuid4().hex[:12]}"
                self._parent_map[child_id] = parent_id
                self._current_exec_var.set(child_id)
                self._prev_locals[child_id] = {}

                now_ns = time.monotonic_ns()
                self.queue.put_execution_start(
                    execution_id=child_id,
                    session_id=self.coordinator.session_id,
                    parent_execution_id=parent_id,
                    function_name=func_name,
                    file_path=filename,
                    start_time_mono_ns=now_ns,
                    async_task_id=task_id,
                )

    def _on_line(self, code: Any, line_number: int) -> None:
        """Called on execution of each source code line."""
        if self.coordinator.state != SessionState.TRACING:
            return

        current_id = self._current_exec_var.get()
        if not current_id:
            return

        if self._is_internal(code.co_filename):
            return

        # Check circuit breaker
        if not self.coordinator.increment_event(self.queue.db_path):
            self.detach()
            self._finalize_session(truncated=True)
            return

        # Snapshot locals safely
        frame = sys._getframe(1)
        locals_snap = DataSanitizer.sanitize_locals(frame.f_locals)

        # Calculate variable delta
        prev = self._prev_locals.get(current_id, {})
        delta = {
            k: v for k, v in locals_snap.items()
            if k not in prev or prev[k] != v
        }
        self._prev_locals[current_id] = locals_snap.copy()

        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        seq = self._next_seq()
        self.queue.put_event(
            event_id=event_id,
            execution_id=current_id,
            sequence_number=seq,
            event_type="LINE",
            line_number=line_number,
            branch_taken=None,
            payload={"delta": delta, "locals": locals_snap},
        )

    def _on_branch(self, code: Any, instruction_offset: int, destination_offset: int) -> None:
        """Called when a branching instruction is evaluated."""
        if self.coordinator.state != SessionState.TRACING:
            return

        current_id = self._current_exec_var.get()
        if not current_id:
            return

        if self._is_internal(code.co_filename):
            return

        if not self.coordinator.increment_event(self.queue.db_path):
            self.detach()
            self._finalize_session(truncated=True)
            return

        # Branch taken: 1 if jumping forward/destination, 0 otherwise
        branch_taken = 1 if destination_offset > instruction_offset else 0
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        seq = self._next_seq()

        frame = sys._getframe(1)
        self.queue.put_event(
            event_id=event_id,
            execution_id=current_id,
            sequence_number=seq,
            event_type="BRANCH",
            line_number=frame.f_lineno,
            branch_taken=branch_taken,
            payload={
                "offset": instruction_offset,
                "dest": destination_offset,
            },
        )

    def _on_py_return(self, code: Any, instruction_offset: int, retval: Any) -> None:
        """Called when a Python function returns normally."""
        if self.coordinator.state != SessionState.TRACING:
            return

        current_id = self._current_exec_var.get()
        if not current_id:
            return

        if self._is_internal(code.co_filename):
            return

        now_ns = time.monotonic_ns()
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        seq = self._next_seq()

        sanitized_ret = DataSanitizer.sanitize_value(retval)
        frame = sys._getframe(1)
        self.queue.put_event(
            event_id=event_id,
            execution_id=current_id,
            sequence_number=seq,
            event_type="RETURN",
            line_number=frame.f_lineno,
            branch_taken=None,
            payload={"return_value": sanitized_ret},
        )
        self.queue.put_execution_end(current_id, now_ns)
        self._prev_locals.pop(current_id, None)

        parent_id = self._parent_map.get(current_id)
        self._current_exec_var.set(parent_id)

        # Check if root function returned
        if current_id == self.coordinator.root_execution_id:
            self.detach()
            self._finalize_session(truncated=False)

    def _on_py_unwind(self, code: Any, instruction_offset: int, exc: Any) -> None:
        """Called when an exception unwinds out of a function."""
        if self.coordinator.state != SessionState.TRACING:
            return

        current_id = self._current_exec_var.get()
        if not current_id:
            return

        if self._is_internal(code.co_filename):
            return

        now_ns = time.monotonic_ns()
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        seq = self._next_seq()

        frame = sys._getframe(1)
        self.queue.put_event(
            event_id=event_id,
            execution_id=current_id,
            sequence_number=seq,
            event_type="EXCEPTION",
            line_number=frame.f_lineno,
            branch_taken=None,
            payload={"exception": str(exc), "exc_type": type(exc).__name__},
        )
        self.queue.put_execution_end(current_id, now_ns)
        self._prev_locals.pop(current_id, None)

        parent_id = self._parent_map.get(current_id)
        self._current_exec_var.set(parent_id)

        if current_id == self.coordinator.root_execution_id:
            self.detach()
            self._finalize_session(truncated=False)

    def _finalize_session(self, truncated: bool = False) -> None:
        """Flush the queue and update session final state in SQLite."""
        self.coordinator.transition_to_completed(truncated=truncated)
        self.queue.put_session_update(
            session_id=self.coordinator.session_id,
            state=self.coordinator.state.value,
            end_time_mono_ns=self.coordinator.end_time_mono_ns,
            total_events=self.coordinator.total_events,
        )
        self.queue.flush()
