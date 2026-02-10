"""Tests for intelligence/merge_suggestions.py.

Covers:
- compute_pair_key: determinism, order independence, length
- store_merge_suggestion: upsert lifecycle (new, pending update, approved skip,
  superseded skip, rejected cooldown, rejected reopen)
- get_merge_suggestion / list_merge_suggestions: retrieval and cursor pagination
- _score_pair: fuzzy + Jaro-Winkler + shared domain boost
- compute_blast_radius: normal counts, hard cap at 10000
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from intelligence.merge_suggestions import (
    MergeSuggestion,
    REJECTED_COOLDOWN_DAYS,
    REJECTED_REOPEN_DELTA,
    SCORING_VERSION,
    _score_pair,
    compute_blast_radius,
    compute_pair_key,
    get_merge_suggestion,
    list_merge_suggestions,
    store_merge_suggestion,
)
from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================


@pytest_asyncio.fixture
async def store():
    """SignalStore backed by a temporary on-disk SQLite database."""
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


def _make_suggestion(
    *,
    pair_key: str = "",
    entity_a_company_id: str = "comp-a",
    entity_b_company_id: str = "comp-b",
    entity_a_canonical_key: str = "domain:alpha.com",
    entity_b_canonical_key: str = "domain:beta.com",
    entity_a_company_name: str = "Alpha Inc",
    entity_b_company_name: str = "Beta Inc",
    match_type: str = "fuzzy_name",
    similarity_score: float = 0.75,
    scoring_version: str = SCORING_VERSION,
    evidence_json: str = '{"source": "test"}',
    shadow_run_id: int | None = None,
    status: str = "pending",
) -> MergeSuggestion:
    """Helper to build a MergeSuggestion with sensible defaults."""
    if not pair_key:
        pair_key = compute_pair_key(entity_a_company_id, entity_b_company_id)
    return MergeSuggestion(
        shadow_run_id=shadow_run_id,
        pair_key=pair_key,
        entity_a_company_id=entity_a_company_id,
        entity_b_company_id=entity_b_company_id,
        entity_a_canonical_key=entity_a_canonical_key,
        entity_b_canonical_key=entity_b_canonical_key,
        entity_a_company_name=entity_a_company_name,
        entity_b_company_name=entity_b_company_name,
        match_type=match_type,
        similarity_score=similarity_score,
        scoring_version=scoring_version,
        evidence_json=evidence_json,
        status=status,
    )


# =============================================================================
# compute_pair_key
# =============================================================================


class TestComputePairKey:
    """Tests for compute_pair_key()."""

    def test_pair_key_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = compute_pair_key("aaa", "bbb")
        key2 = compute_pair_key("aaa", "bbb")
        assert key1 == key2

    def test_pair_key_order_independent(self):
        """compute_pair_key(x, y) == compute_pair_key(y, x)."""
        key_ab = compute_pair_key("company-x", "company-y")
        key_ba = compute_pair_key("company-y", "company-x")
        assert key_ab == key_ba

    def test_pair_key_length_64(self):
        """Returns exactly 64 hex characters (SHA-256 digest)."""
        key = compute_pair_key("one", "two")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_pair_key_matches_manual_sha256(self):
        """Verify the hash matches the documented formula."""
        a, b = "zzz", "aaa"
        sorted_ids = (min(a, b), max(a, b))
        payload = sorted_ids[0] + "\x1f" + sorted_ids[1]
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        assert compute_pair_key(a, b) == expected


# =============================================================================
# store_merge_suggestion
# =============================================================================


class TestStoreMergeSuggestion:
    """Tests for store_merge_suggestion() upsert lifecycle."""

    @pytest.mark.asyncio
    async def test_store_new_suggestion(self, store):
        """New pair is inserted with status=pending and returns an integer id."""
        suggestion = _make_suggestion()
        result_id = await store_merge_suggestion(store, suggestion)

        assert result_id is not None
        assert isinstance(result_id, int)

        # Verify row in DB
        fetched = await get_merge_suggestion(store, result_id)
        assert fetched is not None
        assert fetched.status == "pending"
        assert fetched.similarity_score == 0.75
        assert fetched.entity_a_company_id == "comp-a"
        assert fetched.entity_b_company_id == "comp-b"

    @pytest.mark.asyncio
    async def test_store_existing_pending_higher_score_updates(self, store):
        """Existing pending suggestion is updated when score improves."""
        suggestion = _make_suggestion(similarity_score=0.60)
        first_id = await store_merge_suggestion(store, suggestion)

        improved = _make_suggestion(
            similarity_score=0.85,
            evidence_json='{"source": "improved"}',
        )
        second_id = await store_merge_suggestion(store, improved)

        assert second_id == first_id  # same row
        fetched = await get_merge_suggestion(store, first_id)
        assert fetched.similarity_score == 0.85
        assert '"improved"' in fetched.evidence_json

    @pytest.mark.asyncio
    async def test_store_existing_pending_lower_score_no_update(self, store):
        """Existing pending suggestion is NOT updated when score is lower."""
        suggestion = _make_suggestion(similarity_score=0.80)
        first_id = await store_merge_suggestion(store, suggestion)

        worse = _make_suggestion(
            similarity_score=0.50,
            evidence_json='{"source": "worse"}',
        )
        second_id = await store_merge_suggestion(store, worse)

        assert second_id == first_id  # still returns existing id
        fetched = await get_merge_suggestion(store, first_id)
        assert fetched.similarity_score == 0.80  # unchanged

    @pytest.mark.asyncio
    async def test_store_existing_approved_skipped(self, store):
        """Approved suggestion is skipped (returns None)."""
        suggestion = _make_suggestion()
        first_id = await store_merge_suggestion(store, suggestion)

        # Manually set status to approved
        await store._db.execute(
            "UPDATE merge_suggestions SET status = 'approved' WHERE id = ?",
            (first_id,),
        )
        await store._db.commit()

        result = await store_merge_suggestion(store, _make_suggestion(similarity_score=0.99))
        assert result is None

    @pytest.mark.asyncio
    async def test_store_existing_superseded_skipped(self, store):
        """Superseded suggestion is skipped (returns None)."""
        suggestion = _make_suggestion()
        first_id = await store_merge_suggestion(store, suggestion)

        await store._db.execute(
            "UPDATE merge_suggestions SET status = 'superseded' WHERE id = ?",
            (first_id,),
        )
        await store._db.commit()

        result = await store_merge_suggestion(store, _make_suggestion(similarity_score=0.99))
        assert result is None

    @pytest.mark.asyncio
    async def test_store_existing_rejected_cooldown_not_elapsed(self, store):
        """Rejected suggestion within 7-day cooldown is skipped (returns None)."""
        suggestion = _make_suggestion(similarity_score=0.50)
        first_id = await store_merge_suggestion(store, suggestion)

        # Mark as rejected 2 days ago (inside cooldown window)
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        await store._db.execute(
            "UPDATE merge_suggestions SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (two_days_ago, first_id),
        )
        await store._db.commit()

        # Even with a big score improvement, cooldown blocks reopen
        result = await store_merge_suggestion(
            store, _make_suggestion(similarity_score=0.95)
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_store_existing_rejected_score_delta_too_small(self, store):
        """Rejected suggestion outside cooldown but with insufficient score delta is skipped."""
        suggestion = _make_suggestion(similarity_score=0.70)
        first_id = await store_merge_suggestion(store, suggestion)

        # Mark as rejected 10 days ago (outside cooldown)
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        await store._db.execute(
            "UPDATE merge_suggestions SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (ten_days_ago, first_id),
        )
        await store._db.commit()

        # Score delta = 0.75 - 0.70 = 0.05 (< REJECTED_REOPEN_DELTA=0.1)
        result = await store_merge_suggestion(
            store, _make_suggestion(similarity_score=0.75)
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_store_existing_rejected_reopen(self, store):
        """Rejected suggestion is reopened when cooldown elapsed AND score delta >= 0.1."""
        suggestion = _make_suggestion(similarity_score=0.50)
        first_id = await store_merge_suggestion(store, suggestion)

        # Mark as rejected 8 days ago (outside the 7-day cooldown)
        eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        await store._db.execute(
            "UPDATE merge_suggestions SET status = 'rejected', reviewed_at = ?, reviewed_by = 'operator-1' WHERE id = ?",
            (eight_days_ago, first_id),
        )
        await store._db.commit()

        # Score delta = 0.70 - 0.50 = 0.20 (>= 0.1)
        reopened = _make_suggestion(
            similarity_score=0.70,
            evidence_json='{"source": "reopened"}',
        )
        result_id = await store_merge_suggestion(store, reopened)

        assert result_id == first_id
        fetched = await get_merge_suggestion(store, first_id)
        assert fetched.status == "pending"
        assert fetched.similarity_score == 0.70
        assert fetched.reviewed_by is None
        assert fetched.reviewed_at is None


# =============================================================================
# get_merge_suggestion
# =============================================================================


class TestGetMergeSuggestion:
    """Tests for get_merge_suggestion()."""

    @pytest.mark.asyncio
    async def test_get_existing_suggestion(self, store):
        """Fetch a suggestion by its id."""
        suggestion = _make_suggestion()
        new_id = await store_merge_suggestion(store, suggestion)

        fetched = await get_merge_suggestion(store, new_id)
        assert fetched is not None
        assert fetched.id == new_id
        assert fetched.entity_a_company_name == "Alpha Inc"
        assert fetched.entity_b_company_name == "Beta Inc"
        assert fetched.match_type == "fuzzy_name"
        assert fetched.scoring_version == SCORING_VERSION

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, store):
        """Non-existent id returns None."""
        result = await get_merge_suggestion(store, 99999)
        assert result is None


# =============================================================================
# list_merge_suggestions
# =============================================================================


class TestListMergeSuggestions:
    """Tests for list_merge_suggestions()."""

    @pytest.mark.asyncio
    async def test_list_empty(self, store):
        """Empty table returns an empty list."""
        results = await list_merge_suggestions(store)
        assert results == []

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self, store):
        """Filter by status returns only matching rows."""
        # Insert three suggestions with different statuses
        for i, status_val in enumerate(["pending", "approved", "pending"]):
            s = _make_suggestion(
                entity_a_company_id=f"comp-a{i}",
                entity_b_company_id=f"comp-b{i}",
                similarity_score=0.75,
            )
            new_id = await store_merge_suggestion(store, s)
            if status_val != "pending":
                await store._db.execute(
                    "UPDATE merge_suggestions SET status = ? WHERE id = ?",
                    (status_val, new_id),
                )
                await store._db.commit()

        pending = await list_merge_suggestions(store, status="pending")
        assert len(pending) == 2
        for item in pending:
            assert item.status == "pending"

        approved = await list_merge_suggestions(store, status="approved")
        assert len(approved) == 1

    @pytest.mark.asyncio
    async def test_list_pagination(self, store):
        """Cursor pagination (created_at, id) returns the next page."""
        # Insert 5 suggestions
        ids = []
        for i in range(5):
            s = _make_suggestion(
                entity_a_company_id=f"comp-a{i}",
                entity_b_company_id=f"comp-b{i}",
            )
            new_id = await store_merge_suggestion(store, s)
            ids.append(new_id)

        # First page: limit 3
        page1 = await list_merge_suggestions(store, limit=3)
        assert len(page1) == 3

        # Use last item as cursor
        last = page1[-1]
        page2 = await list_merge_suggestions(
            store,
            cursor_created_at=last.created_at,
            cursor_id=last.id,
            limit=3,
        )
        assert len(page2) == 2  # 5 total - 3 = 2 remaining

        # No overlap between pages
        page1_ids = {s.id for s in page1}
        page2_ids = {s.id for s in page2}
        assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.asyncio
    async def test_list_limit_capped_at_200(self, store):
        """Requested limit > 200 is silently capped to 200."""
        # Insert 3 suggestions
        for i in range(3):
            s = _make_suggestion(
                entity_a_company_id=f"comp-a{i}",
                entity_b_company_id=f"comp-b{i}",
            )
            await store_merge_suggestion(store, s)

        # Request limit=500, should be capped to 200 (but only 3 rows exist)
        results = await list_merge_suggestions(store, limit=500)
        assert len(results) == 3  # all 3 returned, cap did not truncate

    @pytest.mark.asyncio
    async def test_list_ordering_desc(self, store):
        """Results are ordered by created_at DESC, id DESC."""
        ids = []
        for i in range(3):
            s = _make_suggestion(
                entity_a_company_id=f"comp-a{i}",
                entity_b_company_id=f"comp-b{i}",
            )
            new_id = await store_merge_suggestion(store, s)
            ids.append(new_id)

        results = await list_merge_suggestions(store)
        result_ids = [r.id for r in results]
        # Most recently inserted should come first (DESC order)
        assert result_ids == sorted(result_ids, reverse=True)


# =============================================================================
# _score_pair
# =============================================================================


class TestScorePair:
    """Tests for _score_pair() scoring function."""

    def test_score_pair_identical_names_no_domain(self):
        """Identical names without shared domain: high fuzzy + jaro, no domain boost."""
        score, match_type = _score_pair("Acme Corp", "Acme Corp", shared_domain=False)
        # token_sort_ratio = 1.0, jaro_winkler = 1.0, domain = 0.0
        # expected = 0.4*1.0 + 0.3*1.0 + 0.3*0.0 = 0.7
        assert match_type == "fuzzy_name"
        assert score == pytest.approx(0.7, abs=0.05)

    def test_score_pair_shared_domain_boost(self):
        """Shared domain adds 0.3 to the score."""
        score_without, _ = _score_pair("Acme Corp", "Acme Corp", shared_domain=False)
        score_with, match_type = _score_pair("Acme Corp", "Acme Corp", shared_domain=True)
        assert match_type == "shared_domain"
        # Domain boost = 0.3 * 1.0 = 0.3
        assert score_with == pytest.approx(score_without + 0.3, abs=0.01)

    def test_score_pair_completely_different_names(self):
        """Very different names with no domain produce a low score."""
        score, match_type = _score_pair("Acme Foods", "Zenith Robotics", shared_domain=False)
        assert match_type == "fuzzy_name"
        assert score < 0.5

    def test_score_pair_returns_rounded(self):
        """Score is rounded to 4 decimal places."""
        score, _ = _score_pair("Foo Bar", "Foo Baz", shared_domain=False)
        decimal_str = str(score)
        if "." in decimal_str:
            decimal_places = len(decimal_str.split(".")[1])
            assert decimal_places <= 4


# =============================================================================
# compute_blast_radius
# =============================================================================


class TestComputeBlastRadius:
    """Tests for compute_blast_radius()."""

    @pytest.mark.asyncio
    async def test_blast_radius_normal(self, store):
        """Normal case: counts signals for each entity."""
        now = datetime.now(timezone.utc).isoformat()
        db = store._db

        # Disable FK so we can insert test signals without full schema dependencies
        await db.execute("PRAGMA foreign_keys = OFF")

        # Insert signals for comp-a (3 signals)
        for i in range(3):
            await db.execute(
                """
                INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                    company_id, confidence, raw_data, detected_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"type_{i}", f"src_{i}", f"domain:alpha{i}.com", "Alpha Inc",
                    "comp-a", 0.7, "{}", now, now,
                ),
            )

        # Insert signals for comp-b (2 signals)
        for i in range(2):
            await db.execute(
                """
                INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                    company_id, confidence, raw_data, detected_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"type_b{i}", f"src_b{i}", f"domain:beta{i}.com", "Beta Inc",
                    "comp-b", 0.6, "{}", now, now,
                ),
            )
        await db.commit()

        result = await compute_blast_radius(store, "comp-a", "comp-b")

        assert result["signals_a"] == 3
        assert result["signals_b"] == 2
        assert result["total_affected"] >= 5  # signals + possibly reviews + files
        assert "capped" not in result
        assert "timeout" not in result

    @pytest.mark.asyncio
    async def test_blast_radius_cap_at_10000(self, store):
        """Total > 10000 returns capped=True with the total count."""
        db = store._db
        await db.execute("PRAGMA foreign_keys = OFF")

        now = datetime.now(timezone.utc).isoformat()

        # We mock the actual counts rather than inserting 10001 rows.
        # Patch the DB execute to return large counts.
        original_execute = db.execute
        call_count = 0

        class FakeCursor:
            def __init__(self, count):
                self._count = count

            async def fetchone(self):
                return (self._count,)

        async def mock_execute(sql, params=None):
            nonlocal call_count
            if "SELECT COUNT(*) FROM signals" in sql:
                call_count += 1
                # Return 6000 for each entity's signals
                return FakeCursor(6000)
            if "SELECT COUNT(*) FROM review_items" in sql:
                return FakeCursor(0)
            if "SELECT COUNT(*) FROM company_files" in sql:
                return FakeCursor(0)
            if params:
                return await original_execute(sql, params)
            return await original_execute(sql)

        store._db.execute = mock_execute

        result = await compute_blast_radius(store, "comp-a", "comp-b")

        assert result.get("capped") is True
        assert result["total_affected"] == 12000

        # Restore original execute
        store._db.execute = original_execute

    @pytest.mark.asyncio
    async def test_blast_radius_zero_signals(self, store):
        """Entities with no signals return zero counts."""
        result = await compute_blast_radius(store, "nonexistent-a", "nonexistent-b")
        assert result["signals_a"] == 0
        assert result["signals_b"] == 0
        assert result["total_affected"] == 0


# =============================================================================
# MergeSuggestion dataclass
# =============================================================================


class TestMergeSuggestionDataclass:
    """Tests for the MergeSuggestion dataclass defaults."""

    def test_defaults(self):
        """Verify default field values."""
        s = MergeSuggestion()
        assert s.id is None
        assert s.shadow_run_id is None
        assert s.pair_key == ""
        assert s.status == "pending"
        assert s.similarity_score == 0.0
        assert s.scoring_version == SCORING_VERSION
        assert s.evidence_json == "{}"
        assert s.reviewed_by is None
        assert s.reviewed_at is None
        assert s.blast_radius_json is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
