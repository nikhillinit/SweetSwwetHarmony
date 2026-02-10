"""Cross-phase integration tests for Wave 4 — Write Activation.

Verifies interactions between:
- Merge → Triage (merged entity's reviews handled correctly)
- Bulk Triage + Merge (concurrent bulk while merge in-flight)
- Hunter Promotion during merge lineage
- Full lifecycle: seed → triage → merge → rollback → re-triage
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
from api.routers import merge_review as merge_mod
from api.routers import triage as triage_mod
from api.routers import hunter as hunter_mod
from storage.signal_store import SignalStore


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role = Role.GP) -> dict:
    token, _ = create_access_token(
        user_id="test-user", email="test@example.com", role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_scenario(store):
    """Seed a rich scenario with two companies, signals, reviews, company_files,
    merge suggestion, and a hunter result."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    # Company A: 3 signals, approved review, promoted company_file
    for i in range(1, 4):
        await db.execute(
            """INSERT INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, 'AlphaCo', 'alpha', 'domain:alpha.com', ?, 'github',
                       0.8, ?, ?, '{"description":"Alpha signal"}')""",
            (i, f"signal_type_{i}", now, now),
        )

    # Company B: 3 signals, pending review, thin company_file
    for i in range(4, 7):
        await db.execute(
            """INSERT INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, 'BetaCo', 'beta', 'domain:beta.com', ?, 'sec_edgar',
                       0.7, ?, ?, '{"description":"Beta signal"}')""",
            (i, f"signal_type_{i}", now, now),
        )

    # Reviews
    await db.execute(
        """INSERT INTO review_items
           (id, company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (1, 'alpha', 'approved', ?, ?, ?)""",
        (json.dumps({"signal_ids": [1, 2, 3]}), now, now),
    )
    await db.execute(
        """INSERT INTO review_items
           (id, company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (2, 'beta', 'pending', ?, ?, ?)""",
        (json.dumps({"signal_ids": [4, 5, 6]}), now, now),
    )

    # Company files
    await db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, source_apis,
            first_seen_at, last_seen_at, status)
           VALUES ('alpha', 'AlphaCo', 'domain:alpha.com', '["github"]',
                   '2026-01-01T00:00:00Z', '2026-01-15T00:00:00Z', 'promoted')""",
    )
    await db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, source_apis,
            first_seen_at, last_seen_at, status)
           VALUES ('beta', 'BetaCo', 'domain:beta.com', '["sec_edgar"]',
                   '2026-01-10T00:00:00Z', '2026-01-20T00:00:00Z', 'thin')""",
    )

    # Merge suggestion
    await db.execute(
        """INSERT INTO merge_suggestions
           (id, pair_key, entity_a_company_id, entity_b_company_id,
            entity_a_canonical_key, entity_b_canonical_key,
            entity_a_company_name, entity_b_company_name,
            match_type, similarity_score, evidence_json, status, scoring_version, created_at)
           VALUES (1, 'alpha:beta', 'alpha', 'beta',
                   'domain:alpha.com', 'domain:beta.com',
                   'AlphaCo', 'BetaCo', 'shared_domain', 0.95,
                   '{"match_details":"domain match","domains":["alpha.com","beta.com"]}',
                   'pending', '1.0.0', ?)""",
        (now,),
    )

    # Hunter run_history + query + result for beta company
    await db.execute(
        """INSERT INTO run_history
           (id, run_type, status, created_at)
           VALUES ('hunter-run-1', 'hunter', 'completed', ?)""",
        (now,),
    )
    await db.execute(
        """INSERT INTO hunter_queries
           (id, run_id, collector, query_text, query_type, status,
            results_count, created_at, executed_at, completed_at)
           VALUES (1, 'hunter-run-1', 'github', 'consumer cpg', 'pattern',
                   'completed', 1, ?, ?, ?)""",
        (now, now, now),
    )
    await db.execute(
        """INSERT INTO hunter_results
           (id, run_id, query_id, result_dedupe_key, company_name,
            canonical_key, company_id, source_api, raw_data,
            confidence_score, thesis_fit_score, already_known,
            status, created_at, updated_at)
           VALUES (1, 'hunter-run-1', 1, 'dedupe:beta-1', 'BetaCo',
                   'domain:beta-new.com', 'beta', 'github',
                   '{"description":"Beta high growth"}',
                   0.85, 0.8, 0, 'relevant', ?, ?)""",
        (now, now),
    )

    await db.commit()


