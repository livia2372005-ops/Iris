"""Provider and adapter layer for PEP 669 sys.monitoring across Python versions.

Provides forward compatibility for Python 3.14+ (which deprecates BRANCH in favor
of BRANCH_LEFT and BRANCH_RIGHT) while preserving optimal performance on Python 3.12-3.13.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional


class MonitoringProvider:
    """Adapts sys.monitoring event masks and callbacks across Python 3.12, 3.13, and 3.14+."""

    def __init__(self) -> None:
        events = sys.monitoring.events
        self.has_directional_branches: bool = (
            hasattr(events, "BRANCH_LEFT") and hasattr(events, "BRANCH_RIGHT")
        )
        self.is_py314_plus: bool = sys.version_info >= (3, 14)

    def get_tracing_events_mask(self) -> int:
        """Return the complete monitoring mask for active tracing."""
        events = sys.monitoring.events
        mask = (
            events.PY_START
            | events.PY_RETURN
            | events.PY_UNWIND
            | events.PY_YIELD
            | events.PY_RESUME
            | events.LINE
        )

        if self.has_directional_branches:
            mask |= (events.BRANCH_LEFT | events.BRANCH_RIGHT)
        elif hasattr(events, "BRANCH"):
            mask |= events.BRANCH

        return mask

    def register_callbacks(
        self,
        tool_id: int,
        on_py_start: Callable,
        on_py_return: Callable,
        on_py_unwind: Callable,
        on_py_yield: Callable,
        on_py_resume: Callable,
        on_line: Callable,
        on_branch_event: Callable[[Any, int, int, str], None],
    ) -> None:
        """Register all PEP 669 callbacks with appropriate version handling."""
        events = sys.monitoring.events

        sys.monitoring.register_callback(tool_id, events.PY_START, on_py_start)
        sys.monitoring.register_callback(tool_id, events.PY_RETURN, on_py_return)
        sys.monitoring.register_callback(tool_id, events.PY_UNWIND, on_py_unwind)
        sys.monitoring.register_callback(tool_id, events.PY_YIELD, on_py_yield)
        sys.monitoring.register_callback(tool_id, events.PY_RESUME, on_py_resume)
        sys.monitoring.register_callback(tool_id, events.LINE, on_line)

        if self.has_directional_branches:
            def _left(code: Any, offset: int, dest: int) -> Any:
                on_branch_event(code, offset, dest, "left")

            def _right(code: Any, offset: int, dest: int) -> Any:
                on_branch_event(code, offset, dest, "right")

            sys.monitoring.register_callback(tool_id, events.BRANCH_LEFT, _left)
            sys.monitoring.register_callback(tool_id, events.BRANCH_RIGHT, _right)
        elif hasattr(events, "BRANCH"):
            def _branch(code: Any, offset: int, dest: int) -> Any:
                on_branch_event(code, offset, dest, "branch")

            sys.monitoring.register_callback(tool_id, events.BRANCH, _branch)
