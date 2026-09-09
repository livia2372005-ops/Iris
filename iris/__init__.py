"""Iris: A flight recorder for CPython code via PEP 669 & MCP."""

from iris.core.runtime import auto_attach_from_db, trace, trace_context

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "trace",
    "trace_context",
    "auto_attach_from_db",
]
