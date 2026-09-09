"""Data sanitizer for redaction of sensitive credentials and size bounds."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Union

BLACKLIST_NAMES: Set[str] = {
    "password",
    "secret",
    "token",
    "api_key",
    "auth",
    "credit_card",
    "private_key",
    "ssn",
}

REGEX_PATTERNS = [
    ("JWT", re.compile(r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]{10,}")),
    ("AWS_KEY", re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----", re.IGNORECASE)),
    ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

MAX_STRING_LEN = 256
HEAD_STRING_LEN = 128
TAIL_STRING_LEN = 64
MAX_COLLECTION_ITEMS = 20


class DataSanitizer:
    """Performs in-memory data sanitization, secret masking, and size truncation."""

    @classmethod
    def is_name_sensitive(cls, name: str) -> bool:
        """Check if a variable or attribute name matches sensitive blacklist."""
        lowered = name.lower()
        return any(term in lowered for term in BLACKLIST_NAMES)

    @classmethod
    def sanitize_string(cls, value: str) -> str:
        """Apply regex mask and length truncation to string values."""
        # Check patterns
        for name, pattern in REGEX_PATTERNS:
            if pattern.search(value):
                return f"[REDACTED_{name}: length={len(value)}]"

        # Check length
        if len(value) > MAX_STRING_LEN:
            omitted = len(value) - (HEAD_STRING_LEN + TAIL_STRING_LEN)
            return (
                value[:HEAD_STRING_LEN]
                + f"... [truncated {omitted} chars] ..."
                + value[-TAIL_STRING_LEN:]
            )

        return value

    @classmethod
    def sanitize_value(cls, val: Any, depth: int = 0) -> Any:
        """Recursively sanitize an arbitrary Python object into a safe JSON structure."""
        if depth > 3:
            return "[NESTED_OBJECT_LIMIT]"

        if val is None or isinstance(val, (int, float, bool)):
            return val

        if isinstance(val, str):
            return cls.sanitize_string(val)

        if isinstance(val, bytes):
            return f"[BYTES: len={len(val)}]"

        if isinstance(val, (list, tuple, set)):
            total_items = len(val)
            items_to_process = list(val)[:MAX_COLLECTION_ITEMS]
            result = [cls.sanitize_value(item, depth + 1) for item in items_to_process]
            if total_items > MAX_COLLECTION_ITEMS:
                result.append(f"... [truncated {total_items - MAX_COLLECTION_ITEMS} items]")
            return result

        if isinstance(val, dict):
            total_items = len(val)
            sanitized_dict: Dict[str, Any] = {}
            for idx, (k, v) in enumerate(val.items()):
                if idx >= MAX_COLLECTION_ITEMS:
                    sanitized_dict["__iris_truncated__"] = (
                        f"{total_items - MAX_COLLECTION_ITEMS} items omitted"
                    )
                    break
                key_str = str(k)
                if cls.is_name_sensitive(key_str):
                    sanitized_dict[key_str] = (
                        f"[REDACTED_SECRET: len={len(str(v)) if v is not None else 0}]"
                    )
                else:
                    sanitized_dict[key_str] = cls.sanitize_value(v, depth + 1)
            return sanitized_dict

        # For arbitrary objects, attempt safe repr
        try:
            repr_str = repr(val)
            return cls.sanitize_string(repr_str)
        except Exception:
            return f"<{type(val).__name__} unprintable object>"

    @classmethod
    def sanitize_locals(cls, locals_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize a frame's local variables dictionary."""
        result: Dict[str, Any] = {}
        for name, value in locals_dict.items():
            # Skip internal python dunder variables
            if name.startswith("__") and name.endswith("__"):
                continue

            if cls.is_name_sensitive(name):
                str_val = str(value) if value is not None else ""
                result[name] = f"[REDACTED_SECRET: len={len(str_val)}]"
            else:
                result[name] = cls.sanitize_value(value)
        return result
