"""Zero-dependency ASGI Middleware for Iris flight recording (FastAPI, Starlette, Litestar)."""

from __future__ import annotations

from pathlib import Path
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator
from iris.core.observer import IrisObserver
from iris.core.runtime import auto_attach_from_db
from iris.storage.db import arm_session, get_db_path, init_db


class IrisASGIMiddleware:
    """ASGI 3 middleware for on-demand request flight recording via HTTP headers.

    Usage with FastAPI / Starlette:
        from fastapi import FastAPI
        from iris.middleware import IrisASGIMiddleware

        app = FastAPI()
        app.add_middleware(IrisASGIMiddleware)

    Triggering via HTTP:
        curl -X GET http://localhost:8000/api/users/42 \\
             -H "X-Iris-Trace: true" \\
             -H "X-Iris-Target: get_user_profile"
    """

    def __init__(
        self,
        app: Any,
        default_target_function: Optional[str] = None,
        default_target_file: Optional[str] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.app = app
        self.default_target_function = default_target_function
        self.default_target_file = default_target_file
        self.db_path = db_path or get_db_path()
        init_db(self.db_path)

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Extract headers from scope (headers is a list of 2-item byte tuples)
        headers_dict = self._extract_headers(scope.get("headers", []))
        trace_header = headers_dict.get("x-iris-trace", "").lower()
        should_trace = trace_header in ("true", "1", "yes", "on")

        if not should_trace:
            # Check if there are existing ARMED sessions from MCP iris_arm_entry
            existing_observer = auto_attach_from_db()
            try:
                await self.app(scope, receive, send)
            finally:
                if existing_observer:
                    existing_observer.detach()
                    existing_observer.queue.flush()
                    existing_observer.queue.stop()
            return

        # On-demand tracing requested via headers
        custom_sid = headers_dict.get("x-iris-session-id")
        session_id = custom_sid or f"iris_http_{uuid.uuid4().hex[:12]}"

        target_func = headers_dict.get("x-iris-target") or self.default_target_function or scope.get("path", "handler")
        target_file = headers_dict.get("x-iris-file") or self.default_target_file or "app"
        condition = headers_dict.get("x-iris-condition")

        # Arm session in SQLite
        arm_session(
            session_id=session_id,
            entry_file=target_file,
            entry_function=target_func,
            condition=condition,
            db_path=self.db_path,
        )

        coord = SessionCoordinator(
            session_id=session_id,
            entry_file=target_file,
            entry_function=target_func,
            condition=condition,
        )
        queue = EventQueue(db_path=self.db_path)
        observer = IrisObserver(coordinator=coord, event_queue=queue)

        # Wrap send to inject X-Iris-Session-Id response header
        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                resp_headers = list(message.get("headers", []))
                resp_headers.append((b"x-iris-session-id", session_id.encode("utf-8")))
                message = dict(message)
                message["headers"] = resp_headers
            await send(message)

        observer.attach()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            observer.detach()
            if coord.is_active():
                observer._finalize_session(coordinator=coord, truncated=False)
            queue.stop()

    @staticmethod
    def _extract_headers(raw_headers: List[Tuple[bytes, bytes]]) -> Dict[str, str]:
        """Convert ASGI byte-tuple headers into lowercased string dictionary."""
        out = {}
        for k, v in raw_headers:
            try:
                out[k.decode("latin1").lower()] = v.decode("latin1")
            except Exception:
                pass
        return out
