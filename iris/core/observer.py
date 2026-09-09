"""Iris Observer using PEP 669 (sys.monitoring) for non-intrusive flight recording.

Supports multi-session observation, asyncio task & thread-safe context isolation,
coroutine yield/resume tracking, and forward compatibility for Python 3.14+ branch monitoring.
"""

from __future__ import annotations

import contextvars
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from iris.analysis.ast_lineage import ASTLineageAnalyzer
from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.monitoring_provider import MonitoringProvider
from iris.core.sanitizer import DataSanitizer

TOOL_ID = sys.monitoring.DEBUGGER_ID
TOOL_NAME = "iris"


class IrisObserver:
    """Attaches PEP 669 hooks to CPython to record execution events into EventQueue."""

    def __init__(
        self,
        coordinator: Optional[SessionCoordinator] = None,
        event_queue: Optional[EventQueue] = None,
        coordinators: Optional[List[SessionCoordinator]] = None,
    ) -> None:
        self.queue: EventQueue = event_queue or EventQueue()
        self.coordinators: Dict[str, SessionCoordinator] = {}
        if coordinator:
            self.coordinators[coordinator.session_id] = coordinator
        if coordinators:
            for c in coordinators:
                self.coordinators[c.session_id] = c

        self._lock = threading.RLock()
        self._seq_counter = 0
        self._attached = False
        self._monitoring_provider = MonitoringProvider()

        # Context variable tracking (session_id, execution_id) in async/thread context
        self._current_context_var: contextvars.ContextVar[Optional[Tuple[str, str]]] = (
            contextvars.ContextVar("iris_current_context", default=None)
        )

        self._parent_map: Dict[str, Optional[str]] = {}
        self._prev_locals: Dict[str, Dict[str, Any]] = {}
        self._last_line: Dict[str, int] = {}
        self._var_versions: Dict[str, Dict[str, int]] = {}  # execution_id -> {var_name: version}
        self._active_vrefs: Dict[str, Dict[str, str]] = {}  # execution_id -> {var_name: value_ref_id}
        self._iris_dir = str(Path(__file__).resolve().parent.parent).lower()

        import sysconfig
        self._stdlib_dir = str(Path(sysconfig.get_path("stdlib")).resolve()).lower()
        self._internal_cache: Dict[str, bool] = {}

    @property
    def coordinator(self) -> SessionCoordinator:
        """Return the primary coordinator for single-session backward compatibility."""
        with self._lock:
            if not self.coordinators:
                raise ValueError("No coordinator registered in IrisObserver.")
            return next(iter(self.coordinators.values()))

    @coordinator.setter
    def coordinator(self, val: SessionCoordinator) -> None:
        with self._lock:
            self.coordinators[val.session_id] = val

    def add_coordinator(self, coord: SessionCoordinator) -> None:
        """Register an additional session coordinator for multi-session tracing."""
        with self._lock:
            self.coordinators[coord.session_id] = coord

    @property
    def _current_exec_var(self):
        """Compatibility adapter for legacy code accessing _current_exec_var."""
        class _ExecVarCompat:
            def __init__(self, ctx_var, coordinators, lock):
                self._ctx = ctx_var
                self._coords = coordinators
                self._lock = lock

            def get(self, default=None) -> Optional[str]:
                val = self._ctx.get()
                return val[1] if val else default

            def set(self, execution_id: Optional[str]) -> None:
                if execution_id is None:
                    self._ctx.set(None)
                else:
                    curr = self._ctx.get()
                    if curr:
                        sid = curr[0]
                    else:
                        with self._lock:
                            sid = next(iter(self._coords.keys())) if self._coords else "default"
                    self._ctx.set((sid, execution_id))

        return _ExecVarCompat(self._current_context_var, self.coordinators, self._lock)

    def _next_seq(self) -> int:
        """Thread-safe increment for strictly monotonic sequence numbers."""
        with self._lock:
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
        cached = self._internal_cache.get(filename)
        if cached is not None:
            return cached

        norm = str(Path(filename).resolve()).lower()
        res = False
        if self._iris_dir in norm:
            res = True
        elif norm.startswith(self._stdlib_dir) and "site-packages" not in norm:
            res = True
        else:
            base = os.path.basename(norm)
            if base in ("queue.py", "threading.py", "linecache.py", "contextvars.py"):
                res = True

        self._internal_cache[filename] = res
        return res

    def attach(self) -> None:
        """Register sys.monitoring callbacks and activate minimal ARMED monitoring."""
        if self._attached:
            return

        events = sys.monitoring.events
        try:
            sys.monitoring.use_tool_id(TOOL_ID, TOOL_NAME)
        except ValueError:
            # Tool ID might already be registered in this process
            pass

        self._monitoring_provider.register_callbacks(
            tool_id=TOOL_ID,
            on_py_start=self._on_py_start,
            on_py_return=self._on_py_return,
            on_py_unwind=self._on_py_unwind,
            on_py_yield=self._on_py_yield,
            on_py_resume=self._on_py_resume,
            on_line=self._on_line,
            on_branch_event=self._on_branch_event,
        )

        # Performance fix: When ARMED, ONLY listen to PY_START globally!
        sys.monitoring.set_events(TOOL_ID, events.PY_START)
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

        # Check for matching ARMED coordinators
        with self._lock:
            coords_snapshot = list(self.coordinators.values())

        for coord in coords_snapshot:
            if coord.state == SessionState.ARMED:
                frame_locals = None
                if coord.condition:
                    try:
                        frame = sys._getframe(1)
                        frame_locals = frame.f_locals
                    except Exception:
                        frame_locals = {}

                if coord.matches_entry(filename, func_name, frame_locals):
                    root_id = f"exec_{uuid.uuid4().hex[:12]}"
                    coord.transition_to_tracing(root_id)

                    try:
                        f = sys._getframe(1)
                        args_snap = DataSanitizer.sanitize_locals(f.f_locals)
                    except Exception:
                        args_snap = {}

                    self._current_context_var.set((coord.session_id, root_id))

                    # Elevate events to full tracing
                    all_events = self._monitoring_provider.get_tracing_events_mask()
                    sys.monitoring.set_events(TOOL_ID, all_events)

                    now_ns = time.monotonic_ns()
                    self.queue.put_execution_start(
                        execution_id=root_id,
                        session_id=coord.session_id,
                        parent_execution_id=None,
                        function_name=func_name,
                        file_path=filename,
                        start_time_mono_ns=now_ns,
                        async_task_id=task_id,
                    )
                    self.queue.put_session_update(
                        session_id=coord.session_id,
                        state=SessionState.TRACING.value,
                        start_time_mono_ns=now_ns,
                    )

                    with self._lock:
                        self._parent_map[root_id] = None
                        self._prev_locals[root_id] = args_snap.copy()
                        self._last_line[root_id] = code.co_firstlineno
                        self._var_versions[root_id] = {}
                        self._active_vrefs[root_id] = {}
                        for arg_k, arg_v in args_snap.items():
                            vref_id = f"vref_{uuid.uuid4().hex[:12]}"
                            self._var_versions[root_id][arg_k] = 1
                            self._active_vrefs[root_id][arg_k] = vref_id
                            v_json = json.dumps(arg_v, ensure_ascii=False) if arg_v is not None else "null"
                            self.queue.put_value_ref(
                                value_ref_id=vref_id,
                                execution_id=root_id,
                                variable_name=arg_k,
                                version=1,
                                line_number=code.co_firstlineno,
                                value_snapshot=v_json,
                                epistemic_status="Observed",
                            )
                    return

        # Case 2: Currently TRACING, record nested sub-call
        ctx = self._current_context_var.get()
        if ctx is not None:
            session_id, parent_id = ctx
            coord = self.coordinators.get(session_id)
            if coord and coord.state == SessionState.TRACING:
                child_id = f"exec_{uuid.uuid4().hex[:12]}"

                try:
                    f = sys._getframe(1)
                    child_args = DataSanitizer.sanitize_locals(f.f_locals)
                except Exception:
                    child_args = {}

                self._current_context_var.set((session_id, child_id))

                now_ns = time.monotonic_ns()
                self.queue.put_execution_start(
                    execution_id=child_id,
                    session_id=session_id,
                    parent_execution_id=parent_id,
                    function_name=func_name,
                    file_path=filename,
                    start_time_mono_ns=now_ns,
                    async_task_id=task_id,
                )

                with self._lock:
                    self._parent_map[child_id] = parent_id
                    self._prev_locals[child_id] = child_args.copy()
                    self._last_line[child_id] = code.co_firstlineno
                    self._var_versions[child_id] = {}
                    self._active_vrefs[child_id] = {}
                    for arg_k, arg_v in child_args.items():
                        vref_id = f"vref_{uuid.uuid4().hex[:12]}"
                        self._var_versions[child_id][arg_k] = 1
                        self._active_vrefs[child_id][arg_k] = vref_id
                        v_json = json.dumps(arg_v, ensure_ascii=False) if arg_v is not None else "null"
                        self.queue.put_value_ref(
                            value_ref_id=vref_id,
                            execution_id=child_id,
                            variable_name=arg_k,
                            version=1,
                            line_number=code.co_firstlineno,
                            value_snapshot=v_json,
                            epistemic_status="Observed",
                        )

    def _on_line(self, code: Any, line_number: int) -> None:
        """Called on execution of each source code line."""
        ctx = self._current_context_var.get()
        if not ctx:
            return
        session_id, current_id = ctx
        coord = self.coordinators.get(session_id)
        if not coord or coord.state != SessionState.TRACING:
            return

        if self._is_internal(code.co_filename):
            return

        # Check circuit breaker
        if not coord.increment_event(self.queue.db_path):
            self._finalize_session(coordinator=coord, truncated=True)
            self._check_all_sessions_done()
            return

        # Snapshot locals safely
        frame = sys._getframe(1)
        locals_snap = DataSanitizer.sanitize_locals(frame.f_locals)

        with self._lock:
            prev = self._prev_locals.get(current_id, {})
            delta = {
                k: v for k, v in locals_snap.items()
                if k not in prev or prev[k] != v
            }
            self._prev_locals[current_id] = locals_snap.copy()
            target_line = self._last_line.get(current_id, line_number)
            self._last_line[current_id] = line_number

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

        # Data Lineage Graph causality capture (Mục 13.1 & 13.3)
        if delta:
            causal_info = ASTLineageAnalyzer.get_line_info(code.co_filename, target_line)
            op = causal_info.operation if causal_info else "assign"
            read_vars = causal_info.read_vars if causal_info else set()

            with self._lock:
                if current_id not in self._var_versions:
                    self._var_versions[current_id] = {}
                if current_id not in self._active_vrefs:
                    self._active_vrefs[current_id] = {}

                for var_name, new_val in delta.items():
                    cur_ver = self._var_versions[current_id].get(var_name, 0) + 1
                    self._var_versions[current_id][var_name] = cur_ver
                    prev_vref = self._active_vrefs[current_id].get(var_name)

                    vref_id = f"vref_{uuid.uuid4().hex[:12]}"
                    self._active_vrefs[current_id][var_name] = vref_id

                    v_snap = json.dumps(new_val, ensure_ascii=False) if new_val is not None else "null"
                    self.queue.put_value_ref(
                        value_ref_id=vref_id,
                        execution_id=current_id,
                        variable_name=var_name,
                        version=cur_ver,
                        line_number=target_line,
                        value_snapshot=v_snap,
                        epistemic_status="Observed",
                    )

                    # Link self modification (e.g. x = x + 1, x += 1)
                    if var_name in read_vars and prev_vref:
                        edge_id = f"edge_{uuid.uuid4().hex[:12]}"
                        self.queue.put_lineage_edge(
                            edge_id=edge_id,
                            source_value_ref_id=prev_vref,
                            target_value_ref_id=vref_id,
                            operation=op,
                        )

                    # Link other read inputs
                    for src_name in read_vars:
                        if src_name != var_name:
                            src_vref = self._active_vrefs[current_id].get(src_name)
                            if src_vref:
                                edge_id = f"edge_{uuid.uuid4().hex[:12]}"
                                self.queue.put_lineage_edge(
                                    edge_id=edge_id,
                                    source_value_ref_id=src_vref,
                                    target_value_ref_id=vref_id,
                                    operation=op,
                                )
                            elif src_name in frame.f_globals and not src_name.startswith("__"):
                                # Static constant (Mục 13.3)
                                static_raw = frame.f_globals.get(src_name)
                                if (
                                    static_raw is not None
                                    and not callable(static_raw)
                                    and not hasattr(static_raw, "__loader__")
                                    and not hasattr(static_raw, "__module__")
                                ):
                                    static_vref = f"vref_static_{uuid.uuid4().hex[:8]}"
                                    static_val = DataSanitizer.sanitize_value(static_raw)
                                    s_snap = json.dumps(static_val, ensure_ascii=False) if static_val is not None else "null"
                                    self._active_vrefs[current_id][src_name] = static_vref
                                    self.queue.put_value_ref(
                                        value_ref_id=static_vref,
                                        execution_id=current_id,
                                        variable_name=src_name,
                                        version=1,
                                        line_number=0,
                                        value_snapshot=s_snap,
                                        epistemic_status="Static",
                                    )
                                    edge_id = f"edge_{uuid.uuid4().hex[:12]}"
                                    self.queue.put_lineage_edge(
                                        edge_id=edge_id,
                                        source_value_ref_id=static_vref,
                                        target_value_ref_id=vref_id,
                                        operation=op,
                                    )

    def _on_branch_event(
        self,
        code: Any,
        instruction_offset: int,
        destination_offset: int,
        direction: str,
    ) -> None:
        """Called when a branching instruction is evaluated (Python 3.12-3.13 and 3.14+)."""
        ctx = self._current_context_var.get()
        if not ctx:
            return
        session_id, current_id = ctx
        coord = self.coordinators.get(session_id)
        if not coord or coord.state != SessionState.TRACING:
            return

        if self._is_internal(code.co_filename):
            return

        if not coord.increment_event(self.queue.db_path):
            self._finalize_session(coordinator=coord, truncated=True)
            self._check_all_sessions_done()
            return

        if direction in ("left", "right"):
            jumped = (direction == "left")
            payload = {
                "offset": instruction_offset,
                "dest": destination_offset,
                "direction": direction,
                "jumped": jumped,
            }
        else:
            # Python 3.12 / 3.13: fallthrough is offset + 2
            jumped = destination_offset != (instruction_offset + 2)
            payload = {
                "offset": instruction_offset,
                "dest": destination_offset,
                "jumped": jumped,
            }

        branch_taken = 1 if jumped else 0
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
            payload=payload,
        )

    def _on_py_yield(self, code: Any, instruction_offset: int, retval: Any) -> None:
        """Called when a generator or coroutine yields execution."""
        ctx = self._current_context_var.get()
        if not ctx:
            return
        session_id, current_id = ctx
        coord = self.coordinators.get(session_id)
        if not coord or coord.state != SessionState.TRACING:
            return

        if self._is_internal(code.co_filename):
            return

        if not coord.increment_event(self.queue.db_path):
            self._finalize_session(coordinator=coord, truncated=True)
            self._check_all_sessions_done()
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
            event_type="YIELD",
            line_number=frame.f_lineno,
            branch_taken=None,
            payload={"offset": instruction_offset, "yield_value": sanitized_ret},
        )

    def _on_py_resume(self, code: Any, instruction_offset: int) -> None:
        """Called when a generator or coroutine resumes execution."""
        ctx = self._current_context_var.get()
        if not ctx:
            return
        session_id, current_id = ctx
        coord = self.coordinators.get(session_id)
        if not coord or coord.state != SessionState.TRACING:
            return

        if self._is_internal(code.co_filename):
            return

        if not coord.increment_event(self.queue.db_path):
            self._finalize_session(coordinator=coord, truncated=True)
            self._check_all_sessions_done()
            return

        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        seq = self._next_seq()
        frame = sys._getframe(1)
        self.queue.put_event(
            event_id=event_id,
            execution_id=current_id,
            sequence_number=seq,
            event_type="RESUME",
            line_number=frame.f_lineno,
            branch_taken=None,
            payload={"offset": instruction_offset},
        )

    def _on_py_return(self, code: Any, instruction_offset: int, retval: Any) -> None:
        """Called when a Python function returns normally."""
        ctx = self._current_context_var.get()
        if not ctx:
            return
        session_id, current_id = ctx
        coord = self.coordinators.get(session_id)
        if not coord or coord.state != SessionState.TRACING:
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

        with self._lock:
            self._prev_locals.pop(current_id, None)
            parent_id = self._parent_map.get(current_id)

        if parent_id is not None:
            self._current_context_var.set((session_id, parent_id))
        else:
            self._current_context_var.set(None)

        # Check if root function returned for this coordinator
        if current_id == coord.root_execution_id:
            self._finalize_session(coordinator=coord, truncated=False)
            self._check_all_sessions_done()

    def _on_py_unwind(self, code: Any, instruction_offset: int, exc: Any) -> None:
        """Called when an exception unwinds out of a function."""
        ctx = self._current_context_var.get()
        if not ctx:
            return
        session_id, current_id = ctx
        coord = self.coordinators.get(session_id)
        if not coord or coord.state != SessionState.TRACING:
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

        with self._lock:
            self._prev_locals.pop(current_id, None)
            parent_id = self._parent_map.get(current_id)

        if parent_id is not None:
            self._current_context_var.set((session_id, parent_id))
        else:
            self._current_context_var.set(None)

        if current_id == coord.root_execution_id:
            self._finalize_session(coordinator=coord, truncated=False)
            self._check_all_sessions_done()

    def _check_all_sessions_done(self) -> None:
        """Adjust or disable monitoring based on remaining active sessions."""
        with self._lock:
            any_tracing = any(c.state == SessionState.TRACING for c in self.coordinators.values())
            any_armed = any(c.state == SessionState.ARMED for c in self.coordinators.values())

        if any_tracing:
            return
        elif any_armed:
            try:
                sys.monitoring.set_events(TOOL_ID, sys.monitoring.events.PY_START)
            except Exception:
                pass
        else:
            self.detach()

    def _finalize_session(
        self,
        coordinator: Optional[SessionCoordinator] = None,
        truncated: bool = False,
    ) -> None:
        """Flush the queue and update session final state in SQLite."""
        target_coord = coordinator or self.coordinator
        target_coord.transition_to_completed(truncated=truncated)
        self.queue.put_session_update(
            session_id=target_coord.session_id,
            state=target_coord.state.value,
            end_time_mono_ns=target_coord.end_time_mono_ns,
            total_events=target_coord.total_events,
        )
        self.queue.flush()

    def stop_session(self, session_id: str) -> Dict[str, Any]:
        """Actively terminate a session, detach monitoring if none remain, and persist state."""
        with self._lock:
            coord = self.coordinators.get(session_id)
            if coord:
                coord.transition_to_stopped()
                self.queue.put_session_update(
                    session_id=coord.session_id,
                    state=SessionState.STOPPED.value,
                    end_time_mono_ns=coord.end_time_mono_ns,
                    total_events=coord.total_events,
                )
                self._check_all_sessions_done()

        self.queue.flush()
        from iris.storage.db import stop_session as db_stop_session
        return db_stop_session(session_id, db_path=self.queue.db_path)

