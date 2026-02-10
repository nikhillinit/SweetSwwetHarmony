"""Tests for Hunter API Router.

Coverage (~20 tests):
- Read endpoints: list runs, list results, budget
- Promote: happy path, quality threshold rejection, RBAC, feature disabled, idempotency
- Feedback: happy path, invalid feedback, not found
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.auth.jwt_auth import Role, create_access_token
from api.routers import hunter as hunter_mod
from storage.signal_store import SignalStore


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role, email: str = "test@example.com") -> dict:
    token, _ = create_access_token(
        user_id="test-user", email=email, role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_hunter_run(store, run_id="run-001"):
    """Seed a hunter run + query + result."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    # Create run_history entry (id is the run_id in run_history table)
    await db.execute(
        """INSERT OR IGNORE INTO run_history
           (id, run_type, status, created_at)
           VALUES (?, 'hunter', 'completed', ?)""",
        (run_id, now),
    )

    # Create hunter query
    await db.execute(
        """INSERT OR IGNORE INTO hunter_queries
           (id, run_id, collector, query_text, query_type, status,
            results_count, created_at, executed_at, completed_at)
           VALUES (1, ?, 'github', 'consumer cpg startup', 'pattern',
                   'completed', 1, ?, ?, ?)""",
        (run_id, now, now, now),
    )

    # Create hunter result (status=relevant for promotion)
    await db.execute(
        """INSERT OR IGNORE INTO hunter_results
           (id, run_id, query_id, result_dedupe_key, company_name,
            canonical_key, company_id, source_api, raw_data,
            confidence_score, thesis_fit_score, already_known,
            status, created_at, updated_at)
           VALUES (1, ?, 1, 'dedupe:test-1', 'TestCo',
                   'domain:testco.com', 'company-testco', 'github', ?,
                   0.75, 0.8, 0, 'relevant', ?, ?)""",
        (run_id, json.dumps({"description": "Test company"}), now, now),
    )

    # Seed a not_relevant result too
    await db.execute(
        """INSERT OR IGNORE INTO hunter_results
           (id, run_id, query_id, result_dedupe_key, company_name,
            canonical_key, company_id, source_api, raw_data,
            confidence_score, thesis_fit_score, already_known,
            status, created_at, updated_at)
           VALUES (2, ?, 1, 'dedupe:test-2', 'IgnoredCo',
                   'domain:ignored.com', 'company-ignored', 'github', ?,
                   0.3, 0.2, 0, 'not_relevant', ?, ?)""",
        (run_id, json.dumps({"description": "Ignored company"}), now, now),
    )

    # Budget entry
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.execute(
        """INSERT OR IGNORE INTO hunter_budget
           (collector, budget_date, queries_executed, queries_cap,
            cost_units, cost_cap, circuit_breaker_tripped, updated_at)
           VALUES ('__global__', ?, 5, 100, 10, 500, 0, ?)""",
        (today, now),
    )

    await db.commit()


async def _seed_pending_result(store, result_id=3, run_id="run-001"):
    """Seed a pending result for feedback testing."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR IGNORE INTO hunter_results
           (id, run_id, query_id, result_dedupe_key, company_name,
            canonical_key, company_id, source_api, raw_data,
            confidence_score, thesis_fit_score, already_known,
            status, created_at, updated_at)
           VALUES (?, ?, 1, 'dedupe:test-pending', 'PendingCo',
                   'domain:pending.com', 'company-pending', 'github', ?,
                   0.6, 0.5, 0, 'pending', ?, ?)""",
        (result_id, run_id, json.dumps({"description": "Pending"}), now, now),
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
    app.include_router(hunter_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, client):
    await _seed_hunter_run(store)
    return client, store


# =============================================================================
# READ ENDPOINT TESTS
# =============================================================================

class TestHunterRuns:
    @pytest.mark.asyncio
    async def test_list_runs_empty(self, client):
        resp = await client.get(
            "/api/v1/hunter/runs", headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_runs_with_data(self, seeded):
        client, store = seeded
        resp = await client.get(
            "/api/v1/hunter/runs", headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1
        assert data[0]["run_id"] == "run-001"
        assert data[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_list_results(self, seeded):
        client, store = seeded
        resp = await client.get(
            "/api/v1/hunter/runs/run-001/results",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_results_status_filter(self, seeded):
        client, store = seeded
        resp = await client.get(
            "/api/v1/hunter/runs/run-001/results?status=relevant",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["status"] == "relevant"

    @pytest.mark.asyncio
    async def test_list_queries(self, seeded):
        client, store = seeded
        resp = await client.get(
            "/api/v1/hunter/runs/run-001/queries",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["collector"] == "github"

    @pytest.mark.asyncio
    async def test_budget(self, seeded):
        client, store = seeded
        resp = await client.get(
            "/api/v1/hunter/budget", headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "budget_date" in data


# =============================================================================
# PROMOTE ENDPOINT TESTS
# =============================================================================

class TestHunterPromote:
    @pytest.mark.asyncio
    async def test_promote_happy_path(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        resp = await client.post(
            "/api/v1/hunter/results/1/promote",
            json={"reason": "Great fit"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["success"] is True
        assert data["signal_id"] is not None
        assert data["status"] == "promoted"

    @pytest.mark.asyncio
    async def test_promote_feature_disabled(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.delenv("HUNTER_PROMOTE_ENABLED", raising=False)
        resp = await client.post(
            "/api/v1/hunter/results/1/promote",
            json={},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 423
        assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"

    @pytest.mark.asyncio
    async def test_promote_rbac_readonly_forbidden(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        resp = await client.post(
            "/api/v1/hunter/results/1/promote",
            json={},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_promote_quality_threshold_rejection(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        monkeypatch.setenv("HUNTER_PROMOTE_MIN_CONFIDENCE", "0.9")
        resp = await client.post(
            "/api/v1/hunter/results/1/promote",
            json={},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "BELOW_QUALITY_THRESHOLD"

    @pytest.mark.asyncio
    async def test_promote_not_found(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        resp = await client.post(
            "/api/v1/hunter/results/9999/promote",
            json={},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_promote_idempotent(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        headers = {
            **_auth_header(Role.ANALYST),
            "Idempotency-Key": "promote-test-1",
        }
        resp1 = await client.post(
            "/api/v1/hunter/results/1/promote", json={}, headers=headers,
        )
        assert resp1.status_code == 201

        # Second call with same key → already promoted
        resp2 = await client.post(
            "/api/v1/hunter/results/1/promote", json={}, headers=headers,
        )
        # Already promoted returns 200 (not 201)
        assert resp2.status_code in (200, 201)
        assert resp2.json()["data"]["success"] is True


# =============================================================================
# FEEDBACK ENDPOINT TESTS
# =============================================================================

class TestHunterFeedback:
    @pytest.mark.asyncio
    async def test_feedback_happy_path(self, seeded):
        client, store = seeded
        await _seed_pending_result(store, result_id=3, run_id="run-001")
        resp = await client.post(
            "/api/v1/hunter/results/3/feedback",
            json={"feedback": "relevant", "reason": "Good fit"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["new_status"] == "relevant"

    @pytest.mark.asyncio
    async def test_feedback_invalid_value(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/hunter/results/1/feedback",
            json={"feedback": "invalid_status"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_feedback_not_found(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/hunter/results/9999/feedback",
            json={"feedback": "relevant"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_feedback_readonly_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/hunter/results/1/feedback",
            json={"feedback": "relevant"},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403
