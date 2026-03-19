"""Tests for api/routers/governance.py — transition validation, 422 errors."""

import os
import sys
import tempfile

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.auth.jwt_auth import Role, create_access_token
from api.routers import governance as governance_mod
from storage.signal_store import SignalStore


def _auth_header(role: Role = Role.GP, email: str = "test@example.com") -> dict:
    token, _ = create_access_token(
        user_id="test-user", email=email, role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def app():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SignalStore(db_path=db_path)
    await store.initialize()

    app = FastAPI()
    app.state.store = store
    app.include_router(governance_mod.router, prefix="/api/v1")

    yield app

    await store.close()
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ── Valid promote ────────────────────────────────────────────────────────


class TestValidPromote:
    @pytest.mark.asyncio
    async def test_delivery_mode_promote(self, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/governance/events",
                headers=_auth_header(),
                json={
                    "feature_name": "DELIVERY_MODE",
                    "reason": "Step 4A promotion",
                    "metadata": {
                        "action_type": "feature_promote",
                        "feature_name": "DELIVERY_MODE",
                        "from_state": "manual_publish",
                        "to_state": "batch_publish",
                        "regret_due_at": "2026-04-01",
                        "config_snapshot_hash": "abc123",
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["event_id"] > 0
            assert data["action_type"] == "feature_promote"
            assert data["feature_name"] == "DELIVERY_MODE"

    @pytest.mark.asyncio
    async def test_feature_registry_promote(self, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/governance/events",
                headers=_auth_header(),
                json={
                    "feature_name": "boilerplate_defense",
                    "reason": "Shadow → active",
                    "metadata": {
                        "action_type": "feature_promote",
                        "feature_name": "boilerplate_defense",
                        "from_state": "shadow",
                        "to_state": "active",
                        "regret_due_at": "2026-04-01",
                        "config_snapshot_hash": "def456",
                    },
                },
            )
            assert resp.status_code == 200


# ── 422 errors ───────────────────────────────────────────────────────────


class TestValidation422:
    @pytest.mark.asyncio
    async def test_cross_family_states_rejected(self, app):
        """Using env-backed states for feature-registry flag → 422."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/governance/events",
                headers=_auth_header(),
                json={
                    "feature_name": "boilerplate_defense",
                    "reason": "should fail",
                    "metadata": {
                        "action_type": "feature_promote",
                        "feature_name": "boilerplate_defense",
                        "from_state": "manual_publish",
                        "to_state": "batch_publish",
                        "regret_due_at": "2026-04-01",
                        "config_snapshot_hash": "abc",
                    },
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_wrong_direction_rejected(self, app):
        """Promoting downward → 422."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/governance/events",
                headers=_auth_header(),
                json={
                    "feature_name": "DELIVERY_MODE",
                    "reason": "should fail",
                    "metadata": {
                        "action_type": "feature_promote",
                        "feature_name": "DELIVERY_MODE",
                        "from_state": "batch_publish",
                        "to_state": "manual_publish",
                        "regret_due_at": "2026-04-01",
                        "config_snapshot_hash": "abc",
                    },
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_flag_rejected(self, app):
        """Unknown flag → 422."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/governance/events",
                headers=_auth_header(),
                json={
                    "feature_name": "NONEXISTENT",
                    "reason": "should fail",
                    "metadata": {
                        "action_type": "feature_promote",
                        "feature_name": "NONEXISTENT",
                        "from_state": "off",
                        "to_state": "active",
                        "regret_due_at": "2026-04-01",
                        "config_snapshot_hash": "abc",
                    },
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_garbage_state_rejected(self, app):
        """Completely invalid state → 422."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/governance/events",
                headers=_auth_header(),
                json={
                    "feature_name": "DELIVERY_MODE",
                    "reason": "should fail",
                    "metadata": {
                        "action_type": "feature_promote",
                        "feature_name": "DELIVERY_MODE",
                        "from_state": "yolo",
                        "to_state": "batch_publish",
                        "regret_due_at": "2026-04-01",
                        "config_snapshot_hash": "abc",
                    },
                },
            )
            assert resp.status_code == 422
