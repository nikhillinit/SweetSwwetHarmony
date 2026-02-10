"""Tests for Hunter Promotion Bridge — the ONLY module writing to signals."""

import json
import pytest

from storage.signal_store import SignalStore
from storage.hunter_result_store import (
    InvalidHunterTransition,
    create_query,
    create_result,
    update_result_status,
    get_result_by_id,
)
from workflows.hunter_promotion import promote_hunter_result


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    await s._db.execute(
        "INSERT INTO run_history (id, run_type, status, created_at) VALUES (?, ?, ?, ?)",
        ("run1", "hunter", "queued", "2026-01-01T00:00:00Z"),
    )
    await s._db.commit()
    yield s
    await s.close()


async def _create_relevant_result(store, canonical_key="domain:acme.ai", company_name="Acme Inc"):
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )
    rid = await create_result(
        store, run_id="run1", query_id=qid,
        company_name=company_name, source_api="github",
        raw_data={"url": "https://github.com/acme", "description": "consumer brand"},
        canonical_key=canonical_key,
        confidence_score=0.8,
    )
    await update_result_status(store, rid, "relevant")
    return rid


class TestSuccessfulPromotion:
    @pytest.mark.asyncio
    async def test_promote_creates_signal(self, store):
        rid = await _create_relevant_result(store)
        result = await promote_hunter_result(store, rid)

        assert result.success is True
        assert result.status == "promoted"
        assert result.signal_id is not None

        # Verify signal exists in signals table
        cursor = await store._db.execute(
            "SELECT id, canonical_key, signal_type FROM signals WHERE id = ?",
            (result.signal_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "domain:acme.ai"
        assert row[2] == "hunter_discovery"

    @pytest.mark.asyncio
    async def test_promote_updates_result(self, store):
        rid = await _create_relevant_result(store)
        result = await promote_hunter_result(store, rid)

        hr = await get_result_by_id(store, rid)
        assert hr["status"] == "promoted"
        assert hr["promoted_signal_id"] == result.signal_id
        assert hr["promoted_at"] is not None


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_already_promoted_returns_existing(self, store):
        rid = await _create_relevant_result(store)
        result1 = await promote_hunter_result(store, rid, idempotency_key="key1")
        result2 = await promote_hunter_result(store, rid, idempotency_key="key1")

        assert result2.success is True
        assert result2.status == "already_promoted"
        assert result2.signal_id == result1.signal_id

    @pytest.mark.asyncio
    async def test_auto_idempotency_key(self, store):
        rid = await _create_relevant_result(store)
        result1 = await promote_hunter_result(store, rid)
        result2 = await promote_hunter_result(store, rid)

        assert result2.success is True
        assert result2.signal_id == result1.signal_id


class TestWrongStatus:
    @pytest.mark.asyncio
    async def test_pending_result_rejected(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Test", source_api="github", raw_data={},
        )
        with pytest.raises(InvalidHunterTransition, match="expected 'relevant'"):
            await promote_hunter_result(store, rid)

    @pytest.mark.asyncio
    async def test_not_relevant_result_rejected(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Test", source_api="github", raw_data={},
        )
        await update_result_status(store, rid, "not_relevant")
        with pytest.raises(InvalidHunterTransition):
            await promote_hunter_result(store, rid)


class TestCanonicalCollision:
    @pytest.mark.asyncio
    async def test_collision_sets_already_known(self, store):
        # Pre-seed a signal with the same canonical key
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("spike", "github", "domain:acme.ai", "Acme",
             0.8, "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await store._db.commit()

        rid = await _create_relevant_result(store, canonical_key="domain:acme.ai")
        result = await promote_hunter_result(store, rid)

        assert result.success is True
        assert result.collision is True
        assert result.status == "already_known"

        hr = await get_result_by_id(store, rid)
        assert hr["status"] == "already_known"
        assert hr["promoted_signal_id"] is not None


class TestAuditEvent:
    @pytest.mark.asyncio
    async def test_promotion_audit_event(self, store):
        rid = await _create_relevant_result(store)
        await promote_hunter_result(store, rid)

        cursor = await store._db.execute(
            "SELECT action_type FROM audit_events WHERE action_type = 'hunter_promote'"
        )
        row = await cursor.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_collision_audit_event(self, store):
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("spike", "github", "domain:collision.ai", "Col",
             0.8, "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await store._db.commit()

        rid = await _create_relevant_result(store, canonical_key="domain:collision.ai")
        await promote_hunter_result(store, rid)

        cursor = await store._db.execute(
            "SELECT action_type FROM audit_events WHERE action_type = 'hunter_promote_collision'"
        )
        row = await cursor.fetchone()
        assert row is not None


class TestNotFound:
    @pytest.mark.asyncio
    async def test_nonexistent_result(self, store):
        with pytest.raises(ValueError, match="not found"):
            await promote_hunter_result(store, 99999)


class TestNullCanonicalKey:
    @pytest.mark.asyncio
    async def test_promote_without_canonical(self, store):
        rid = await _create_relevant_result(store, canonical_key=None, company_name="NoCK Corp")
        result = await promote_hunter_result(store, rid)
        assert result.success is True
        assert result.signal_id is not None

        cursor = await store._db.execute(
            "SELECT canonical_key FROM signals WHERE id = ?", (result.signal_id,)
        )
        row = await cursor.fetchone()
        assert row[0].startswith("name_loc:")
