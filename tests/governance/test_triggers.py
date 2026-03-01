"""Tests for v47 governance triggers — DB-level metadata enforcement."""

import json
import os
import tempfile

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore


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


class TestFeaturePromoteTrigger:
    @pytest.mark.asyncio
    async def test_rejects_null_metadata(self, store):
        with pytest.raises(Exception, match="metadata must not be NULL"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, created_at) "
                "VALUES ('feature_promote', 'feature_flag', 'x', 'u1', '2026-01-01')"
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self, store):
        with pytest.raises(Exception, match="metadata must be valid JSON"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
                "VALUES ('feature_promote', 'feature_flag', 'x', 'u1', 'not-json', '2026-01-01')"
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_regret_due_at(self, store):
        meta = json.dumps({
            "feature_name": "x", "from_state": "shadow",
            "to_state": "active", "config_snapshot_hash": "h",
        })
        with pytest.raises(Exception, match="regret_due_at is required"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
                "VALUES ('feature_promote', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
                (meta,),
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_from_state(self, store):
        meta = json.dumps({
            "feature_name": "x", "to_state": "active",
            "regret_due_at": "2026-03-14", "config_snapshot_hash": "h",
        })
        with pytest.raises(Exception, match="from_state is required"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
                "VALUES ('feature_promote', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
                (meta,),
            )

    @pytest.mark.asyncio
    async def test_accepts_valid_promote(self, store):
        meta = json.dumps({
            "action_type": "feature_promote",
            "feature_name": "x", "from_state": "shadow", "to_state": "active",
            "regret_due_at": "2026-03-14", "config_snapshot_hash": "h",
        })
        cursor = await store._db.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
            "VALUES ('feature_promote', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
            (meta,),
        )
        assert cursor.lastrowid > 0


class TestRegretCheckTrigger:
    @pytest.mark.asyncio
    async def test_rejects_missing_verdict(self, store):
        meta = json.dumps({"canary_verdict": "pass", "drift_status": "in_control"})
        with pytest.raises(Exception, match="verdict is required"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
                "VALUES ('regret_check', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
                (meta,),
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_canary_verdict(self, store):
        meta = json.dumps({"verdict": "pass", "drift_status": "in_control"})
        with pytest.raises(Exception, match="canary_verdict is required"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
                "VALUES ('regret_check', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
                (meta,),
            )

    @pytest.mark.asyncio
    async def test_accepts_valid_regret_check(self, store):
        meta = json.dumps({
            "action_type": "regret_check",
            "verdict": "pass", "canary_verdict": "pass",
            "drift_status": "in_control", "window_days": 14,
        })
        cursor = await store._db.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
            "VALUES ('regret_check', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
            (meta,),
        )
        assert cursor.lastrowid > 0


class TestFeatureDemoteTrigger:
    @pytest.mark.asyncio
    async def test_rejects_missing_from_state(self, store):
        meta = json.dumps({"to_state": "shadow"})
        with pytest.raises(Exception, match="from_state is required"):
            await store._db.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
                "VALUES ('feature_demote', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
                (meta,),
            )

    @pytest.mark.asyncio
    async def test_accepts_valid_demote(self, store):
        meta = json.dumps({
            "action_type": "feature_demote",
            "from_state": "active", "to_state": "shadow",
        })
        cursor = await store._db.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
            "VALUES ('feature_demote', 'feature_flag', 'x', 'u1', ?, '2026-01-01')",
            (meta,),
        )
        assert cursor.lastrowid > 0


class TestNonGovernanceEventsUnaffected:
    @pytest.mark.asyncio
    async def test_triage_approve_no_trigger(self, store):
        """Non-governance action types pass through without trigger checks."""
        cursor = await store._db.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, created_at) "
            "VALUES ('triage_approve', 'review_item', '42', 'u1', '2026-01-01')"
        )
        assert cursor.lastrowid > 0

    @pytest.mark.asyncio
    async def test_batch_commit_no_trigger(self, store):
        """batch_commit (non-governance) passes through."""
        cursor = await store._db.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, created_at) "
            "VALUES ('batch_commit', 'batch', 'b-1', 'admin-1', '2026-01-01')"
        )
        assert cursor.lastrowid > 0
