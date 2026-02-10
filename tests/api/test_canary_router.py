"""Tests for Canary API Router.

Coverage (~10 tests):
- Canary status: empty, with run data
- Canary runs: list (empty, with data, pagination)
- Drift alerts: list (empty, with data, status filter, severity filter)
- RBAC: all VIEW endpoints accessible by all roles
"""

import asyncio
import json
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


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role, email: str = "test@example.com") -> dict:
    token, _ = create_access_token(
        user_id="test-user", email=email, role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_canary_run(
    store,
    run_id=1,
    run_ref="canary-run-001",
    golden_set_size=20,
    golden_set_hash="hash123",
    total_scored=20,
    passed=18,
    failed=2,
    skipped=0,
    pass_rate=0.9,
    verdict="pass",
    drift_threshold=0.15,
    pass_rate_threshold=0.80,
    duration_ms=500.0,
):
    """Insert a canary run directly into the DB."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """INSERT INTO canary_runs
           (id, run_id, golden_set_size, golden_set_hash,
            total_scored, passed, failed, skipped,
            pass_rate, verdict, drift_threshold, pass_rate_threshold,
            duration_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (run_id, run_ref, golden_set_size, golden_set_hash,
         total_scored, passed, failed, skipped,
         pass_rate, verdict, drift_threshold, pass_rate_threshold,
         duration_ms),
    )
    await db.commit()


async def _seed_drift_alert(
    store,
    alert_id=1,
    canary_run_id=1,
    alert_type="pass_rate_drop",
    severity="warning",
    metric_name="pass_rate",
    expected_value=0.95,
    actual_value=0.80,
    delta=-0.15,
    message="Pass rate dropped by 15%",
    status="open",
):
    """Insert a canary drift alert directly into the DB."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """INSERT INTO canary_drift_alerts
           (id, canary_run_id, alert_type, severity,
            metric_name, expected_value, actual_value, delta,
            message, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (alert_id, canary_run_id, alert_type, severity,
         metric_name, expected_value, actual_value, delta,
         message, status),
    )
    await db.commit()


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
async def client(store):
    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()
    app.include_router(canary_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# =============================================================================
# CANARY STATUS TESTS
# =============================================================================

class TestCanaryStatus:
    """Tests for GET /canary/status."""

    @pytest.mark.asyncio
    async def test_canary_status_empty(self, client):
        """Empty DB should return null status fields and zero counts."""
        resp = await client.get(
            "/api/v1/canary/status",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["latest_verdict"] is None
        assert data["latest_pass_rate"] is None
        assert data["latest_run_at"] is None
        assert data["total_runs"] == 0
        assert data["open_alerts"] == 0

    @pytest.mark.asyncio
    async def test_canary_status_with_run(self, client, store):
        """Should return latest verdict and counts when runs exist."""
        await _seed_canary_run(store, run_id=1, verdict="pass", pass_rate=0.95)
        await _seed_canary_run(store, run_id=2, verdict="fail", pass_rate=0.70)
        await _seed_drift_alert(store, alert_id=1, canary_run_id=2, status="open")

        resp = await client.get(
            "/api/v1/canary/status",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Latest run is the most recent (run_id=2, verdict=fail)
        assert data["latest_verdict"] == "fail"
        assert data["latest_pass_rate"] == 0.70
        assert data["total_runs"] == 2
        assert data["open_alerts"] == 1

    @pytest.mark.asyncio
    async def test_canary_status_rbac(self, client, store):
        """All roles (VIEW permission) should be able to access status."""
        await _seed_canary_run(store, run_id=1, verdict="pass", pass_rate=0.9)

        for role in (Role.READONLY, Role.ANALYST, Role.GP):
            resp = await client.get(
                "/api/v1/canary/status",
                headers=_auth_header(role),
            )
            assert resp.status_code == 200, (
                f"Role {role.value} should have VIEW permission"
            )


# =============================================================================
# CANARY RUNS LIST TESTS
# =============================================================================

class TestListCanaryRuns:
    """Tests for GET /canary/runs."""

    @pytest.mark.asyncio
    async def test_list_canary_runs_empty(self, client):
        """Empty table should return empty list."""
        resp = await client.get(
            "/api/v1/canary/runs",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["meta"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_list_canary_runs_with_data(self, client, store):
        """Should return seeded canary runs with correct structure."""
        await _seed_canary_run(store, run_id=1, run_ref="cr-001", verdict="pass")
        await _seed_canary_run(store, run_id=2, run_ref="cr-002", verdict="fail")

        resp = await client.get(
            "/api/v1/canary/runs",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        # Check item structure
        item = data["data"][0]
        assert "id" in item
        assert "run_id" in item
        assert "golden_set_size" in item
        assert "pass_rate" in item
        assert "verdict" in item
        assert "created_at" in item

    @pytest.mark.asyncio
    async def test_list_canary_runs_pagination(self, client, store):
        """Should respect limit and signal has_more."""
        for i in range(5):
            await _seed_canary_run(
                store, run_id=i + 1, run_ref=f"cr-{i:03d}",
                verdict="pass", pass_rate=0.9,
            )

        resp = await client.get(
            "/api/v1/canary/runs?limit=3",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["data"]) == 3
        assert page1["meta"]["has_more"] is True


# =============================================================================
# DRIFT ALERTS TESTS
# =============================================================================

class TestListDriftAlerts:
    """Tests for GET /canary/drift-alerts."""

    @pytest.mark.asyncio
    async def test_list_drift_alerts_empty(self, client):
        """Empty table should return empty list."""
        resp = await client.get(
            "/api/v1/canary/drift-alerts",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["meta"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_list_drift_alerts_with_data(self, client, store):
        """Should return seeded drift alerts with correct structure."""
        await _seed_canary_run(store, run_id=1, run_ref="cr-alert-1")
        await _seed_drift_alert(
            store, alert_id=1, canary_run_id=1,
            alert_type="pass_rate_drop", severity="critical",
            message="Pass rate dropped significantly",
        )
        await _seed_drift_alert(
            store, alert_id=2, canary_run_id=1,
            alert_type="individual_drift", severity="warning",
            message="Individual signal drift detected",
        )

        resp = await client.get(
            "/api/v1/canary/drift-alerts",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        item = data["data"][0]
        assert "id" in item
        assert "alert_type" in item
        assert "severity" in item
        assert "message" in item
        assert "status" in item

    @pytest.mark.asyncio
    async def test_list_drift_alerts_status_filter(self, client, store):
        """Should filter drift alerts by status."""
        await _seed_canary_run(store, run_id=1, run_ref="cr-status-filter")
        await _seed_drift_alert(
            store, alert_id=1, canary_run_id=1, status="open",
        )
        await _seed_drift_alert(
            store, alert_id=2, canary_run_id=1, status="acknowledged",
        )
        await _seed_drift_alert(
            store, alert_id=3, canary_run_id=1, status="open",
        )

        resp = await client.get(
            "/api/v1/canary/drift-alerts?status=open",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert all(item["status"] == "open" for item in data["data"])

    @pytest.mark.asyncio
    async def test_list_drift_alerts_severity_filter(self, client, store):
        """Should filter drift alerts by severity."""
        await _seed_canary_run(store, run_id=1, run_ref="cr-sev-filter")
        await _seed_drift_alert(
            store, alert_id=1, canary_run_id=1,
            severity="critical", message="Critical drift",
        )
        await _seed_drift_alert(
            store, alert_id=2, canary_run_id=1,
            severity="warning", message="Warning drift",
        )
        await _seed_drift_alert(
            store, alert_id=3, canary_run_id=1,
            severity="critical", message="Another critical",
        )

        resp = await client.get(
            "/api/v1/canary/drift-alerts?severity=critical",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert all(item["severity"] == "critical" for item in data["data"])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
