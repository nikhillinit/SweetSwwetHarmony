"""Phase 7.4 — Tests for health endpoint hardening.

Tests cover:
1. Store singleton via app.state (no new SignalStore per request)
2. TTL cache on health endpoints
3. Query parameter bounds (hours, limit)
"""

import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi import FastAPI, Request, Query
from fastapi.testclient import TestClient


# ===========================================================================
# TTL Cache Tests
# ===========================================================================


class TestTTLCache:
    """In-memory TTL cache decorator for health endpoints."""

    def test_cache_returns_same_result_within_ttl(self):
        """Cached result is reused within TTL window."""
        from api.health_cache import ttl_cache

        call_count = 0

        @ttl_cache(ttl_seconds=1.0)
        def expensive():
            nonlocal call_count
            call_count += 1
            return {"data": call_count}

        r1 = expensive()
        r2 = expensive()
        assert r1 == r2
        assert call_count == 1  # Only called once

    def test_cache_expires_after_ttl(self):
        """Cache expires and re-calls after TTL."""
        from api.health_cache import ttl_cache

        call_count = 0

        @ttl_cache(ttl_seconds=0.1)
        def expensive():
            nonlocal call_count
            call_count += 1
            return {"data": call_count}

        r1 = expensive()
        time.sleep(0.15)
        r2 = expensive()
        assert r1 != r2
        assert call_count == 2

    def test_cache_with_different_args(self):
        """Different arguments produce different cache entries."""
        from api.health_cache import ttl_cache

        @ttl_cache(ttl_seconds=1.0)
        def fetch(key: str):
            return {"key": key, "time": time.monotonic()}

        r1 = fetch("a")
        r2 = fetch("b")
        assert r1["key"] == "a"
        assert r2["key"] == "b"

    def test_cache_clear(self):
        """Cache can be manually cleared."""
        from api.health_cache import ttl_cache

        call_count = 0

        @ttl_cache(ttl_seconds=60.0)
        def expensive():
            nonlocal call_count
            call_count += 1
            return call_count

        expensive()
        assert call_count == 1

        expensive.cache_clear()
        expensive()
        assert call_count == 2


# ===========================================================================
# Query Parameter Bounds Tests
# ===========================================================================


class TestQueryParameterBounds:
    """Health endpoints must reject out-of-range query parameters."""

    @pytest.fixture
    def bounded_app(self):
        """Create test app with bounded endpoints."""
        from api.health_bounds import BoundedParams

        app = FastAPI()

        @app.get("/test/history")
        async def test_history(
            hours: int = BoundedParams.hours(),
            limit: int = BoundedParams.limit(),
        ):
            return {"hours": hours, "limit": limit}

        @app.get("/test/metrics")
        async def test_metrics(
            window_hours: int = BoundedParams.window_hours(),
            history_days: int = BoundedParams.history_days(),
        ):
            return {"window_hours": window_hours, "history_days": history_days}

        return app

    @pytest.fixture
    def client(self, bounded_app):
        return TestClient(bounded_app)

    def test_default_values(self, client):
        resp = client.get("/test/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["hours"] == 24
        assert body["limit"] == 100

    def test_valid_custom_values(self, client):
        resp = client.get("/test/history?hours=48&limit=500")
        assert resp.status_code == 200
        body = resp.json()
        assert body["hours"] == 48
        assert body["limit"] == 500

    def test_hours_too_large(self, client):
        """hours > 720 (30 days) returns 422."""
        resp = client.get("/test/history?hours=9999")
        assert resp.status_code == 422

    def test_hours_too_small(self, client):
        """hours < 1 returns 422."""
        resp = client.get("/test/history?hours=0")
        assert resp.status_code == 422

    def test_limit_too_large(self, client):
        """limit > 1000 returns 422."""
        resp = client.get("/test/history?limit=5000")
        assert resp.status_code == 422

    def test_limit_too_small(self, client):
        """limit < 1 returns 422."""
        resp = client.get("/test/history?limit=0")
        assert resp.status_code == 422

    def test_window_hours_bounded(self, client):
        resp = client.get("/test/metrics?window_hours=800")
        assert resp.status_code == 422

    def test_history_days_bounded(self, client):
        resp = client.get("/test/metrics?history_days=400")
        assert resp.status_code == 422

    def test_history_days_max_365(self, client):
        """history_days max is 365."""
        resp = client.get("/test/metrics?history_days=365")
        assert resp.status_code == 200
        assert resp.json()["history_days"] == 365
