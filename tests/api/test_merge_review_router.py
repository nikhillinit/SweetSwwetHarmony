"""Tests for Merge Review API Router.

Coverage (~12 tests):
- Merge suggestions: list (empty, with data, status filter, pagination)
- Merge suggestion detail: found, not found, RBAC, blast radius computation
- Shadow runs: list (empty, with data, pagination, RBAC)
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
from api.routers import merge_review as merge_review_mod
from storage.signal_store import SignalStore


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role, email: str = "test@example.com") -> dict:
    token, _ = create_access_token(
        user_id="test-user", email=email, role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_merge_suggestion(
    store,
    suggestion_id=1,
    pair_key="abc123def456",
    status="pending",
    score=0.85,
    entity_a_company_id="comp-a",
    entity_b_company_id="comp-b",
    blast_radius_json=None,
    evidence_json="{}",
):
    """Insert a merge suggestion directly into the DB."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """INSERT INTO merge_suggestions
           (id, pair_key, entity_a_company_id, entity_b_company_id,
            entity_a_canonical_key, entity_b_canonical_key,
            entity_a_company_name, entity_b_company_name,
            match_type, similarity_score, scoring_version,
            evidence_json, status, blast_radius_json, created_at)
           VALUES (?, ?, ?, ?, 'domain:a.com', 'domain:b.com',
                   'Company A', 'Company B', 'fuzzy_name', ?, '1.0.0',
                   ?, ?, ?, datetime('now'))""",
        (suggestion_id, pair_key, entity_a_company_id, entity_b_company_id,
         score, evidence_json, status, blast_radius_json),
    )
    await db.commit()


async def _seed_shadow_run(
    store,
    run_id=1,
    run_ref="run-001",
    status="completed",
    total_signals=100,
    agreements=90,
    disagreements=10,
    agreement_rate=0.9,
    duration_ms=1200.0,
    inputs_hash="abc123",
    truncated=0,
):
    """Insert a shadow entity run directly into the DB."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """INSERT INTO shadow_entity_runs
           (id, run_id, status, total_signals, phase1a_groups, phase_g_groups,
            agreements, disagreements, agreement_rate, duration_ms,
            inputs_hash, truncated, created_at)
           VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (run_id, run_ref, status, total_signals, agreements, disagreements,
         agreement_rate, duration_ms, inputs_hash, truncated),
    )
    await db.commit()


