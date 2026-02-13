"""E2E drift workflow test (W5.13).

Full lifecycle via FastAPI TestClient:
1. Seed canary run + drift alerts
2. List alerts (verify data)
3. Acknowledge alert
4. Resolve alert
5. Verify audit trail
6. RBAC enforcement
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
from api.routers import canary as canary_mod
from storage.signal_store import SignalStore


def _auth_header(role: Role) -> dict:
    token, _ = create_access_token(
        user_id="e2e-user", email="e2e@example.com", role=role, name="E2E",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_run_and_alerts(store):
    """Seed a canary run and several drift alerts."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        "INSERT INTO canary_runs "
        "(id, run_id, golden_set_size, golden_set_hash, total_scored, passed, failed, "
        "skipped, pass_rate, verdict, drift_threshold, pass_rate_threshold, duration_ms, created_at) "
        "VALUES (1, 'e2e-run', 20, 'hash', 20, 14, 6, 0, 0.7, 'fail', 0.15, 0.80, 300, datetime('now'))"
    )
    for i in range(3):
        await db.execute(
            "INSERT INTO canary_drift_alerts "
            "(id, canary_run_id, alert_type, severity, metric_name, message, status, created_at) "
            "VALUES (?, 1, 'pass_rate_drop', 'warning', 'pass_rate', ?, 'open', datetime('now'))",
            (i + 1, f"E2E alert {i + 1}"),
        )
    await db.commit()


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
async def client(store):
    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()
    app.include_router(canary_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDriftE2EWorkflow:
    """Full E2E lifecycle: seed -> list -> ack -> resolve -> audit."""

    @pytest.mark.asyncio
    async def test_list_alerts_returns_seeded_data(self, client, store):
        """GET /drift-alerts should return seeded alerts."""
        await _seed_run_and_alerts(store)

        resp = await client.get(
            "/api/v1/canary/drift-alerts",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 3
        assert all(a["status"] == "open" for a in data)

    @pytest.mark.asyncio
    async def test_ack_then_resolve_lifecycle(self, client, store, monkeypatch):
        """Ack then resolve an alert through the full lifecycle."""
        monkeypatch.setenv("DRIFT_MONITORING_ENABLED", "active")
        await _seed_run_and_alerts(store)

        # Acknowledge
        resp = await client.post(
            "/api/v1/canary/drift-alerts/1/acknowledge",
            json={"reason": "E2E investigating"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "acknowledged"

        # Resolve
        resp = await client.post(
            "/api/v1/canary/drift-alerts/1/resolve",
            json={"resolution": "E2E resolved"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_audit_trail_recorded(self, client, store, monkeypatch):
        """Audit events should be recorded for each transition."""
        monkeypatch.setenv("DRIFT_MONITORING_ENABLED", "active")
        await _seed_run_and_alerts(store)

        # Ack then resolve
        await client.post(
            "/api/v1/canary/drift-alerts/2/acknowledge",
            json={"reason": "Audit test"},
            headers=_auth_header(Role.ANALYST),
        )
        await client.post(
            "/api/v1/canary/drift-alerts/2/resolve",
            json={"resolution": "Audit resolved"},
            headers=_auth_header(Role.ANALYST),
        )

        # Check audit events
        cursor = await store._db.execute(
            "SELECT action_type FROM audit_events "
            "WHERE entity_type = 'canary_drift_alert' AND entity_id = '2' "
            "ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        actions = [r[0] for r in rows]
        assert "alert_acknowledged" in actions
        assert "alert_resolved" in actions

    @pytest.mark.asyncio
    async def test_feature_disabled_returns_423(self, client, store, monkeypatch):
        """Mutation endpoints should return 423 when feature is disabled."""
        monkeypatch.delenv("DRIFT_MONITORING_ENABLED", raising=False)
        await _seed_run_and_alerts(store)

        resp = await client.post(
            "/api/v1/canary/drift-alerts/1/acknowledge",
            json={"reason": "Should fail"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 423

    @pytest.mark.asyncio
    async def test_stats_reflect_mutations(self, client, store, monkeypatch):
        """Stats should reflect alert status changes."""
        monkeypatch.setenv("DRIFT_MONITORING_ENABLED", "active")
        await _seed_run_and_alerts(store)

        # Initially 3 open
        resp = await client.get(
            "/api/v1/canary/drift-alerts/stats",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.json()["data"]["open"] == 3

        # Resolve one
        await client.post(
            "/api/v1/canary/drift-alerts/1/resolve",
            json={"resolution": "Stats test"},
            headers=_auth_header(Role.ANALYST),
        )

        # Now 2 open, 1 resolved
        resp = await client.get(
            "/api/v1/canary/drift-alerts/stats",
            headers=_auth_header(Role.READONLY),
        )
        stats = resp.json()["data"]
        assert stats["open"] == 2
        assert stats["resolved"] == 1

    @pytest.mark.asyncio
    async def test_canary_status_reflects_run(self, client, store):
        """Canary status should show latest run data."""
        await _seed_run_and_alerts(store)

        resp = await client.get(
            "/api/v1/canary/status",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["latest_verdict"] == "fail"
        assert data["latest_pass_rate"] == 0.7
        assert data["total_runs"] == 1
        assert data["open_alerts"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
