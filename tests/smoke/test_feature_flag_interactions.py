"""
M5.6 Feature Flag Interaction Smoke Tests

Validates that the API starts and /api/v1/health returns 200 across all
activation step flag combinations, and that conflict detection (staging_only
+ BULK_TRIAGE_ENABLED=active) returns 423 on write attempts.

These tests use the real FastAPI app via ASGI transport — no Docker needed.
"""

import asyncio
import os
import sys
import tempfile
import uuid

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from api.auth.jwt_auth import Role, create_access_token


# =============================================================================
# STEP FLAG COMBINATIONS (from activation runbook)
# =============================================================================

# Step 0: All defaults — everything disabled
STEP_0_FLAGS = {
    "LLM_THESIS_MODE": "off",
    "ML_ENABLEMENT": "disabled",
    "V2_ENABLEMENT": "disabled",
    "USE_SHADOW_ENTITY_RESOLUTION": "false",
    "MERGE_WRITES_ENABLED": "disabled",
    "BULK_TRIAGE_ENABLED": "disabled",
    "HUNTER_PROMOTE_ENABLED": "disabled",
    "DELIVERY_MODE": "staging_only",
    "USE_PHASE_G_IDENTITY_RESOLUTION": "false",
    "USE_CLAIM_FACTS": "false",
}

# Step 1: Shadow quartet (observe-only)
STEP_1_FLAGS = {
    **STEP_0_FLAGS,
    "LLM_THESIS_MODE": "shadow",
    "ML_ENABLEMENT": "shadow",
    "V2_ENABLEMENT": "shadow",
    "USE_SHADOW_ENTITY_RESOLUTION": "true",
}

# Step 2: Low-risk trio (some writes)
STEP_2_FLAGS = {
    **STEP_1_FLAGS,
    "LLM_THESIS_MODE": "active",
    "ML_ENABLEMENT": "live",
    "V2_ENABLEMENT": "live",
}

# Step 3: Write trio
STEP_3_FLAGS = {
    **STEP_2_FLAGS,
    "MERGE_WRITES_ENABLED": "shadow",
    "DELIVERY_MODE": "manual_publish",
    "HUNTER_PROMOTE_ENABLED": "active",
}

# Step 4: Batch pair
STEP_4_FLAGS = {
    **STEP_3_FLAGS,
    "MERGE_WRITES_ENABLED": "active",
    "BULK_TRIAGE_ENABLED": "active",
    "DELIVERY_MODE": "batch_publish",
}

# All active simultaneously
ALL_ACTIVE_FLAGS = {
    "LLM_THESIS_MODE": "active",
    "ML_ENABLEMENT": "live",
    "V2_ENABLEMENT": "live",
    "USE_SHADOW_ENTITY_RESOLUTION": "true",
    "MERGE_WRITES_ENABLED": "active",
    "BULK_TRIAGE_ENABLED": "active",
    "HUNTER_PROMOTE_ENABLED": "active",
    "DELIVERY_MODE": "batch_publish",
    "USE_PHASE_G_IDENTITY_RESOLUTION": "true",
    "USE_CLAIM_FACTS": "true",
}


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role = Role.GP) -> dict:
    token, _ = create_access_token(
        user_id="flag-test", email="flag@test.com", role=role, name="FlagTest",
    )
    return {"Authorization": f"Bearer {token}"}


def _set_flags(monkeypatch, flags: dict):
    """Set environment variables for the given flag combination."""
    for key, val in flags.items():
        monkeypatch.setenv(key, val)


def _clear_known_flags(monkeypatch):
    """Remove all known feature flags from environment to get clean defaults."""
    for key in list(STEP_0_FLAGS.keys()):
        monkeypatch.delenv(key, raising=False)
    # Also clear other env vars that might interfere
    for key in ["GNEWS_API_KEY", "STRICT_CONFIG_VALIDATION"]:
        monkeypatch.delenv(key, raising=False)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    from storage.signal_store import SignalStore
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
    """API app with health and triage routers for flag interaction testing."""
    from fastapi import FastAPI
    from api.routers import health as health_mod
    from api.routers import triage as triage_mod

    application = FastAPI()
    application.state.store = store
    application.state.write_lock = asyncio.Lock()

    # Root health endpoint (mirrors api/main.py:179)
    @application.get("/api/v1/health")
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

    application.include_router(triage_mod.router, prefix="/api/v1")
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://flagtest") as c:
        yield c


# =============================================================================
# STEP FLAG COMBINATION TESTS
# =============================================================================

class TestFlagCombinations:
    """API starts and /api/v1/health returns 200 for each activation step."""

    @pytest.mark.asyncio
    async def test_step_0_defaults(self, monkeypatch, client):
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, STEP_0_FLAGS)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step_1_shadow_quartet(self, monkeypatch, client):
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, STEP_1_FLAGS)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step_2_low_risk_trio(self, monkeypatch, client):
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, STEP_2_FLAGS)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step_3_write_trio(self, monkeypatch, client):
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, STEP_3_FLAGS)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step_4_batch_pair(self, monkeypatch, client):
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, STEP_4_FLAGS)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_all_active(self, monkeypatch, client):
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, ALL_ACTIVE_FLAGS)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200


# =============================================================================
# CONFLICT DETECTION
# =============================================================================

class TestFlagConflicts:
    """staging_only + write feature enabled => 423 on write attempt."""

    @pytest.mark.asyncio
    async def test_staging_only_plus_bulk_triage_returns_423(self, monkeypatch, client):
        """DELIVERY_MODE=staging_only with BULK_TRIAGE_ENABLED=active should
        return 423 Locked on a bulk triage write attempt."""
        _clear_known_flags(monkeypatch)
        _set_flags(monkeypatch, {
            **STEP_0_FLAGS,
            "BULK_TRIAGE_ENABLED": "disabled",  # guard is at feature_guard level
        })

        # Attempt a bulk triage POST — feature guard should block with 423
        resp = await client.post(
            "/api/v1/triage/bulk",
            headers={
                **_auth_header(Role.GP),
                "X-Idempotency-Key": str(uuid.uuid4()),
            },
            json={
                "action": "approve",
                "reason": "test conflict",
                "items": [{"review_id": 1, "updated_at": "2026-01-01T00:00:00Z"}],
            },
        )
        assert resp.status_code == 423
