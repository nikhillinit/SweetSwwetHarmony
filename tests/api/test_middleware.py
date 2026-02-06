"""Phase 7.2 — Tests for API middleware (exception handling + request IDs).

TDD RED: These tests should fail until api/middleware.py is implemented.
"""

import uuid
import pytest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Helpers — build a minimal FastAPI app with middleware wired in
# ---------------------------------------------------------------------------


def _make_app_with_middleware():
    """Create a FastAPI app with our middleware applied."""
    from api.middleware import ExceptionHandlerMiddleware, RequestIdMiddleware

    app = FastAPI()

    # Order matters: RequestId first (outermost), then ExceptionHandler
    app.add_middleware(ExceptionHandlerMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    async def ok_endpoint():
        return {"status": "ok"}

    @app.get("/fail")
    async def fail_endpoint():
        raise RuntimeError("something broke")

    @app.get("/value-error")
    async def value_error_endpoint():
        raise ValueError("bad value")

    @app.get("/echo-request-id")
    async def echo_request_id(request: Request):
        """Return the request ID from request state."""
        request_id = getattr(request.state, "request_id", None)
        return {"request_id": request_id}

    return app


@pytest.fixture
def app():
    return _make_app_with_middleware()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# ExceptionHandlerMiddleware Tests
# ===========================================================================


class TestExceptionHandlerMiddleware:
    """Unhandled exceptions must return clean JSON 500 without stack traces."""

    def test_unhandled_exception_returns_500_json(self, client):
        """RuntimeError in endpoint → 500 with JSON body, not HTML."""
        resp = client.get("/fail")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "Internal Server Error"
        assert "request_id" in body

    def test_no_stack_trace_in_response(self, client):
        """Stack traces must NOT leak to the client."""
        resp = client.get("/fail")
        body = resp.text
        assert "Traceback" not in body
        assert "something broke" not in body

    def test_value_error_returns_500_json(self, client):
        """Any unhandled exception type returns the same clean 500."""
        resp = client.get("/value-error")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "Internal Server Error"

    def test_normal_endpoint_unaffected(self, client):
        """Middleware does not interfere with successful responses."""
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_404_unaffected(self, client):
        """Middleware does not intercept normal HTTP errors like 404."""
        resp = client.get("/nonexistent")
        assert resp.status_code == 404


# ===========================================================================
# RequestIdMiddleware Tests
# ===========================================================================


class TestRequestIdMiddleware:
    """Every response must carry an X-Request-ID header (UUID)."""

    def test_response_has_request_id_header(self, client):
        """Successful response includes X-Request-ID."""
        resp = client.get("/ok")
        assert "x-request-id" in resp.headers
        # Must be valid UUID
        uid = uuid.UUID(resp.headers["x-request-id"])
        assert uid.version == 4

    def test_error_response_has_request_id_header(self, client):
        """Error responses also include X-Request-ID."""
        resp = client.get("/fail")
        assert "x-request-id" in resp.headers
        uid = uuid.UUID(resp.headers["x-request-id"])
        assert uid.version == 4

    def test_client_provided_request_id_is_honored(self, client):
        """If client sends X-Request-ID, middleware uses it."""
        custom_id = str(uuid.uuid4())
        resp = client.get("/ok", headers={"X-Request-ID": custom_id})
        assert resp.headers["x-request-id"] == custom_id

    def test_request_id_available_in_request_state(self, client):
        """Middleware sets request.state.request_id for downstream use."""
        resp = client.get("/echo-request-id")
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] is not None
        # Also matches the header
        assert body["request_id"] == resp.headers["x-request-id"]

    def test_each_request_gets_unique_id(self, client):
        """Two requests get different IDs."""
        r1 = client.get("/ok")
        r2 = client.get("/ok")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_error_body_includes_request_id(self, client):
        """The JSON error body should include the request_id for correlation."""
        resp = client.get("/fail")
        body = resp.json()
        assert "request_id" in body
        assert body["request_id"] == resp.headers["x-request-id"]
