"""Tests for operator feedback and negative keyword extraction."""

import json
import pytest

from storage.signal_store import SignalStore
from storage.hunter_result_store import (
    InvalidHunterTransition,
    StaleUpdateError,
    create_query,
    create_result,
    update_result_status,
    get_result_by_id,
    extract_negative_keywords_from_rejection,
    get_active_negative_keywords,
    _extract_candidate_keywords,
)


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


async def _create_test_result(store, company_name="TestCo", raw_data=None, canonical_key=None):
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )
    return await create_result(
        store, run_id="run1", query_id=qid,
        company_name=company_name, source_api="github",
        raw_data=raw_data or {"description": "test company"},
        canonical_key=canonical_key,
    )


class TestFeedbackFlow:
    @pytest.mark.asyncio
    async def test_relevant_feedback(self, store):
        rid = await _create_test_result(store)
        await update_result_status(store, rid, "relevant", operator_feedback="Looks good")
        result = await get_result_by_id(store, rid)
        assert result["status"] == "relevant"
        assert result["operator_feedback"] == "Looks good"
        assert result["reviewed_at"] is not None

    @pytest.mark.asyncio
    async def test_not_relevant_feedback(self, store):
        rid = await _create_test_result(store)
        await update_result_status(store, rid, "not_relevant", operator_feedback="B2B SaaS")
        result = await get_result_by_id(store, rid)
        assert result["status"] == "not_relevant"

    @pytest.mark.asyncio
    async def test_invalid_transition_rejected(self, store):
        rid = await _create_test_result(store)
        with pytest.raises(InvalidHunterTransition):
            await update_result_status(store, rid, "promoted")

    @pytest.mark.asyncio
    async def test_optimistic_concurrency_check(self, store):
        rid = await _create_test_result(store)
        with pytest.raises(StaleUpdateError):
            await update_result_status(
                store, rid, "relevant",
                expected_updated_at="2000-01-01T00:00:00Z",
            )

    @pytest.mark.asyncio
    async def test_audit_event_created(self, store):
        rid = await _create_test_result(store)
        await update_result_status(store, rid, "relevant")
        cursor = await store._db.execute(
            "SELECT action_type FROM audit_events WHERE entity_type = 'hunter_result'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "hunter_feedback"


class TestNegativeKeywordExtraction:
    @pytest.mark.asyncio
    async def test_extraction_from_single_rejection(self, store):
        """Single rejection should not create keywords (min_occurrences=2)."""
        rid = await _create_test_result(
            store, company_name="BlockchainSaaS Platform",
            raw_data={"description": "enterprise blockchain saas analytics"},
        )
        await update_result_status(store, rid, "not_relevant")
        keywords = await extract_negative_keywords_from_rejection(store, rid)
        assert keywords == []  # Not enough occurrences

    @pytest.mark.asyncio
    async def test_extraction_from_multiple_rejections(self, store):
        """Words appearing in 2+ rejections should be extracted."""
        # Create 2 rejected results with shared words
        rid1 = await _create_test_result(
            store, company_name="BlockchainTech",
            raw_data={"description": "blockchain enterprise analytics platform"},
        )
        rid2 = await _create_test_result(
            store, company_name="BlockchainIO",
            raw_data={"description": "blockchain decentralized enterprise solution"},
        )
        await update_result_status(store, rid1, "not_relevant")
        await update_result_status(store, rid2, "not_relevant")

        keywords = await extract_negative_keywords_from_rejection(store, rid2)
        assert "blockchain" in keywords
        assert "enterprise" in keywords

    @pytest.mark.asyncio
    async def test_protected_vocabulary_not_extracted(self, store):
        """Protected vocabulary should never become negative keywords."""
        rid1 = await _create_test_result(
            store, company_name="Health Blockchain",
            raw_data={"description": "health blockchain platform"},
        )
        rid2 = await _create_test_result(
            store, company_name="Health Analytics",
            raw_data={"description": "health data platform"},
        )
        await update_result_status(store, rid1, "not_relevant")
        await update_result_status(store, rid2, "not_relevant")

        keywords = await extract_negative_keywords_from_rejection(store, rid2)
        assert "health" not in keywords  # Protected

    @pytest.mark.asyncio
    async def test_review_required_flag(self, store):
        """Auto-extracted keywords should have review_required=True."""
        rid1 = await _create_test_result(
            store, company_name="EnterpriseSaaS",
            raw_data={"description": "enterprise saas solution"},
        )
        rid2 = await _create_test_result(
            store, company_name="EnterpriseTool",
            raw_data={"description": "enterprise devtool analytics"},
        )
        await update_result_status(store, rid1, "not_relevant")
        await update_result_status(store, rid2, "not_relevant")

        await extract_negative_keywords_from_rejection(store, rid2)
        all_kws = await get_active_negative_keywords(store)
        for kw in all_kws:
            assert kw["review_required"] is True


class TestCandidateExtraction:
    def test_basic_extraction(self):
        candidates = _extract_candidate_keywords(
            "BlockchainSaaS Inc", {"description": "enterprise analytics"}
        )
        assert "blockchainsaas" in candidates or "enterprise" in candidates
        assert "analytics" in candidates

    def test_protected_words_filtered(self):
        candidates = _extract_candidate_keywords(
            "Health Food Co", {"description": "healthy food brand"}
        )
        assert "health" not in candidates
        assert "food" not in candidates

    def test_empty_input(self):
        assert _extract_candidate_keywords("", {}) == []
