"""Zero-dependency WSGI Middleware for Iris flight recording (Flask, Django)."""

from __future__ import annotations

from pathlib import Path
import uuid
from typing import Any, Callable, Dict, Optional

from iris.core.event_queue import EventQueue
from iris.core.fsm import SessionCoordinator
from iris.core.observer import IrisObserver
from iris.core.runtime import auto_attach_from_db
from iris.storage.db import arm_session, get_db_path, init_db


class IrisWSGIMiddleware:
    """WSGI middleware for on-demand request flight recording via HTTP headers.

    Usage with Flask:
        from flask import Flask
        from iris.middleware import IrisWSGIMiddleware

        app = Flask(__name__)
        app.wsgi_app = IrisWSGIMiddleware(app.wsgi_app)

    Usage with Django (wsgi.py):
        from django.core.wsgi import get_wsgi_application
        from iris.middleware import IrisWSGIMiddleware

        application = get_wsgi_application()
        application = IrisWSGIMiddleware(application)
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

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Any:
        trace_val = environ.get("HTTP_X_IRIS_TRACE", "").lower()
        should_trace = trace_val in ("true", "1", "yes", "on")

        if not should_trace:
            # Check for existing ARMED sessions
            existing_observer = auto_attach_from_db()
            try:
                return self.app(environ, start_response)
            finally:
                if existing_observer:
                    existing_observer.detach()
                    existing_observer.queue.flush()
                    existing_observer.queue.stop()

        # On-demand tracing requested
        custom_sid = environ.get("HTTP_X_IRIS_SESSION_ID")
        session_id = custom_sid or f"iris_http_{uuid.uuid4().hex[:12]}"

        target_func = (
            environ.get("HTTP_X_IRIS_TARGET")
            or self.default_target_function
            or environ.get("PATH_INFO", "handler")
        )
        target_file = environ.get("HTTP_X_IRIS_FILE") or self.default_target_file or "app"
        condition = environ.get("HTTP_X_IRIS_CONDITION")

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

        # Wrap start_response to inject X-Iris-Session-Id header
        def wrapped_start_response(status: str, response_headers: list, exc_info: Any = None) -> Any:
            response_headers.append(("X-Iris-Session-Id", session_id))
            return start_response(status, response_headers, exc_info)

        observer.attach()
        try:
            return self.app(environ, wrapped_start_response)
        finally:
            observer.detach()
            if coord.is_active():
                observer._finalize_session(coordinator=coord, truncated=False)
            queue.stop()
