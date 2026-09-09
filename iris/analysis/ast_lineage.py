"""Zero-overhead line-level AST causality extractor with in-memory caching."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

from iris.storage.resolver import SourceResolver


@dataclass
class LineCausalInfo:
    """Represents variable read/write causality and operation for a source code line."""
    line_number: int
    read_vars: Set[str] = field(default_factory=set)
    written_vars: Set[str] = field(default_factory=set)
    operation: str = "assign"


class ASTLineageAnalyzer:
    """Pre-parses and caches AST statement dependencies for zero-overhead runtime lookup."""

    _cache: Dict[str, Dict[int, LineCausalInfo]] = {}

    @classmethod
    def get_line_info(cls, file_path: str, line_number: int) -> Optional[LineCausalInfo]:
        """Retrieve causal info for a specific line, analyzing file if not already cached."""
        if not file_path:
            return None

        norm_path = str(Path(file_path).resolve())
        if norm_path not in cls._cache:
            source = SourceResolver.get_source(norm_path)
            if not source:
                # Try direct file read
                try:
                    p = Path(norm_path)
                    if p.exists() and p.is_file():
                        source = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            if source:
                cls.analyze_source(source, norm_path)
            else:
                cls._cache[norm_path] = {}

        return cls._cache.get(norm_path, {}).get(line_number)

    @classmethod
    def analyze_source(cls, source_code: str, file_path: str = "") -> Dict[int, LineCausalInfo]:
        """Parse source code AST and index read/write dependencies per line."""
        norm_path = str(Path(file_path).resolve()) if file_path else ""
        if norm_path and norm_path in cls._cache:
            return cls._cache[norm_path]

        line_map: Dict[int, LineCausalInfo] = {}

        try:
            tree = ast.parse(source_code)
        except Exception:
            if norm_path:
                cls._cache[norm_path] = {}
            return line_map

        for node in ast.walk(tree):
            if not hasattr(node, "lineno"):
                continue

            lineno = node.lineno
            end_lineno = getattr(node, "end_lineno", lineno) or lineno

            # Ensure all spanning lines have an entry
            for l in range(lineno, end_lineno + 1):
                if l not in line_map:
                    line_map[l] = LineCausalInfo(line_number=l)

            # Record Load names
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                for l in range(lineno, end_lineno + 1):
                    line_map[l].read_vars.add(node.id)

            # Record Store names
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                for l in range(lineno, end_lineno + 1):
                    line_map[l].written_vars.add(node.id)

            # Statement-level operations
            if isinstance(node, ast.Assign):
                op = cls._determine_op(node.value)
                for l in range(lineno, end_lineno + 1):
                    line_map[l].operation = op
            elif isinstance(node, ast.AugAssign):
                op = f"aug:{node.op.__class__.__name__.lower()}"
                for l in range(lineno, end_lineno + 1):
                    line_map[l].operation = op
                    if isinstance(node.target, ast.Name):
                        line_map[l].read_vars.add(node.target.id)
                        line_map[l].written_vars.add(node.target.id)
            elif isinstance(node, ast.Return) and node.value:
                for l in range(lineno, end_lineno + 1):
                    line_map[l].operation = "return"

        if norm_path:
            cls._cache[norm_path] = line_map

        return line_map

    @staticmethod
    def _determine_op(val_node: ast.AST) -> str:
        """Derive a human-readable operation tag from AST value node."""
        if isinstance(val_node, ast.Call):
            if isinstance(val_node.func, ast.Name):
                return f"call:{val_node.func.id}"
            elif isinstance(val_node.func, ast.Attribute):
                return f"call:{val_node.func.attr}"
            return "call"
        elif isinstance(val_node, ast.BinOp):
            return f"binop:{val_node.op.__class__.__name__.lower()}"
        elif isinstance(val_node, ast.UnaryOp):
            return f"unary:{val_node.op.__class__.__name__.lower()}"
        elif isinstance(val_node, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
            return f"literal:{val_node.__class__.__name__.lower()}"
        elif isinstance(val_node, ast.Subscript):
            return "subscript"
        elif isinstance(val_node, ast.Constant):
            return "constant"
        return "assign"

    @classmethod
    def clear_cache(cls) -> None:
        """Reset cached AST lookups (useful for unit tests)."""
        cls._cache.clear()
