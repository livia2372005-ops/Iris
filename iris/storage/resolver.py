"""Source code resolver to provide source code context for line events."""

from __future__ import annotations

import linecache
from pathlib import Path
from typing import Dict, Optional


class SourceResolver:
    """Resolves and caches source file lines for code-level agent inspection."""

    @classmethod
    def get_source(cls, file_path: str) -> Optional[str]:
        """Fetch full source content for a file."""
        if not file_path:
            return None
        try:
            lines = linecache.getlines(file_path)
            if lines:
                return "".join(lines)
            p = Path(file_path)
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return None

    @classmethod
    def resolve_line(cls, file_path: str, line_number: int) -> Optional[str]:
        """Fetch the exact trimmed source line from a file."""
        if not file_path or line_number <= 0:
            return None
        try:
            line = linecache.getline(file_path, line_number)
            return line.rstrip("\r\n") if line else None
        except Exception:
            return None

    @classmethod
    def resolve_context(
        cls,
        file_path: str,
        line_number: int,
        context_radius: int = 2,
    ) -> Dict[str, str]:
        """Fetch surrounding lines of context centered at line_number."""
        context: Dict[str, str] = {}
        start_line = max(1, line_number - context_radius)
        end_line = line_number + context_radius

        for ln in range(start_line, end_line + 1):
            line = cls.resolve_line(file_path, ln)
            if line is not None:
                context[str(ln)] = line

        return context
