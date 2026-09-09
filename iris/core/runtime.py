"""Runtime integration utilities, decorators, and script execution runner."""

from __future__ import annotations

from contextlib import contextmanager
import functools
import importlib.util
import os
from pathlib import Path
import sys
import uuid
from typing import Any, Callable, Generator, List, Optional

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator, SessionState
from iris.core.observer import IrisObserver
from iris.storage.db import arm_session, get_latest_armed_session, init_db


@contextmanager
def trace_context(
    entry_file: str,
    entry_function: str,
    session_id: Optional[str] = None,
    timeout_seconds: int = 60,
) -> Generator[str, None, None]:
    """Context manager to trace a target entry point."""
    init_db()
    sid = session_id or f"iris_{uuid.uuid4().hex[:12]}"
    arm_session(sid, entry_file, entry_function)

    coordinator = SessionCoordinator(
        session_id=sid,
        entry_file=entry_file,
        entry_function=entry_function,
        timeout_seconds=timeout_seconds,
    )
    event_queue = EventQueue()
    observer = IrisObserver(coordinator=coordinator, event_queue=event_queue)

    observer.attach()
    try:
        yield sid
    finally:
        observer.detach()
        if coordinator.is_active():
            observer._finalize_session(truncated=False)
        event_queue.stop()


def trace(
    entry_function: Optional[str] = None,
    entry_file: Optional[str] = None,
    timeout_seconds: int = 60,
) -> Callable:
    """Decorator to trace a specific function execution."""
    def decorator(fn: Callable) -> Callable:
        target_func = entry_function or fn.__name__
        target_file = entry_file or fn.__code__.co_filename

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_context(target_file, target_func, timeout_seconds=timeout_seconds):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


from iris.storage.db import arm_session, get_all_armed_sessions, get_latest_armed_session, init_db


def auto_attach_from_db() -> Optional[IrisObserver]:
    """Check the database for any pending ARMED sessions and attach observer."""
    init_db()
    all_armed = get_all_armed_sessions()
    if not all_armed:
        return None

    coordinators = [
        SessionCoordinator(
            session_id=armed["session_id"],
            entry_file=armed["entry_file"],
            entry_function=armed["entry_function"],
        )
        for armed in all_armed
    ]
    event_queue = EventQueue()
    observer = IrisObserver(coordinators=coordinators, event_queue=event_queue)
    observer.attach()
    return observer


def run_script(script_path: str, script_args: Optional[List[str]] = None) -> None:
    """Execute a Python script with Iris flight recording active."""
    resolved = Path(script_path).resolve()
    if not resolved.exists():
        print(f"Error: Script file '{script_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    init_db()
    armed = get_latest_armed_session()

    entry_file = armed["entry_file"] if armed else resolved.name
    entry_function = armed["entry_function"] if armed else "<module>"
    session_id = armed["session_id"] if armed else f"iris_{uuid.uuid4().hex[:12]}"

    if not armed:
        arm_session(session_id, entry_file, entry_function)

    coordinator = SessionCoordinator(
        session_id=session_id,
        entry_file=entry_file,
        entry_function=entry_function,
    )
    event_queue = EventQueue()
    observer = IrisObserver(coordinator=coordinator, event_queue=event_queue)

    # Set up sys.argv for the target script
    sys.argv = [str(resolved)] + (script_args or [])
    sys.path.insert(0, str(resolved.parent))

    observer.attach()
    try:
        spec = importlib.util.spec_from_file_location("__main__", str(resolved))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {resolved}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["__main__"] = module
        spec.loader.exec_module(module)
    finally:
        observer.detach()
        if coordinator.is_active():
            observer._finalize_session(truncated=False)
        event_queue.stop()
