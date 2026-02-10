"""Tests for Merge Write Endpoints (Phase A — Wave 4).

Coverage (~30 tests):
- Propose: happy path, suggestion not found, duplicate active proposal, RBAC
- Approve: happy path, invalid transition, version mismatch, RBAC
- Apply: happy path, shadow mode, feature disabled, invalid transition, RBAC
- Rollback: happy path, TTL expired, subsequent merge exists, entity drifted, RBAC
- List proposals: empty, with data, status filter
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.auth.jwt_auth import Role, create_access_token
from api.routers import merge_review as merge_mod
from storage.signal_store import SignalStore


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role, email: str = "test@example.com") -> dict:
    token, _ = create_access_token(
        user_id="test-user", email=email, role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_companies(store, winner_id="company-a", loser_id="company-b"):
    """Seed two companies with signals, reviews, and company_files."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    # Signals for winner (each needs unique canonical_key+signal_type+source_api+detected_at)
    for i in range(1, 4):
        await db.execute(
            """INSERT OR IGNORE INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, 'WinnerCo', ?, 'domain:winner.com', ?, 'github',
                       0.8, ?, ?, '{}')""",
            (i, winner_id, f"signal_type_{i}", now, now),
        )

    # Signals for loser
    for i in range(4, 7):
        await db.execute(
            """INSERT OR IGNORE INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, 'LoserCo', ?, 'domain:loser.com', ?, 'sec_edgar',
                       0.7, ?, ?, '{}')""",
            (i, loser_id, f"signal_type_{i}", now, now),
        )

    # Reviews
    await db.execute(
        """INSERT OR IGNORE INTO review_items
           (id, company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (1, ?, 'approved', ?, ?, ?)""",
        (winner_id, json.dumps({"signal_ids": [1, 2, 3]}), now, now),
    )
    await db.execute(
        """INSERT OR IGNORE INTO review_items
           (id, company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (2, ?, 'pending', ?, ?, ?)""",
        (loser_id, json.dumps({"signal_ids": [4, 5, 6]}), now, now),
    )

    # Company files (no created_at/updated_at columns; status is thin/promoted/archived)
    await db.execute(
        """INSERT OR IGNORE INTO company_files
           (company_id, company_name, canonical_key, source_apis,
            first_seen_at, last_seen_at, status)
           VALUES (?, 'WinnerCo', 'domain:winner.com', '["github"]',
                   ?, ?, 'promoted')""",
        (winner_id, now, now),
    )
    await db.execute(
        """INSERT OR IGNORE INTO company_files
           (company_id, company_name, canonical_key, source_apis,
            first_seen_at, last_seen_at, status)
           VALUES (?, 'LoserCo', 'domain:loser.com', '["sec_edgar"]',
                   ?, ?, 'thin')""",
        (loser_id, now, now),
    )

    # Merge suggestion
    await db.execute(
        """INSERT OR IGNORE INTO merge_suggestions
           (id, pair_key, entity_a_company_id, entity_b_company_id,
            entity_a_canonical_key, entity_b_canonical_key,
            entity_a_company_name, entity_b_company_name,
            match_type, similarity_score, evidence_json, status, scoring_version, created_at)
           VALUES (1, ?, ?, ?, 'domain:winner.com', 'domain:loser.com',
                   'WinnerCo', 'LoserCo', 'shared_domain', 0.95,
                   '{"match_details":"domain match","domains":["winner.com","loser.com"]}',
                   'pending', '1.0.0', ?)""",
        (f"{winner_id}:{loser_id}", winner_id, loser_id, now),
    )

    await db.commit()


async def _propose_merge(client, suggestion_id=1, winner="company-a", loser="company-b"):
    """Helper: propose a merge and return response."""
    resp = await client.post(
        f"/api/v1/entities/merge-suggestions/{suggestion_id}/propose",
        json={
            "suggestion_id": suggestion_id,
            "winner_company_id": winner,
            "loser_company_id": loser,
            "reason": "Domain match",
        },
        headers=_auth_header(Role.GP),
    )
    return resp


