"""Tests for functional schema storage methods in SignalStore.

Verifies:
- save_functional_schema() + get_active_schema() round-trip
- Advisory flag stored correctly
- Schema history returns all versions ordered
- has_active_schema() lightweight check
- Evidence signal_id validation (rejects wrong company)
- Empty evidence_signal_ids succeeds
- Auto-incrementing schema_version per company
"""

import os
import sys
import json
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_signal(store, signal_id, company_id, canonical_key="domain:test.com"):
    """Helper: insert a minimal signal row for evidence validation tests."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await store._db.execute(
        """INSERT INTO signals (id, signal_type, source_api, canonical_key,
               company_name, confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, 'funding', 'sec_edgar', ?, 'Test Co', 0.7, '{}', ?, ?, ?)""",
        (signal_id, canonical_key, now, now, company_id),
    )
    await store._db.commit()


class TestSaveFunctionalSchema:
    """Tests for save_functional_schema()."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, store):
        """Save schema then retrieve via get_active_schema."""
        schema = {
            "company_id": "comp-100",
            "problem_solved_text": "Helps creators monetize",
            "customer_text": "Individual content creators",
            "approach_text": "Subscription-based platform",
            "customer_archetype": "creators",
            "problem_archetypes": ["content_monetization", "creator_economy"],
            "schema_confidence": 0.85,
            "is_advisory": False,
            "extraction_model": "gemini-2.0-flash",
            "extraction_prompt_version": "v1.0.0-func-schema",
        }
        row_id = await store.save_functional_schema(schema)
        assert row_id is not None
        assert row_id > 0

        active = await store.get_active_schema("comp-100")
        assert active is not None
        assert active["company_id"] == "comp-100"
        assert active["problem_solved_text"] == "Helps creators monetize"
        assert active["customer_archetype"] == "creators"
        assert active["problem_archetypes"] == ["content_monetization", "creator_economy"]
        assert active["schema_confidence"] == 0.85
        assert active["is_advisory"] is False
        assert active["is_active"] is True
        assert active["schema_version"] == 1
        assert active["extraction_model"] == "gemini-2.0-flash"

    @pytest.mark.asyncio
    async def test_advisory_flag_stored(self, store):
        """Advisory schema (low confidence) should have is_advisory=True."""
        schema = {
            "company_id": "comp-101",
            "problem_solved_text": "Some vague description",
            "customer_archetype": "unknown",
            "schema_confidence": 0.3,
            "is_advisory": True,
        }
        await store.save_functional_schema(schema)

        active = await store.get_active_schema("comp-101")
        assert active["is_advisory"] is True
        assert active["schema_confidence"] == 0.3

    @pytest.mark.asyncio
    async def test_auto_increment_version(self, store):
        """Second save for same company gets schema_version=2."""
        for i in range(3):
            await store.save_functional_schema({
                "company_id": "comp-102",
                "customer_archetype": f"archetype_{i}",
                "schema_confidence": 0.8,
            })

        history = await store.get_schema_history("comp-102")
        assert len(history) == 3
        assert [h["schema_version"] for h in history] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_empty_evidence_succeeds(self, store):
        """Save with no evidence_signal_ids should succeed."""
        row_id = await store.save_functional_schema({
            "company_id": "comp-103",
            "customer_archetype": "foodies",
            "schema_confidence": 0.9,
        })
        assert row_id > 0

        active = await store.get_active_schema("comp-103")
        assert active["evidence_signal_ids"] is None

    @pytest.mark.asyncio
    async def test_evidence_validation_rejects_wrong_company(self, store):
        """evidence_signal_ids from a different company should raise ValueError."""
        await _insert_signal(store, 901, "comp-AAA", canonical_key="domain:aaa.com")
        await _insert_signal(store, 902, "comp-BBB", canonical_key="domain:bbb.com")

        with pytest.raises(ValueError, match="do not belong to company_id"):
            await store.save_functional_schema({
                "company_id": "comp-AAA",
                "customer_archetype": "creators",
                "schema_confidence": 0.8,
                "evidence_signal_ids": [901, 902],  # 902 belongs to comp-BBB
            })

    @pytest.mark.asyncio
    async def test_evidence_validation_accepts_correct_company(self, store):
        """evidence_signal_ids all belonging to the company should succeed."""
        await _insert_signal(store, 903, "comp-CCC")
        await _insert_signal(store, 904, "comp-CCC", canonical_key="domain:other.com")

        row_id = await store.save_functional_schema({
            "company_id": "comp-CCC",
            "customer_archetype": "travelers",
            "schema_confidence": 0.75,
            "evidence_signal_ids": [903, 904],
        })
        assert row_id > 0

        active = await store.get_active_schema("comp-CCC")
        assert active["evidence_signal_ids"] == [903, 904]


class TestGetActiveSchema:
    """Tests for get_active_schema()."""

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self, store):
        """Unknown company returns None."""
        result = await store.get_active_schema("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_latest_active_version(self, store):
        """When multiple versions exist, returns highest active version."""
        await store.save_functional_schema({
            "company_id": "comp-200",
            "customer_archetype": "foodies",
            "schema_confidence": 0.7,
        })
        await store.save_functional_schema({
            "company_id": "comp-200",
            "customer_archetype": "travelers",
            "schema_confidence": 0.9,
        })

        active = await store.get_active_schema("comp-200")
        assert active["schema_version"] == 2
        assert active["customer_archetype"] == "travelers"


class TestGetSchemaHistory:
    """Tests for get_schema_history()."""

    @pytest.mark.asyncio
    async def test_empty_history(self, store):
        """Unknown company returns empty list."""
        result = await store.get_schema_history("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_ordered_by_version(self, store):
        """History returned in ascending version order."""
        for archetype in ["creators", "foodies", "travelers"]:
            await store.save_functional_schema({
                "company_id": "comp-300",
                "customer_archetype": archetype,
                "schema_confidence": 0.8,
            })

        history = await store.get_schema_history("comp-300")
        assert len(history) == 3
        assert history[0]["customer_archetype"] == "creators"
        assert history[1]["customer_archetype"] == "foodies"
        assert history[2]["customer_archetype"] == "travelers"


class TestHasActiveSchema:
    """Tests for has_active_schema()."""

    @pytest.mark.asyncio
    async def test_false_for_unknown(self, store):
        """Unknown company returns False."""
        assert await store.has_active_schema("nonexistent") is False

    @pytest.mark.asyncio
    async def test_true_after_save(self, store):
        """Returns True after saving a schema."""
        await store.save_functional_schema({
            "company_id": "comp-400",
            "customer_archetype": "gamers",
            "schema_confidence": 0.8,
        })
        assert await store.has_active_schema("comp-400") is True

    @pytest.mark.asyncio
    async def test_false_after_deactivation(self, store):
        """Returns False if all schemas deactivated."""
        await store.save_functional_schema({
            "company_id": "comp-401",
            "customer_archetype": "shoppers",
            "schema_confidence": 0.8,
        })

        # Manually deactivate
        await store._db.execute(
            "UPDATE functional_schemas SET is_active = 0 WHERE company_id = ?",
            ("comp-401",),
        )
        await store._db.commit()

        assert await store.has_active_schema("comp-401") is False
