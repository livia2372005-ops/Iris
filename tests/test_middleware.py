"""Tests for Feature 4: ASGI & WSGI Middlewares (FastAPI, Starlette, Flask, Django)."""

import asyncio
from pathlib import Path
import pytest

from iris.middleware import IrisASGIMiddleware, IrisWSGIMiddleware
from iris.storage.db import get_session, init_db, query_call_tree


# --- Sample Target Functions ---

def calculate_tax(subtotal: float) -> float:
    rate = 0.08
    tax = subtotal * rate
    return tax


def compute_discount(total: float) -> float:
    discount = 0.0
    if total > 100:
        discount = total * 0.15
    return discount


# --- Simulated ASGI Application ---

async def sample_asgi_app(scope, receive, send):
    subtotal = 100.0
    tax = calculate_tax(subtotal)
    body = f"Total: {subtotal + tax}".encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({"type": "http.response.body", "body": body})


# --- Simulated WSGI Application ---

def sample_wsgi_app(environ, start_response):
    total = 200.0
    disc = compute_discount(total)
    headers = [("Content-Type", "text/plain")]
    start_response("200 OK", headers)
    return [f"Discount: {disc}".encode("utf-8")]


# --- Test Cases ---

def test_asgi_middleware_flow(tmp_path):
    db_path = tmp_path / "asgi_test.db"
    init_db(db_path)

    app = IrisASGIMiddleware(sample_asgi_app, db_path=db_path)

    async def run_request(headers_list):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/checkout",
            "headers": headers_list,
        }
        sent_messages = []

        async def fake_receive():
            return {"type": "http.request"}

        async def fake_send(msg):
            sent_messages.append(msg)

        await app(scope, fake_receive, fake_send)
        return sent_messages

    # 1. Non-traced request
    res_untraced = asyncio.run(run_request([]))
    start_msg = res_untraced[0]
    resp_headers = dict(start_msg["headers"])
    assert b"x-iris-session-id" not in resp_headers

    # 2. Traced request with X-Iris-Trace and X-Iris-Target
    traced_headers = [
        (b"x-iris-trace", b"true"),
        (b"x-iris-target", b"calculate_tax"),
        (b"x-iris-file", Path(__file__).name.encode()),
    ]
    res_traced = asyncio.run(run_request(traced_headers))
    start_traced_msg = res_traced[0]
    resp_headers_traced = dict(start_traced_msg["headers"])

    assert b"x-iris-session-id" in resp_headers_traced
    sid = resp_headers_traced[b"x-iris-session-id"].decode("utf-8")
    assert sid.startswith("iris_http_")

    # Verify session recorded in SQLite
    sess = get_session(sid, db_path=db_path)
    assert sess is not None
    assert sess["state"] == "COMPLETED"

    tree = query_call_tree(sid, db_path=db_path)
    assert len(tree) >= 1
    assert tree[0]["function_name"] == "calculate_tax"


def test_wsgi_middleware_flow(tmp_path):
    db_path = tmp_path / "wsgi_test.db"
    init_db(db_path)

    app = IrisWSGIMiddleware(sample_wsgi_app, db_path=db_path)

    def run_wsgi_request(environ):
        captured_status = []
        captured_headers = []

        def fake_start_response(status, headers, exc_info=None):
            captured_status.append(status)
            captured_headers.extend(headers)

        body_iter = app(environ, fake_start_response)
        body = b"".join(body_iter)
        return captured_status[0], dict(captured_headers), body

    # 1. Non-traced request
    status, headers, body = run_wsgi_request({"PATH_INFO": "/discount"})
    assert status == "200 OK"
    assert "X-Iris-Session-Id" not in headers

    # 2. Traced request
    current_file = Path(__file__).name
    traced_environ = {
        "PATH_INFO": "/discount",
        "HTTP_X_IRIS_TRACE": "true",
        "HTTP_X_IRIS_TARGET": "compute_discount",
        "HTTP_X_IRIS_FILE": current_file,
    }
    status, headers, body = run_wsgi_request(traced_environ)
    assert status == "200 OK"
    assert "X-Iris-Session-Id" in headers
    sid = headers["X-Iris-Session-Id"]

    sess = get_session(sid, db_path=db_path)
    assert sess is not None
    assert sess["state"] == "COMPLETED"

    tree = query_call_tree(sid, db_path=db_path)
    assert len(tree) >= 1
    assert tree[0]["function_name"] == "compute_discount"