async def _get_proposal_updated_at(store, proposal_id):
    db = store._db
    cursor = await db.execute(
        "SELECT updated_at FROM merge_proposals WHERE id = ?", (proposal_id,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


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
    app.include_router(merge_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, client, monkeypatch):
    """Seed companies + enable merge writes."""
    monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
    await _seed_companies(store)
    return client, store


# =============================================================================
# PROPOSE TESTS
# =============================================================================

class TestMergePropose:
    @pytest.mark.asyncio
    async def test_propose_happy_path(self, seeded):
        client, store = seeded
        resp = await _propose_merge(client)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["status"] == "proposed"
        assert data["proposal_id"] >= 1

    @pytest.mark.asyncio
    async def test_propose_suggestion_not_found(self, seeded):
        client, store = seeded
        resp = await _propose_merge(client, suggestion_id=9999)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_propose_duplicate_active(self, seeded):
        client, store = seeded
        resp1 = await _propose_merge(client)
        assert resp1.status_code == 201
        resp2 = await _propose_merge(client)
        # Should fail because partial unique index prevents duplicate active proposals
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_propose_readonly_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-suggestions/1/propose",
            json={"suggestion_id": 1, "winner_company_id": "company-a",
                  "loser_company_id": "company-b"},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_propose_analyst_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-suggestions/1/propose",
            json={"suggestion_id": 1, "winner_company_id": "company-a",
                  "loser_company_id": "company-b"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_propose_feature_disabled(self, client, store, monkeypatch):
        monkeypatch.delenv("MERGE_WRITES_ENABLED", raising=False)
        await _seed_companies(store)
        resp = await _propose_merge(client)
        assert resp.status_code == 423


# =============================================================================
# APPROVE TESTS
# =============================================================================

class TestMergeApprove:
    @pytest.mark.asyncio
    async def test_approve_happy_path(self, seeded):
        client, store = seeded
        # Propose first
        resp = await _propose_merge(client)
        assert resp.status_code == 201
        proposal_id = resp.json()["data"]["proposal_id"]

        updated_at = await _get_proposal_updated_at(store, proposal_id)

        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "approved"

    @pytest.mark.asyncio
    async def test_approve_invalid_transition(self, seeded):
        client, store = seeded
        # Propose + approve
        resp = await _propose_merge(client)
        proposal_id = resp.json()["data"]["proposal_id"]
        updated_at = await _get_proposal_updated_at(store, proposal_id)
        await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )

        # Try to approve again
        updated_at2 = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": updated_at2},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_approve_version_mismatch(self, seeded):
        client, store = seeded
        resp = await _propose_merge(client)
        proposal_id = resp.json()["data"]["proposal_id"]

        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": "2000-01-01T00:00:00+00:00"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "VERSION_MISMATCH"

    @pytest.mark.asyncio
    async def test_approve_not_found(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/9999/approve",
            json={"updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_readonly_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/approve",
            json={"updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_analyst_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/approve",
            json={"updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403


# =============================================================================
# APPLY TESTS
# =============================================================================

class TestMergeApply:
    @pytest.mark.asyncio
    async def test_apply_happy_path(self, seeded):
        client, store = seeded
        # Propose -> approve -> apply
        resp = await _propose_merge(client)
        proposal_id = resp.json()["data"]["proposal_id"]

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/apply",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["status"] == "applied"
        assert data["cascade_report"] is not None

        # Verify loser signals moved to winner
        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = ?", ("company-b",)
        )
        row = await cursor.fetchone()
        assert row[0] == 0  # All moved to winner

    @pytest.mark.asyncio
    async def test_apply_shadow_mode(self, store, client, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "shadow")
        await _seed_companies(store)

        # Propose (allowed in shadow)
        resp = await _propose_merge(client)
        assert resp.status_code == 201
        proposal_id = resp.json()["data"]["proposal_id"]

        # Approve (allowed in shadow)
        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200

        # Apply (blocked in shadow - requires active)
        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/apply",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 423

    @pytest.mark.asyncio
    async def test_apply_feature_disabled(self, client, store, monkeypatch):
        monkeypatch.delenv("MERGE_WRITES_ENABLED", raising=False)
        await _seed_companies(store)
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/apply",
            json={"updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 423

    @pytest.mark.asyncio
    async def test_apply_invalid_transition_from_proposed(self, seeded):
        client, store = seeded
        resp = await _propose_merge(client)
        proposal_id = resp.json()["data"]["proposal_id"]

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/apply",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_apply_readonly_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/apply",
            json={"updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_apply_analyst_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/apply",
            json={"updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403


# =============================================================================
# ROLLBACK TESTS
# =============================================================================

class TestMergeRollback:
    async def _setup_applied_merge(self, client, store):
        """Helper: propose -> approve -> apply, return proposal_id."""
        resp = await _propose_merge(client)
        proposal_id = resp.json()["data"]["proposal_id"]

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/apply",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 201
        return proposal_id

    @pytest.mark.asyncio
    async def test_rollback_happy_path(self, seeded):
        client, store = seeded
        proposal_id = await self._setup_applied_merge(client, store)

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
            json={"reason": "Testing rollback", "updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "rolled_back"

        # Verify loser signals restored
        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = ?", ("company-b",)
        )
        row = await cursor.fetchone()
        assert row[0] == 3  # Original 3 loser signals restored

    @pytest.mark.asyncio
    async def test_rollback_ttl_expired(self, seeded, monkeypatch):
        client, store = seeded
        monkeypatch.setenv("MERGE_ROLLBACK_TTL_HOURS", "0")  # Immediately expired
        proposal_id = await self._setup_applied_merge(client, store)

        # Wait a tiny bit to ensure TTL passes
        await asyncio.sleep(0.01)

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
            json={"reason": "Too late", "updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "ROLLBACK_WINDOW_EXPIRED"

    @pytest.mark.asyncio
    async def test_rollback_subsequent_merge_blocks(self, seeded):
        client, store = seeded
        proposal_id = await self._setup_applied_merge(client, store)

        # Create another applied merge involving winner
        db = store._db
        now = datetime.now(timezone.utc)
        later = (now + timedelta(seconds=1)).isoformat()
        await db.execute(
            """INSERT INTO merge_proposals
               (suggestion_id, entity_a_company_id, entity_b_company_id,
                winner_company_id, loser_company_id, status,
                proposed_by, proposed_at, applied_at, updated_at)
               VALUES (999, 'company-a', 'company-c', 'company-a', 'company-c',
                       'applied', 'test@example.com', ?, ?, ?)""",
            (later, later, later),
        )
        await db.commit()

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
            json={"reason": "Should fail", "updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "ROLLBACK_SUBSEQUENT_MERGE_EXISTS"

    @pytest.mark.asyncio
    async def test_rollback_entity_drifted(self, seeded):
        client, store = seeded
        proposal_id = await self._setup_applied_merge(client, store)

        # Modify winner entity (add a new signal) to cause drift
        db = store._db
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (100, 'WinnerCo', 'company-a', 'domain:winner.com', 'new_company',
                       'news_api', 0.9, ?, ?, '{}')""",
            (now, now),
        )
        await db.commit()

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
            json={"reason": "Drifted", "updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "ROLLBACK_ENTITY_DRIFTED"

    @pytest.mark.asyncio
    async def test_rollback_invalid_transition(self, seeded):
        client, store = seeded
        resp = await _propose_merge(client)
        proposal_id = resp.json()["data"]["proposal_id"]

        updated_at = await _get_proposal_updated_at(store, proposal_id)
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
            json={"reason": "Can't rollback proposed", "updated_at": updated_at},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_rollback_readonly_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/rollback",
            json={"reason": "RBAC test", "updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_rollback_analyst_forbidden(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/entities/merge-proposals/1/rollback",
            json={"reason": "RBAC test", "updated_at": "2026-01-01T00:00:00Z"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403


# =============================================================================
# LIST PROPOSALS TESTS
# =============================================================================

class TestListProposals:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get(
            "/api/v1/entities/merge-proposals",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_with_data(self, seeded):
        client, store = seeded
        await _propose_merge(client)
        resp = await client.get(
            "/api/v1/entities/merge-proposals",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1
        assert data[0]["status"] == "proposed"

    @pytest.mark.asyncio
    async def test_list_status_filter(self, seeded):
        client, store = seeded
        await _propose_merge(client)
        resp = await client.get(
            "/api/v1/entities/merge-proposals?status=approved",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []  # None approved yet

    @pytest.mark.asyncio
    async def test_list_readonly_allowed(self, client):
        """READONLY users can view proposals (VIEW permission)."""
        resp = await client.get(
            "/api/v1/entities/merge-proposals",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_analyst_allowed(self, client):
        """ANALYST users can view proposals (VIEW permission)."""
        resp = await client.get(
            "/api/v1/entities/merge-proposals",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
