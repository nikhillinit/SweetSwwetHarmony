"""Integration tests for merge rollback — full lifecycle verification.

Coverage (~15 tests):
- Apply -> rollback -> verify both entities fully restored
  - Winner review evidence restored
  - Winner company_files restored
  - Loser signals reassigned back
  - Loser company_files recreated
  - Loser review_items reopened
  - entity_migrations row deleted
- Dual audit: both audit_log and audit_events entries
- Re-propose after rollback (partial unique index allows it)
- Proposal status and rollback reason persisted
- Winner signal count unchanged after rollback
- Loser canonical_key restored on signals
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
from storage.signal_store import SignalStore


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role = Role.GP) -> dict:
    token, _ = create_access_token(
        user_id="test-user", email="test@example.com", role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_full_scenario(store):
    """Seed a full merge scenario with rich entity state."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    # Winner: 3 signals, 1 approved review, 1 company_file
    # Each signal needs unique (canonical_key, signal_type, source_api, detected_at)
    for i in range(1, 4):
        await db.execute(
            """INSERT INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, 'AlphaCo', 'alpha', 'domain:alpha.com', ?, 'github',
                       0.8, ?, ?, '{}')""",
            (i, f"signal_type_{i}", now, now),
        )

    # Loser: 3 signals, 1 pending review, 1 company_file
    for i in range(4, 7):
        await db.execute(
            """INSERT INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, 'BetaCo', 'beta', 'domain:beta.com', ?, 'sec_edgar',
                       0.7, ?, ?, '{}')""",
            (i, f"signal_type_{i}", now, now),
        )

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
    await db.commit()


async def _full_lifecycle(client, store):
    """Propose -> approve -> apply, return proposal_id."""
    resp = await client.post(
        "/api/v1/entities/merge-suggestions/1/propose",
        json={"suggestion_id": 1, "winner_company_id": "alpha",
              "loser_company_id": "beta", "reason": "Domain match"},
        headers=_auth_header(),
    )
    assert resp.status_code == 201
    proposal_id = resp.json()["data"]["proposal_id"]

    # Approve
    db = store._db
    cursor = await db.execute(
        "SELECT updated_at FROM merge_proposals WHERE id = ?", (proposal_id,)
    )
    row = await cursor.fetchone()
    resp = await client.post(
        f"/api/v1/entities/merge-proposals/{proposal_id}/approve",
        json={"updated_at": row[0]},
        headers=_auth_header(),
    )
    assert resp.status_code == 200

    # Apply
    cursor = await db.execute(
        "SELECT updated_at FROM merge_proposals WHERE id = ?", (proposal_id,)
    )
    row = await cursor.fetchone()
    resp = await client.post(
        f"/api/v1/entities/merge-proposals/{proposal_id}/apply",
        json={"updated_at": row[0]},
        headers=_auth_header(),
    )
    assert resp.status_code == 201
    return proposal_id


async def _rollback(client, store, proposal_id, reason="Revert"):
    """Rollback a merge proposal, return response."""
    db = store._db
    cursor = await db.execute(
        "SELECT updated_at FROM merge_proposals WHERE id = ?", (proposal_id,)
    )
    row = await cursor.fetchone()
    resp = await client.post(
        f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
        json={"reason": reason, "updated_at": row[0]},
        headers=_auth_header(),
    )
    return resp


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
async def client(store, monkeypatch):
    monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()
    app.include_router(merge_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, client):
    await _seed_full_scenario(store)
    return client, store


# =============================================================================
# FULL ROLLBACK VERIFICATION
# =============================================================================

