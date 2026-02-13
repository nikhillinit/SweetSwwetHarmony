"""M2.4 — Tests for API rate limiting middleware.

Tests cover:
1. Exceeding limit returns 429 with correct error envelope
2. Retry-After header present on 429 responses
3. X-RateLimit-Remaining header present on all responses
4. Health endpoints are exempt from rate limiting
5. Different IPs have independent limits
6. Write endpoints have stricter limits than read endpoints
7. _RateTracker unit tests
"""

import pytest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.middleware import (
    ExceptionHandlerMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    _RateTracker,
    _tracker,
    RATE_LIMIT_DEFAULT,
    RATE_LIMIT_WRITE,
    WINDOW_SECONDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_tracker():
    """Reset the global rate tracker between tests."""
    _tracker.reset()
    yield
    _tracker.reset()


@pytest.fixture
def app():
    """Minimal FastAPI app with rate limiting middleware."""
    test_app = FastAPI()
    test_app.add_middleware(ExceptionHandlerMiddleware)
    test_app.add_middleware(RateLimitMiddleware)
    test_app.add_middleware(RequestIdMiddleware)

    @test_app.get("/health")
    async def health():
        return {"status": "healthy"}

    @test_app.get("/api/v1/health/detailed")
    async def health_detailed():
        return {"status": "healthy", "components": []}

    @test_app.get("/api/v1/companies")
    async def list_companies():
        return {"companies": []}

    @test_app.post("/api/v1/triage/approve")
    async def triage_approve():
        return {"ok": True}

    @test_app.post("/api/v1/batches")
    async def create_batch():
        return {"batch_id": "test"}

    @test_app.get("/")
    async def root():
        return {"name": "Discovery Engine"}

    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# _RateTracker unit tests
# ---------------------------------------------------------------------------


class TestRateTracker:
    """Unit tests for the sliding window rate tracker."""

    def test_allows_within_limit(self):
        tracker = _RateTracker()
        for i in range(5):
            allowed, remaining = tracker.check_and_record("test", 5, now=100.0 + i * 0.01)
            if i < 5:
                assert allowed is True

    def test_blocks_at_limit(self):
        tracker = _RateTracker()
        now = 100.0
        for i in range(3):
            tracker.check_and_record("test", 3, now=now + i * 0.01)
        allowed, remaining = tracker.check_and_record("test", 3, now=now + 0.03)
        assert allowed is False
        assert remaining == 0

    def test_remaining_decrements(self):
        tracker = _RateTracker()
        _, r1 = tracker.check_and_record("test", 5, now=100.0)
        _, r2 = tracker.check_and_record("test", 5, now=100.01)
        _, r3 = tracker.check_and_record("test", 5, now=100.02)
        assert r1 == 4
        assert r2 == 3
        assert r3 == 2

    def test_window_expiry_allows_new_requests(self):
        tracker = _RateTracker()
        now = 100.0
        for i in range(3):
            tracker.check_and_record("test", 3, now=now + i * 0.01)

        # Should be blocked now
        allowed, _ = tracker.check_and_record("test", 3, now=now + 0.03)
        assert allowed is False

        # After window expires, should be allowed again
        allowed, remaining = tracker.check_and_record("test", 3, now=now + WINDOW_SECONDS + 1)
        assert allowed is True
        assert remaining == 2

    def test_different_keys_independent(self):
        tracker = _RateTracker()
        now = 100.0
        for i in range(3):
            tracker.check_and_record("ip_a:default", 3, now=now + i * 0.01)

        # ip_a is exhausted
        allowed_a, _ = tracker.check_and_record("ip_a:default", 3, now=now + 0.03)
        assert allowed_a is False

        # ip_b should still be allowed
        allowed_b, remaining_b = tracker.check_and_record("ip_b:default", 3, now=now + 0.04)
        assert allowed_b is True
        assert remaining_b == 2

    def test_reset_clears_state(self):
        tracker = _RateTracker()
        for i in range(5):
            tracker.check_and_record("test", 5, now=100.0 + i * 0.01)
        allowed, _ = tracker.check_and_record("test", 5, now=100.05)
        assert allowed is False

        tracker.reset()
        allowed, remaining = tracker.check_and_record("test", 5, now=100.06)
        assert allowed is True
        assert remaining == 4


# ---------------------------------------------------------------------------
# 429 response tests
# ---------------------------------------------------------------------------


class TestRateLimitExceeded:
    """Verify 429 responses when rate limit is exceeded."""

    def test_returns_429_when_limit_exceeded(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 2):
            client.get("/api/v1/companies")
            client.get("/api/v1/companies")
            resp = client.get("/api/v1/companies")
            assert resp.status_code == 429

    def test_429_has_correct_error_envelope(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 1):
            client.get("/api/v1/companies")
            resp = client.get("/api/v1/companies")
            body = resp.json()
            assert body["error"] == "rate_limited"
            assert body["code"] == "RATE_LIMIT_EXCEEDED"
            assert "message" in body
            assert "request_id" in body

    def test_429_has_retry_after_header(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 1):
            client.get("/api/v1/companies")
            resp = client.get("/api/v1/companies")
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
            assert resp.headers["Retry-After"] == str(WINDOW_SECONDS)

    def test_429_has_ratelimit_headers(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 1):
            client.get("/api/v1/companies")
            resp = client.get("/api/v1/companies")
            assert resp.headers["X-RateLimit-Limit"] == "1"
            assert resp.headers["X-RateLimit-Remaining"] == "0"
            assert "X-RateLimit-Reset" in resp.headers


# ---------------------------------------------------------------------------
# X-RateLimit headers on normal responses
# ---------------------------------------------------------------------------


class TestRateLimitHeaders:
    """Verify X-RateLimit-* headers are present on normal (non-429) responses."""

    def test_ratelimit_headers_present(self, client):
        resp = client.get("/api/v1/companies")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers

    def test_remaining_decrements_with_requests(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 10):
            r1 = client.get("/api/v1/companies")
            r2 = client.get("/api/v1/companies")
            rem1 = int(r1.headers["X-RateLimit-Remaining"])
            rem2 = int(r2.headers["X-RateLimit-Remaining"])
            assert rem2 < rem1

    def test_limit_header_matches_config(self, client):
        resp = client.get("/api/v1/companies")
        assert resp.headers["X-RateLimit-Limit"] == str(RATE_LIMIT_DEFAULT)


# ---------------------------------------------------------------------------
# Health endpoint exemption tests
# ---------------------------------------------------------------------------


class TestHealthExemption:
    """Verify health endpoints are exempt from rate limiting."""

    def test_root_health_exempt(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 1):
            # Exhaust the default limit
            client.get("/api/v1/companies")
            client.get("/api/v1/companies")

            # Health should still work
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_detailed_health_exempt(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 1):
            client.get("/api/v1/companies")
            client.get("/api/v1/companies")

            resp = client.get("/api/v1/health/detailed")
            assert resp.status_code == 200

    def test_health_has_no_ratelimit_headers(self, client):
        resp = client.get("/health")
        assert "X-RateLimit-Limit" not in resp.headers
        assert "X-RateLimit-Remaining" not in resp.headers

    def test_root_endpoint_exempt(self, client):
        with patch("api.middleware.RATE_LIMIT_DEFAULT", 1):
            client.get("/api/v1/companies")
            client.get("/api/v1/companies")

            resp = client.get("/")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Write endpoint tier tests
# ---------------------------------------------------------------------------


class TestWriteTier:
    """Verify write endpoints have stricter rate limits."""

    def test_write_endpoint_uses_write_limit(self, client):
        resp = client.post("/api/v1/triage/approve")
        assert resp.headers["X-RateLimit-Limit"] == str(RATE_LIMIT_WRITE)

    def test_batch_endpoint_uses_write_limit(self, client):
        resp = client.post("/api/v1/batches")
        assert resp.headers["X-RateLimit-Limit"] == str(RATE_LIMIT_WRITE)

    def test_read_endpoint_uses_default_limit(self, client):
        resp = client.get("/api/v1/companies")
        assert resp.headers["X-RateLimit-Limit"] == str(RATE_LIMIT_DEFAULT)

    def test_write_limit_is_independent_of_default(self, client):
        # Exhaust write limit
        with patch("api.middleware.RATE_LIMIT_WRITE", 2):
            client.post("/api/v1/triage/approve")
            client.post("/api/v1/triage/approve")
            resp_write = client.post("/api/v1/triage/approve")
            assert resp_write.status_code == 429

            # Default tier should still work
            resp_read = client.get("/api/v1/companies")
            assert resp_read.status_code == 200


# ---------------------------------------------------------------------------
# Independent IP tests
# ---------------------------------------------------------------------------


class TestIndependentIPs:
    """Verify different client IPs have independent rate limits."""

    def test_different_ips_independent(self, app):
        """Two clients with different source IPs have separate limits."""
        # We can't easily simulate different IPs with TestClient,
        # so test via the _RateTracker directly
        tracker = _RateTracker()
        now = 100.0

        # Exhaust ip_a
        for i in range(3):
            tracker.check_and_record("10.0.0.1:default", 3, now=now + i * 0.01)
        allowed_a, _ = tracker.check_and_record("10.0.0.1:default", 3, now=now + 0.03)
        assert allowed_a is False

        # ip_b should still be allowed
        allowed_b, _ = tracker.check_and_record("10.0.0.2:default", 3, now=now + 0.04)
        assert allowed_b is True
