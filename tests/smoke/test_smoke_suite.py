"""
G0 Smoke Suite -- Baseline health checks for the Discovery Engine.

Verifies that core API and utility subsystems start and respond correctly.
These tests are intentionally lightweight and non-mutating:
- API startup + /health
- Batch list (dry-run read, no Notion writes)
- Drift alerts list (read-only)
- Config validation callable

Run: pytest tests/smoke/ -v
"""

import asyncio
import os
import sys
import tempfile

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.auth.jwt_auth import Role, create_access_token
from api.routers import batch as batch_mod
from api.routers import canary as canary_mod
from storage.signal_store import SignalStore


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role = Role.GP) -> dict:
    token, _ = create_access_token(
        user_id="smoke-test", email="smoke@test.com", role=role, name="Smoke",
    )
    return {"Authorization": f"Bearer {token}"}


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
    """Minimal FastAPI app with core routers for smoke testing."""
    application = FastAPI()
    application.state.store = store
    application.state.write_lock = asyncio.Lock()

    @application.get("/health")
    async def health_check():
        try:
            stats = await application.state.store.get_stats()
            return {
                "status": "healthy",
                "database": "connected",
                "total_signals": stats.get("total_signals", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    application.include_router(batch_mod.router, prefix="/api/v1")
    application.include_router(canary_mod.router, prefix="/api/v1")
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as c:
        yield c


# =============================================================================
# 1. API STARTUP + HEALTH
# =============================================================================

class TestHealthSmoke:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_reports_healthy(self, client):
        data = (await client.get("/health")).json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

    @pytest.mark.asyncio
    async def test_health_includes_signal_count(self, client):
        data = (await client.get("/health")).json()
        assert "total_signals" in data
        assert isinstance(data["total_signals"], int)


# =============================================================================
# 2. BATCH LIST (DRY-RUN, READ-ONLY)
# =============================================================================

class TestBatchSmoke:
    @pytest.mark.asyncio
    async def test_batch_list_returns_200(self, client):
        resp = await client.get(
            "/api/v1/batches", headers=_auth_header(),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_batch_list_empty_on_fresh_db(self, client):
        resp = await client.get(
            "/api/v1/batches", headers=_auth_header(),
        )
        data = resp.json()
        assert data["data"] == []


# =============================================================================
# 3. DRIFT ALERTS (READ-ONLY)
# =============================================================================

class TestDriftSmoke:
    @pytest.mark.asyncio
    async def test_drift_alerts_returns_200(self, client):
        resp = await client.get(
            "/api/v1/canary/drift-alerts", headers=_auth_header(),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_drift_alerts_empty_on_fresh_db(self, client):
        resp = await client.get(
            "/api/v1/canary/drift-alerts", headers=_auth_header(),
        )
        data = resp.json()
        assert data["data"] == []


# =============================================================================
# 4. CONFIG VALIDATION
# =============================================================================

class TestConfigValidationSmoke:
    def test_validate_config_is_callable(self):
        from utils.config_validator import validate_config
        issues = validate_config()
        assert isinstance(issues, list)

    def test_config_issues_have_required_fields(self):
        from utils.config_validator import validate_config
        issues = validate_config()
        for issue in issues:
            assert hasattr(issue, "level")
            assert hasattr(issue, "key")
            assert hasattr(issue, "message")
            assert issue.level in ("error", "warning", "info")

    def test_print_config_report_returns_bool(self, capsys):
        from utils.config_validator import validate_config, print_config_report
        issues = validate_config()
        result = print_config_report(issues)
        assert isinstance(result, bool)