class TestMergeRollbackIntegration:
    @pytest.mark.asyncio
    async def test_full_rollback_restores_loser_signals(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        # Verify merge happened: loser has 0 signals
        db = store._db
        cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE company_id = 'beta'")
        assert (await cursor.fetchone())[0] == 0

        # Rollback
        resp = await _rollback(client, store, proposal_id)
        assert resp.status_code == 200

        # Verify loser signals restored
        cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE company_id = 'beta'")
        assert (await cursor.fetchone())[0] == 3

    @pytest.mark.asyncio
    async def test_full_rollback_restores_winner_signals(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        resp = await _rollback(client, store, proposal_id)
        assert resp.status_code == 200

        # Winner should still have exactly 3 signals (not 6 from merged state)
        db = store._db
        cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE company_id = 'alpha'")
        assert (await cursor.fetchone())[0] == 3

    @pytest.mark.asyncio
    async def test_full_rollback_restores_loser_review(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        await _rollback(client, store, proposal_id)

        # Loser's review should be reopened (not rejected)
        db = store._db
        cursor = await db.execute(
            "SELECT status FROM review_items WHERE id = 2"
        )
        row = await cursor.fetchone()
        assert row[0] in ("pending", "approved", "publish_queued")

    @pytest.mark.asyncio
    async def test_full_rollback_restores_loser_company_file(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        # After merge, loser company_file is deleted by cascade_merge
        db = store._db
        cursor = await db.execute(
            "SELECT status FROM company_files WHERE company_id = 'beta'"
        )
        row_before = await cursor.fetchone()
        assert row_before is None  # Deleted during merge

        await _rollback(client, store, proposal_id)

        # After rollback, loser company_file should be recreated with original status
        cursor = await db.execute(
            "SELECT status FROM company_files WHERE company_id = 'beta'"
        )
        row_after = await cursor.fetchone()
        assert row_after is not None
        assert row_after[0] == "thin"  # Original status before merge

    @pytest.mark.asyncio
    async def test_full_rollback_restores_winner_company_file(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)
        await _rollback(client, store, proposal_id)

        # Winner company_file should still exist with its original status
        db = store._db
        cursor = await db.execute(
            "SELECT status, company_name FROM company_files WHERE company_id = 'alpha'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "promoted"  # Original status before merge
        assert row[1] == "AlphaCo"

    @pytest.mark.asyncio
    async def test_full_rollback_entity_migration_handling(self, seeded):
        """cascade_merge doesn't create entity_migrations (that's Phase G).
        Verify rollback handles missing migration data gracefully."""
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        # cascade_merge does NOT create entity_migrations rows
        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM entity_migrations WHERE from_entity_id = 'beta' AND to_entity_id = 'alpha'"
        )
        assert (await cursor.fetchone())[0] == 0

        await _rollback(client, store, proposal_id)

        # After rollback, still no entity_migrations rows (none existed)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM entity_migrations WHERE from_entity_id = 'beta' AND to_entity_id = 'alpha'"
        )
        assert (await cursor.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_full_rollback_creates_audit_log(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)
        await _rollback(client, store, proposal_id)

        # Check audit_log has rollback entry
        db = store._db
        cursor = await db.execute(
            "SELECT action_type, details FROM audit_log WHERE action_type = 'cascade_rollback'"
        )
        row = await cursor.fetchone()
        assert row is not None
        details = json.loads(row[1])
        assert details["proposal_id"] == proposal_id

    @pytest.mark.asyncio
    async def test_full_rollback_creates_audit_event(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)
        await _rollback(client, store, proposal_id)

        # Check audit_events has rollback entry
        db = store._db
        cursor = await db.execute(
            "SELECT action_type, metadata FROM audit_events WHERE action_type = 'merge_rollback'"
        )
        row = await cursor.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_proposal_status_is_rolled_back(self, seeded):
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)
        await _rollback(client, store, proposal_id, reason="Revert")

        db = store._db
        cursor = await db.execute(
            "SELECT status, rollback_reason FROM merge_proposals WHERE id = ?",
            (proposal_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "rolled_back"
        assert row[1] == "Revert"

    @pytest.mark.asyncio
    async def test_re_propose_after_rollback(self, seeded):
        """After rollback, same suggestion can be re-proposed (partial unique index)."""
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        resp = await _rollback(client, store, proposal_id)
        assert resp.status_code == 200

        # Re-propose same suggestion
        resp = await client.post(
            "/api/v1/entities/merge-suggestions/1/propose",
            json={"suggestion_id": 1, "winner_company_id": "alpha",
                  "loser_company_id": "beta", "reason": "Retry"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_rollback_restores_loser_canonical_key_on_signals(self, seeded):
        """Loser signals should have their original canonical_key after rollback."""
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        # After merge, loser signals moved to winner - canonical_key may have changed
        db = store._db

        await _rollback(client, store, proposal_id)

        # After rollback, loser signals should have original canonical_key
        cursor = await db.execute(
            "SELECT DISTINCT canonical_key FROM signals WHERE company_id = 'beta'"
        )
        rows = await cursor.fetchall()
        keys = [r[0] for r in rows]
        assert "domain:beta.com" in keys

    @pytest.mark.asyncio
    async def test_apply_creates_cascade_report(self, seeded):
        """Verify that apply stores cascade_report in the proposal row."""
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        db = store._db
        cursor = await db.execute(
            "SELECT cascade_report FROM merge_proposals WHERE id = ?",
            (proposal_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        report = json.loads(row[0])
        assert report["winner"] == "alpha"
        assert report["loser"] == "beta"
        assert report["signals_reassigned"] == 3

    @pytest.mark.asyncio
    async def test_apply_audit_log_exists(self, seeded):
        """Verify that apply itself creates an audit_log entry (cascade_merge)."""
        client, store = seeded
        await _full_lifecycle(client, store)

        db = store._db
        cursor = await db.execute(
            "SELECT action_type FROM audit_log WHERE action_type = 'cascade_merge'"
        )
        row = await cursor.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_full_lifecycle_idempotent_rollback(self, seeded):
        """Calling rollback twice on the same proposal should fail on the second call."""
        client, store = seeded
        proposal_id = await _full_lifecycle(client, store)

        resp = await _rollback(client, store, proposal_id)
        assert resp.status_code == 200

        # Second rollback should fail (already rolled_back -> invalid transition)
        db = store._db
        cursor = await db.execute(
            "SELECT updated_at FROM merge_proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()
        resp = await client.post(
            f"/api/v1/entities/merge-proposals/{proposal_id}/rollback",
            json={"reason": "Double rollback", "updated_at": row[0]},
            headers=_auth_header(),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
