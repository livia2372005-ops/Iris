"""Zero-overhead line-level AST causality extractor with in-memory caching."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from iris.storage.resolver import SourceResolver


@dataclass
class LineCausalInfo:
    """Represents variable read/write causality and operation for a source code line."""
    line_number: int
    read_vars: Set[str] = field(default_factory=set)
    written_vars: Set[str] = field(default_factory=set)
    operation: str = "assign"
    boundary_type: Optional[str] = None
    boundary_target: Optional[str] = None
    boundary_notice: Optional[str] = None


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
            elif isinstance(node, ast.Call):
                boundary = cls._detect_boundary(node.func)
                if boundary:
                    b_type, b_target, b_notice = boundary
                    for l in range(lineno, end_lineno + 1):
                        if l in line_map:
                            line_map[l].boundary_type = b_type
                            line_map[l].boundary_target = b_target
                            line_map[l].boundary_notice = b_notice

        if norm_path:
            cls._cache[norm_path] = line_map

        return line_map

    @staticmethod
    def _resolve_dotted_name(node: ast.AST) -> str:
        """Extract full dotted attribute name (e.g. client.chat.completions.create)."""
        parts = []
        curr = node
        while isinstance(curr, ast.Attribute):
            parts.append(curr.attr)
            curr = curr.value
        if isinstance(curr, ast.Name):
            parts.append(curr.id)
        return ".".join(reversed(parts))

    @classmethod
    def _detect_boundary(cls, func_node: ast.AST) -> Optional[Tuple[str, str, str]]:
        """Identify if a function call targets an external terminal boundary."""
        dotted = cls._resolve_dotted_name(func_node)
        if not dotted:
            return None

        lower = dotted.lower()

        # 1. LLM API Boundary
        llm_providers = ("openai", "anthropic", "genai", "generativeai", "langchain", "litellm", "cohere", "groq", "mistralai", "ollama")
        llm_methods = ("completions.create", "messages.create", "generate_content", "ainvoke", "invoke", "chat_completion")
        if any(prov in lower for prov in llm_providers) or any(meth in lower for meth in llm_methods):
            return (
                "LLM_API",
                dotted,
                "Terminal boundary reached: Invocation of external LLM API service. Execution leaves CPython runtime into external provider network."
            )

        # 2. HTTP Network Boundary
        http_modules = ("requests.", "httpx.", "aiohttp.", "urllib.request.", "urllib3.")
        http_methods = ("requests.get", "requests.post", "requests.put", "requests.delete", "requests.patch", "requests.request",
                        "httpx.get", "httpx.post", "httpx.put", "httpx.delete", "httpx.patch", "httpx.request",
                        "urllib.request.urlopen")
        if lower.startswith(http_modules) or any(lower == hm for hm in http_methods):
            return (
                "HTTP_NETWORK",
                dotted,
                "Terminal boundary reached: Outbound HTTP network request. Execution waits on external remote endpoint."
            )

        # 3. Database IO Boundary
        db_modules = ("sqlite3.", "psycopg.", "psycopg2.", "asyncpg.", "pymongo.", "redis.")
        db_methods = (".execute", ".executemany", ".fetchall", ".fetchone", ".commit", "session.query", "session.execute")
        if lower.startswith(db_modules) or any(lower.endswith(dm) or dm in lower for dm in db_methods):
            return (
                "DATABASE_IO",
                dotted,
                "Terminal boundary reached: Database storage I/O operation. Execution enters database driver engine."
            )

        # 4. External OS Process Boundary
        proc_modules = ("subprocess.run", "subprocess.popen", "subprocess.check_output", "subprocess.call", "os.system", "os.popen")
        if any(lower.startswith(pm) for pm in proc_modules):
            return (
                "EXTERNAL_PROCESS",
                dotted,
                "Terminal boundary reached: Spawning external OS subprocess. Execution controlled by operating system process."
            )

        return None

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