async def _seed_signal(store, signal_id=1, company_id="comp-a"):
    """Insert a minimal signal for blast radius calculation."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """INSERT OR REPLACE INTO signals
           (id, company_name, company_id, canonical_key, signal_type,
            source_api, confidence, detected_at, created_at, raw_data)
           VALUES (?, 'Test Co', ?, ?, 'new_company',
                   'github', 0.8, datetime('now'), datetime('now'), '{}')""",
        (signal_id, company_id, f"domain:test{signal_id}.com"),
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
    app.include_router(merge_review_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# =============================================================================
# MERGE SUGGESTION LIST TESTS
# =============================================================================

class TestListMergeSuggestions:
    """Tests for GET /entities/merge-suggestions."""

    @pytest.mark.asyncio
    async def test_list_merge_suggestions_empty(self, client):
        """Empty table should return empty list with has_more=False."""
        resp = await client.get(
            "/api/v1/entities/merge-suggestions",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["meta"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_list_merge_suggestions_with_data(self, client, store):
        """Should return seeded merge suggestions."""
        await _seed_merge_suggestion(store, suggestion_id=1, pair_key="pair-1")
        await _seed_merge_suggestion(store, suggestion_id=2, pair_key="pair-2")

        resp = await client.get(
            "/api/v1/entities/merge-suggestions",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        # Verify structure of returned items
        item = data["data"][0]
        assert "id" in item
        assert "pair_key" in item
        assert "similarity_score" in item
        assert "status" in item

    @pytest.mark.asyncio
    async def test_list_merge_suggestions_status_filter(self, client, store):
        """Should filter by status when provided."""
        await _seed_merge_suggestion(store, suggestion_id=1, pair_key="p-1", status="pending")
        await _seed_merge_suggestion(store, suggestion_id=2, pair_key="p-2", status="approved")
        await _seed_merge_suggestion(store, suggestion_id=3, pair_key="p-3", status="pending")

        resp = await client.get(
            "/api/v1/entities/merge-suggestions?status=pending",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert all(item["status"] == "pending" for item in data["data"])

    @pytest.mark.asyncio
    async def test_list_merge_suggestions_pagination(self, client, store):
        """Should respect limit and provide pagination cursor."""
        for i in range(5):
            await _seed_merge_suggestion(
                store, suggestion_id=i + 1, pair_key=f"pair-{i}",
            )

        # Request first page with limit=2
        resp = await client.get(
            "/api/v1/entities/merge-suggestions?limit=2",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["data"]) == 2
        assert page1["meta"]["has_more"] is True


# =============================================================================
# MERGE SUGGESTION DETAIL TESTS
# =============================================================================

class TestGetMergeSuggestionDetail:
    """Tests for GET /entities/merge-suggestions/{suggestion_id}."""

    @pytest.mark.asyncio
    async def test_get_merge_suggestion_detail_found(self, client, store):
        """Should return detail for existing suggestion (GP role)."""
        evidence = {"shared_domains": ["test.com"]}
        await _seed_merge_suggestion(
            store, suggestion_id=42, pair_key="detail-pair",
            evidence_json=json.dumps(evidence),
        )

        resp = await client.get(
            "/api/v1/entities/merge-suggestions/42",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == 42
        assert data["pair_key"] == "detail-pair"
        assert data["evidence"] == evidence
        # Blast radius should have been lazily computed
        assert data["blast_radius"] is not None

    @pytest.mark.asyncio
    async def test_get_merge_suggestion_detail_not_found(self, client):
        """Should return 404 for non-existent suggestion."""
        resp = await client.get(
            "/api/v1/entities/merge-suggestions/9999",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_merge_suggestion_detail_rbac(self, client, store):
        """READONLY user should get 403 (requires ENTITY_MERGE permission)."""
        await _seed_merge_suggestion(store, suggestion_id=1, pair_key="rbac-test")

        resp = await client.get(
            "/api/v1/entities/merge-suggestions/1",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_merge_suggestion_detail_analyst_forbidden(self, client, store):
        """ANALYST user should also get 403 (ENTITY_MERGE is GP-only)."""
        await _seed_merge_suggestion(store, suggestion_id=1, pair_key="analyst-test")

        resp = await client.get(
            "/api/v1/entities/merge-suggestions/1",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_merge_suggestion_detail_computes_blast_radius(self, client, store):
        """Blast radius should be lazily computed on first request."""
        await _seed_merge_suggestion(
            store, suggestion_id=10, pair_key="blast-test",
            entity_a_company_id="comp-a", entity_b_company_id="comp-b",
            blast_radius_json=None,
        )
        # Seed signals for both entities to verify blast radius counts
        await _seed_signal(store, signal_id=100, company_id="comp-a")
        await _seed_signal(store, signal_id=101, company_id="comp-a")
        await _seed_signal(store, signal_id=200, company_id="comp-b")

        resp = await client.get(
            "/api/v1/entities/merge-suggestions/10",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        br = data["blast_radius"]
        assert br is not None
        assert br["signals_a"] == 2
        assert br["signals_b"] == 1
        assert br["total_affected"] >= 3

        # Verify it was cached in DB
        db = store._db
        cursor = await db.execute(
            "SELECT blast_radius_json FROM merge_suggestions WHERE id = 10",
        )
        row = await cursor.fetchone()
        assert row[0] is not None
        cached = json.loads(row[0])
        assert cached["signals_a"] == 2

    @pytest.mark.asyncio
    async def test_get_merge_suggestion_detail_uses_cached_blast_radius(self, client, store):
        """Should use cached blast_radius_json if available instead of recomputing."""
        cached_br = json.dumps({
            "signals_a": 99, "signals_b": 88, "reviews_a": 0, "reviews_b": 0,
            "files_a": 0, "files_b": 0, "total_affected": 187,
            "capped": False, "timeout": False,
        })
        await _seed_merge_suggestion(
            store, suggestion_id=20, pair_key="cached-br-test",
            blast_radius_json=cached_br,
        )

        resp = await client.get(
            "/api/v1/entities/merge-suggestions/20",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        br = resp.json()["data"]["blast_radius"]
        # Should use cached values, not recompute
        assert br["signals_a"] == 99
        assert br["signals_b"] == 88


# =============================================================================
# SHADOW RUNS LIST TESTS
# =============================================================================

class TestListShadowRuns:
    """Tests for GET /entities/shadow-runs."""

    @pytest.mark.asyncio
    async def test_list_shadow_runs_empty(self, client):
        """Empty table should return empty list."""
        resp = await client.get(
            "/api/v1/entities/shadow-runs",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["meta"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_list_shadow_runs_with_data(self, client, store):
        """Should return seeded shadow runs."""
        await _seed_shadow_run(store, run_id=1, run_ref="run-001")
        await _seed_shadow_run(store, run_id=2, run_ref="run-002")

        resp = await client.get(
            "/api/v1/entities/shadow-runs",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        item = data["data"][0]
        assert "id" in item
        assert "run_id" in item
        assert "status" in item
        assert "agreement_rate" in item

    @pytest.mark.asyncio
    async def test_list_shadow_runs_pagination(self, client, store):
        """Should respect limit and signal has_more."""
        for i in range(5):
            await _seed_shadow_run(
                store, run_id=i + 1, run_ref=f"run-{i:03d}",
            )

        resp = await client.get(
            "/api/v1/entities/shadow-runs?limit=2",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["data"]) == 2
        assert page1["meta"]["has_more"] is True

    @pytest.mark.asyncio
    async def test_list_shadow_runs_rbac(self, client, store):
        """All roles should be able to list shadow runs (VIEW permission)."""
        await _seed_shadow_run(store, run_id=1, run_ref="run-rbac")

        for role in (Role.READONLY, Role.ANALYST, Role.GP):
            resp = await client.get(
                "/api/v1/entities/shadow-runs",
                headers=_auth_header(role),
            )
            assert resp.status_code == 200, f"Role {role.value} should have VIEW permission"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
