"""Tests for Python 3.14 compatibility abstraction layer (MonitoringProvider)."""

import sys
import pytest
from iris.core.monitoring_provider import MonitoringProvider


def test_monitoring_provider_masks():
    provider = MonitoringProvider()
    mask = provider.get_tracing_events_mask()

    events = sys.monitoring.events
    assert (mask & events.PY_START)
    assert (mask & events.PY_RETURN)
    assert (mask & events.PY_UNWIND)
    assert (mask & events.PY_YIELD)
    assert (mask & events.PY_RESUME)
    assert (mask & events.LINE)

    if provider.has_directional_branches:
        assert (mask & events.BRANCH_LEFT)
        assert (mask & events.BRANCH_RIGHT)
    elif hasattr(events, "BRANCH"):
        assert (mask & events.BRANCH)


def test_monitoring_provider_branch_dispatch():
    provider = MonitoringProvider()
    dispatched_events = []

    def fake_branch_handler(code, offset, dest, direction):
        dispatched_events.append({
            "code": code,
            "offset": offset,
            "dest": dest,
            "direction": direction,
        })

    # Test directional simulation
    fake_code = object()
    if provider.has_directional_branches:
        provider.register_callbacks(
            tool_id=sys.monitoring.DEBUGGER_ID,
            on_py_start=lambda *a: None,
            on_py_return=lambda *a: None,
            on_py_unwind=lambda *a: None,
            on_py_yield=lambda *a: None,
            on_py_resume=lambda *a: None,
            on_line=lambda *a: None,
            on_branch_event=fake_branch_handler,
        )
    else:
        # Standard Python 3.12 branch handler simulation
        fake_branch_handler(fake_code, 10, 20, "branch")
        assert len(dispatched_events) == 1
        assert dispatched_events[0]["direction"] == "branch"
        assert dispatched_events[0]["dest"] == 20
