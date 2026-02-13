"""
Tests for GET /health/activation-readiness and /health/detailed activation component.

7 core tests + 1 timeout safety test (added in M4.5).
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.routers.health import router as health_router, get_store
from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def app(store):
    application = FastAPI()
    application.state.store = store

    # Override dependency to use our test store
    async def _override_store():
        return store

    application.dependency_overrides[get_store] = _override_store
    application.include_router(health_router)
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _insert_canary_run(store, verdict="pass", pass_rate=1.0, created_at=None):
    """Insert a canary_runs row for testing."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    db = store._db
    run_id = f"run-api-{verdict}-{pass_rate}"
    await db.execute(
        "INSERT OR IGNORE INTO run_history (id, run_type, status, started_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, "canary", "completed", created_at, created_at),
    )
    await db.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored, passed, failed,
            skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id, 10, "abc123", 10,
            int(pass_rate * 10), int((1 - pass_rate) * 10), 0,
            pass_rate, verdict, created_at,
        ),
    )
    await db.commit()


# =============================================================================
# TESTS
# =============================================================================

class TestActivationReadinessAPI:
    @pytest.mark.asyncio
    async def test_endpoint_returns_200(self, client):
        """GET /health/activation-readiness returns 200."""
        resp = await client.get("/health/activation-readiness")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_shape(self, client):
        """Response contains all expected fields."""
        resp = await client.get("/health/activation-readiness")
        data = resp.json()
        assert "verdict" in data
        assert "can_proceed" in data
        assert "step" in data
        assert "reasons" in data
        assert "canary" in data
        assert "alerts" in data
        assert "checked_at" in data

    @pytest.mark.asyncio
    async def test_no_canary_data_step1_returns_warn(self, client):
        """No canary data + step 1 -> warn (shadow lenient)."""
        resp = await client.get("/health/activation-readiness?step=1")
        data = resp.json()
        assert data["verdict"] == "warn"
        assert data["can_proceed"] is True

    @pytest.mark.asyncio
    async def test_passing_canary_returns_ready(self, client, store):
        """Passing canary + no alerts -> ready."""
        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)
        resp = await client.get("/health/activation-readiness?step=1")
        data = resp.json()
        assert data["verdict"] == "ready"
        assert data["can_proceed"] is True

    @pytest.mark.asyncio
    async def test_failing_canary_returns_blocked(self, client, store):
        """Failing canary -> blocked."""
        await _insert_canary_run(store, verdict="fail", pass_rate=0.3)
        resp = await client.get("/health/activation-readiness?step=1")
        data = resp.json()
        assert data["verdict"] == "blocked"
        assert data["can_proceed"] is False

    @pytest.mark.asyncio
    async def test_step_query_param_validation(self, client):
        """step=3 is reflected; step=5 -> 422."""
        resp = await client.get("/health/activation-readiness?step=3")
        assert resp.status_code == 200
        assert resp.json()["step"] == 3

        resp = await client.get("/health/activation-readiness?step=5")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_detailed_health_includes_activation_component(self, client):
        """GET /health/detailed includes activation_readiness in components."""
        resp = await client.get("/health/detailed")
        assert resp.status_code == 200
        data = resp.json()
        component_names = [c["name"] for c in data["components"]]
        assert "activation_readiness" in component_names

    @pytest.mark.asyncio
    async def test_detailed_health_timeout_does_not_break_response(self, client):
        """Gate timeout -> /health/detailed still returns 200 with status=unknown."""
        async def _slow_gate(*args, **kwargs):
            await asyncio.sleep(5)

        with patch(
            "monitoring.activation_gate.check_activation_readiness",
            side_effect=_slow_gate,
        ):
            resp = await client.get("/health/detailed")
        assert resp.status_code == 200
        data = resp.json()
        activation = next(
            (c for c in data["components"] if c["name"] == "activation_readiness"),
            None,
        )
        assert activation is not None
        assert activation["status"] == "unknown"
        assert "timed out" in activation["message"]