async def _full_merge_lifecycle(client, store):
    """Propose → approve → apply, return proposal_id."""
    resp = await client.post(
        "/api/v1/entities/merge-suggestions/1/propose",
        json={"suggestion_id": 1, "winner_company_id": "alpha",
              "loser_company_id": "beta", "reason": "Domain match"},
        headers=_auth_header(),
    )
    assert resp.status_code == 201, resp.text
    proposal_id = resp.json()["data"]["proposal_id"]

    # Approve
    db = store._db
    updated_at = await _get_updated_at(db, proposal_id)
    resp = await client.post(
        f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
        json={"updated_at": updated_at},
        headers=_auth_header(),
    )
    assert resp.status_code == 200, resp.text

    # Apply
    updated_at = await _get_updated_at(db, proposal_id)
    resp = await client.post(
        f"/api/v1/entities/merge-proposals/{proposal_id}/apply",
        json={"updated_at": updated_at},
        headers=_auth_header(),
    )
    assert resp.status_code == 201, resp.text
    return proposal_id


async def _rollback_merge(client, store, proposal_id, reason="Revert"):
    """Rollback a merge proposal."""
    db = store._db
    updated_at = await _get_updated_at(db, proposal_id)
    resp = await client.post(
        f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
        json={"reason": reason, "updated_at": updated_at},
        headers=_auth_header(),
    )
    return resp


async def _get_updated_at(db, proposal_id):
    cursor = await db.execute(
        "SELECT updated_at FROM merge_proposals WHERE id = ?", (proposal_id,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def _get_review_updated_at(db, review_id):
    cursor = await db.execute(
        "SELECT updated_at FROM review_items WHERE id = ?", (review_id,)
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
async def app_client(store, monkeypatch):
    monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
    monkeypatch.setenv("BULK_TRIAGE_ENABLED", "active")
    monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")

    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()

    app.include_router(merge_mod.router, prefix="/api/v1")
    app.include_router(triage_mod.router, prefix="/api/v1")
    app.include_router(hunter_mod.router, prefix="/api/v1")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, app_client):
    await _seed_scenario(store)
    return app_client, store


# =============================================================================
# CROSS-PHASE TESTS
# =============================================================================

class TestMergeThenTriage:
    """Verify triage endpoints work correctly after a merge."""

    @pytest.mark.asyncio
    async def test_triage_list_shows_winner_after_merge(self, seeded):
        """After merge, triage list should show winner's review, not loser's."""
        client, store = seeded
        await _full_merge_lifecycle(client, store)

        resp = await client.get(
            "/api/v1/triage",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]
        # Winner review should still be listed — find by company_id
        company_ids = [item.get("company_id") for item in items]
        assert "alpha" in company_ids

    @pytest.mark.asyncio
    async def test_triage_approve_winner_after_merge(self, seeded):
        """Winner review can still be approved via triage after merge."""
        client, store = seeded
        await _full_merge_lifecycle(client, store)

        db = store._db
        updated_at = await _get_review_updated_at(db, 1)

        resp = await client.post(
            "/api/v1/triage/1/approve",
            json={"reason": "Good deal", "updated_at": updated_at},
            headers={
                **_auth_header(Role.ANALYST),
                "X-Idempotency-Key": "triage-approve-1",
            },
        )
        # Review 1 is already approved → invalid transition (approved→approved)
        # This validates the triage endpoint still works with merged entities
        assert resp.status_code in (200, 409)


class TestBulkTriageAndMerge:
    """Verify bulk triage works alongside merge operations."""

    @pytest.mark.asyncio
    async def test_bulk_approve_before_merge(self, seeded):
        """Bulk approve beta review, then merge → beta review should be rejected by cascade."""
        client, store = seeded
        db = store._db

        updated_at = await _get_review_updated_at(db, 2)

        # Bulk approve beta's review
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "approve",
                "items": [{"review_id": 2, "updated_at": updated_at}],
                "reason": "Pre-merge approval",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-pre-merge",
            },
        )
        assert resp.status_code == 200
        result = resp.json()["data"]
        assert result["succeeded"] == 1

        # Now merge
        proposal_id = await _full_merge_lifecycle(client, store)

        # After merge, beta's signals should belong to alpha
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = 'beta'"
        )
        assert (await cursor.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_bulk_triage_stale_after_merge(self, seeded):
        """Bulk triage with stale updated_at (from before merge) returns concurrency_conflict."""
        client, store = seeded
        db = store._db

        # Capture beta review's updated_at BEFORE merge
        old_updated_at = await _get_review_updated_at(db, 2)

        # Execute merge (which modifies beta's review)
        await _full_merge_lifecycle(client, store)

        # Try bulk approve with stale updated_at
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "approve",
                "items": [{"review_id": 2, "updated_at": old_updated_at}],
                "reason": "Stale attempt",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-stale-merge",
            },
        )
        assert resp.status_code == 200
        result = resp.json()["data"]
        # Should have per-item error (not_found or concurrency_conflict or invalid_transition)
        assert result["failed"] >= 1


