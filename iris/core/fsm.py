"""Session Coordinator and Finite State Machine (FSM) for Iris."""

from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional


class SessionState(str, Enum):
    UNARMED = "UNARMED"
    ARMED = "ARMED"
    TRACING = "TRACING"
    COMPLETED = "COMPLETED"
    COMPLETED_TRUNCATED = "COMPLETED_TRUNCATED"
    ABORTED = "ABORTED"


MAX_EVENTS_CIRCUIT_BREAKER = 500_000
MAX_DB_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB


class SessionCoordinator:
    """Coordinates lifecycle transitions, predicate matching, and circuit breaking."""

    def __init__(
        self,
        session_id: str,
        entry_file: str,
        entry_function: str,
        condition: Optional[str] = None,
        timeout_seconds: int = 60,
    ):
        self.session_id = session_id
        self.entry_file = self._normalize_path(entry_file)
        self.entry_function = entry_function
        self.condition = condition.strip() if condition else None
        self.timeout_seconds = timeout_seconds

        self.state: SessionState = SessionState.ARMED
        self.arm_time_mono_ns: int = time.monotonic_ns()
        self.start_time_mono_ns: Optional[int] = None
        self.end_time_mono_ns: Optional[int] = None
        self.total_events: int = 0
        self.root_execution_id: Optional[str] = None

    @staticmethod
    def _normalize_path(path_str: str) -> str:
        """Normalize file paths for consistent comparison across platforms."""
        return Path(path_str).name.lower()

    def matches_entry(
        self,
        code_filename: str,
        func_name: str,
        frame_locals: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check whether the executed function matches the armed entry target and condition."""
        if self.state != SessionState.ARMED:
            return False

        if func_name != self.entry_function:
            return False

        # Match filename or basename
        code_file_norm = Path(code_filename).name.lower()
        if not (self.entry_file in code_file_norm or code_file_norm in self.entry_file):
            return False

        # Evaluate condition if defined
        if self.condition:
            if not frame_locals:
                return False
            safe_builtins = {
                "len": len,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "abs": abs,
                "min": min,
                "max": max,
                "isinstance": isinstance,
                "True": True,
                "False": False,
                "None": None,
            }
            try:
                result = eval(self.condition, {"__builtins__": safe_builtins}, frame_locals)
                return bool(result)
            except Exception:
                # If expression evaluation fails or raises KeyError/AttributeError, do not match
                return False

        return True

    def transition_to_tracing(self, root_execution_id: str) -> None:
        """Transition from ARMED to TRACING upon matching entrypoint."""
        if self.state == SessionState.ARMED:
            self.state = SessionState.TRACING
            self.start_time_mono_ns = time.monotonic_ns()
            self.root_execution_id = root_execution_id

    def increment_event(self, db_path: Optional[Path] = None) -> bool:
        """Increment event counter and check circuit breaker limits.

        Returns:
            True if within safe limits, False if circuit breaker tripped.
        """
        self.total_events += 1

        if self.total_events >= MAX_EVENTS_CIRCUIT_BREAKER:
            self.transition_to_completed(truncated=True)
            return False

        # Check DB file size occasionally every 5,000 events
        if self.total_events % 5000 == 0 and db_path and db_path.exists():
            try:
                if db_path.stat().st_size >= MAX_DB_SIZE_BYTES:
                    self.transition_to_completed(truncated=True)
                    return False
            except OSError:
                pass

        return True

    def transition_to_completed(self, truncated: bool = False) -> None:
        """Finalize session state when root entry returns or breaks."""
        if self.state in (SessionState.TRACING, SessionState.ARMED):
            self.state = (
                SessionState.COMPLETED_TRUNCATED if truncated else SessionState.COMPLETED
            )
            self.end_time_mono_ns = time.monotonic_ns()

    def is_active(self) -> bool:
        """Check if coordinator is actively observing (ARMED or TRACING)."""
        return self.state in (SessionState.ARMED, SessionState.TRACING)
