"""Pytest plugin to automatically activate Iris flight recording for ARMED sessions."""

from typing import Optional
import pytest

from iris.core.observer import IrisObserver
from iris.core.runtime import auto_attach_from_db

_active_observer: Optional[IrisObserver] = None


def pytest_configure(config: pytest.Config) -> None:
    """Hook called when pytest starts. Automatically hooks into active ARMED session."""
    global _active_observer
    _active_observer = auto_attach_from_db()
    if _active_observer:
        sid = _active_observer.coordinator.session_id
        target = f"{_active_observer.coordinator.entry_function} in {_active_observer.coordinator.entry_file}"
        print(f"\n[Iris] Auto-attached flight recorder for session '{sid}' (Target: {target})")


def pytest_unconfigure(config: pytest.Config) -> None:
    """Hook called when pytest finishes. Cleanly finalizes and flushes trace events."""
    global _active_observer
    if _active_observer:
        _active_observer.detach()
        if _active_observer.coordinator.is_active():
            _active_observer._finalize_session(truncated=False)
        _active_observer.queue.stop()
        _active_observer = None