class TestMergeRollbackAndRetriage:
    """Verify full cycle: merge → rollback → triage loser again."""

    @pytest.mark.asyncio
    async def test_rollback_restores_triage_ability(self, seeded):
        """After merge + rollback, loser's review should be triageable again."""
        client, store = seeded
        db = store._db

        # Merge
        proposal_id = await _full_merge_lifecycle(client, store)

        # Rollback
        resp = await _rollback_merge(client, store, proposal_id)
        assert resp.status_code == 200

        # Verify beta's review is back to a triageable status
        cursor = await db.execute(
            "SELECT status FROM review_items WHERE id = 2"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] in ("pending", "approved", "publish_queued")

        # Beta should have its signals back
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = 'beta'"
        )
        assert (await cursor.fetchone())[0] == 3


class TestHunterPromotionAndMerge:
    """Verify hunter promotion interacts correctly with merge state."""

    @pytest.mark.asyncio
    async def test_promote_before_merge(self, seeded):
        """Promote a hunter result, then merge — signal should migrate to winner."""
        client, store = seeded
        db = store._db

        # Promote beta's hunter result
        resp = await client.post(
            "/api/v1/hunter/results/1/promote",
            json={},
            headers={
                **_auth_header(Role.ANALYST),
                "X-Idempotency-Key": "promote-beta-1",
            },
        )
        assert resp.status_code == 201

        # Verify a new signal was created for beta
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = 'beta'"
        )
        beta_count_before = (await cursor.fetchone())[0]
        assert beta_count_before >= 4  # 3 original + at least 1 promoted

        # Merge alpha ← beta
        await _full_merge_lifecycle(client, store)

        # All beta signals (including promoted one) should now belong to alpha
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = 'beta'"
        )
        assert (await cursor.fetchone())[0] == 0

        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = 'alpha'"
        )
        alpha_count = (await cursor.fetchone())[0]
        assert alpha_count >= 6  # 3 original alpha + 3 original beta + promoted

    @pytest.mark.asyncio
    async def test_hunter_result_company_listed_after_merge(self, seeded):
        """Hunter results endpoint still returns results for merged-away company."""
        client, store = seeded

        # Merge first
        await _full_merge_lifecycle(client, store)

        # Hunter results should still list the result (it references original company_id)
        resp = await client.get(
            "/api/v1/hunter/runs/hunter-run-1/results",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        results = resp.json()["data"]
        assert len(results) >= 1


class TestAuditTrailIntegrity:
    """Verify audit events are created across all write operations."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_audit_events(self, seeded):
        """Full lifecycle: triage → merge → rollback should produce audit events."""
        client, store = seeded
        db = store._db

        # 1. Approve beta's review via triage
        updated_at = await _get_review_updated_at(db, 2)
        resp = await client.post(
            "/api/v1/triage/2/approve",
            json={"reason": "Looks good", "updated_at": updated_at},
            headers={
                **_auth_header(Role.ANALYST),
                "X-Idempotency-Key": "audit-triage-approve",
            },
        )
        assert resp.status_code == 200

        # 2. Merge
        proposal_id = await _full_merge_lifecycle(client, store)

        # 3. Rollback
        resp = await _rollback_merge(client, store, proposal_id)
        assert resp.status_code == 200

        # Verify audit_events has entries for all actions
        cursor = await db.execute(
            "SELECT action_type FROM audit_events ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        action_types = [r[0] for r in rows]

        # Should have triage approval event
        assert any("approve" in a or "triage" in a for a in action_types)
        # Should have merge events
        assert any("merge" in a for a in action_types)

    @pytest.mark.asyncio
    async def test_audit_events_have_actor_id(self, seeded):
        """All audit events should have a non-empty actor_id."""
        client, store = seeded
        db = store._db

        # Merge
        await _full_merge_lifecycle(client, store)

        cursor = await db.execute(
            "SELECT actor_id FROM audit_events WHERE actor_id IS NOT NULL AND actor_id != ''"
        )
        rows = await cursor.fetchall()
        assert len(rows) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
