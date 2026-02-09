"""
Tests for cascade_merge (Task 5).

Covers:
- Signals reassigned from loser to winner
- company_files merged (timestamps, source_apis)
- Review collision resolved (precedence, evidence merge)
- Audit log entry created
- Idempotent on re-run (no loser rows left)
- source_apis sorted deterministically
- Works with optional tx parameter
- Edge cases: only loser has file, neither has file, no signals
"""

import json
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.merge_cascade import cascade_merge


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


async def _insert_signal(store, signal_id, company_id, canonical_key=None,
                          source_api="github"):
    """Helper: insert a signal row directly.

    Uses signal_id to generate unique detected_at to avoid UNIQUE constraint
    on (canonical_key, signal_type, source_api, detected_at).
    """
    if canonical_key is None:
        canonical_key = f"domain:test{signal_id}.com"
    detected_at = f"2026-01-{signal_id:02d}T00:00:00+00:00"
    now = "2026-01-15T00:00:00+00:00"
    await store._db.execute(
        """INSERT INTO signals
           (id, signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal_id, "test", source_api, canonical_key, "Test Co",
         0.8, "{}", detected_at, now, company_id),
    )
    await store._db.commit()


async def _insert_company_file(store, company_id, source_apis=None,
                                 status="thin", first_seen="2026-01-01T00:00:00+00:00",
                                 last_seen="2026-01-15T00:00:00+00:00"):
    """Helper: insert a company_files row directly."""
    if source_apis is None:
        source_apis = ["github"]
    await store._db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, "Test Co", f"domain:{company_id}.com", status,
         json.dumps(source_apis), first_seen, last_seen),
    )
    await store._db.commit()


async def _insert_review(store, company_id, status="pending",
                           evidence_ids=None):
    """Helper: insert a review_items row directly."""
    if evidence_ids is None:
        evidence_ids = [1]
    now = "2026-01-15T00:00:00+00:00"
    bundle = json.dumps({"signal_ids": evidence_ids, "schema_version": 1})
    cursor = await store._db.execute(
        """INSERT INTO review_items
           (company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (company_id, status, bundle, now, now),
    )
    await store._db.commit()
    return cursor.lastrowid


# =============================================================================
# SIGNAL REASSIGNMENT
# =============================================================================

class TestSignalReassignment:
    """Test that signals.company_id gets updated."""

    @pytest.mark.asyncio
    async def test_signals_reassigned_to_winner(self, store):
        """Loser's signals should be reassigned to winner."""
        await _insert_signal(store, 1, "winner_id")
        await _insert_signal(store, 2, "loser_id")
        await _insert_signal(store, 3, "loser_id")

        report = await cascade_merge(
            store, "winner_id", "loser_id", "test_merge", "test"
        )

        assert report["signals_reassigned"] == 2

        # Verify all signals now have winner's company_id
        cursor = await store._db.execute(
            "SELECT company_id FROM signals ORDER BY id"
        )
        rows = await cursor.fetchall()
        assert all(row[0] == "winner_id" for row in rows)

    @pytest.mark.asyncio
    async def test_no_signals_to_reassign(self, store):
        """Merge with no loser signals should report 0."""
        await _insert_signal(store, 1, "winner_id")

        report = await cascade_merge(
            store, "winner_id", "loser_id", "test", "test"
        )

        assert report["signals_reassigned"] == 0

    @pytest.mark.asyncio
    async def test_winner_signals_unchanged(self, store):
        """Winner's existing signals should not be modified."""
        await _insert_signal(store, 1, "winner_id", source_api="github")
        await _insert_signal(store, 2, "loser_id", source_api="sec_edgar")

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        cursor = await store._db.execute(
            "SELECT source_api FROM signals WHERE id = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == "github"  # Original data preserved


# =============================================================================
# COMPANY FILE MERGE
# =============================================================================

class TestCompanyFileMerge:
    """Test company_files merge logic."""

    @pytest.mark.asyncio
    async def test_both_files_merge_timestamps(self, store):
        """Merge should use min(first_seen) and max(last_seen)."""
        await _insert_company_file(
            store, "winner_id",
            first_seen="2026-01-10T00:00:00+00:00",
            last_seen="2026-01-15T00:00:00+00:00",
        )
        await _insert_company_file(
            store, "loser_id",
            first_seen="2026-01-05T00:00:00+00:00",
            last_seen="2026-01-20T00:00:00+00:00",
        )

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        cursor = await store._db.execute(
            "SELECT first_seen_at, last_seen_at FROM company_files WHERE company_id = 'winner_id'"
        )
        row = await cursor.fetchone()
        assert row[0] == "2026-01-05T00:00:00+00:00"  # earliest
        assert row[1] == "2026-01-20T00:00:00+00:00"  # latest

    @pytest.mark.asyncio
    async def test_both_files_merge_sources_sorted(self, store):
        """source_apis should be merged, deduplicated, and sorted."""
        await _insert_company_file(store, "winner_id", source_apis=["github", "sec_edgar"])
        await _insert_company_file(store, "loser_id", source_apis=["news_api", "github"])

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        cursor = await store._db.execute(
            "SELECT source_apis FROM company_files WHERE company_id = 'winner_id'"
        )
        row = await cursor.fetchone()
        sources = json.loads(row[0])
        assert sources == ["github", "news_api", "sec_edgar"]  # sorted, deduped

    @pytest.mark.asyncio
    async def test_loser_file_deleted(self, store):
        """Loser's company_file should be deleted after merge."""
        await _insert_company_file(store, "winner_id")
        await _insert_company_file(store, "loser_id")

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM company_files WHERE company_id = 'loser_id'"
        )
        assert (await cursor.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_only_loser_has_file(self, store):
        """If only loser has a file, it should be reassigned to winner."""
        await _insert_company_file(store, "loser_id", source_apis=["github"])

        report = await cascade_merge(
            store, "winner_id", "loser_id", "test", "test"
        )

        assert report["company_file_merged"] is True

        cursor = await store._db.execute(
            "SELECT company_id FROM company_files"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "winner_id"

    @pytest.mark.asyncio
    async def test_neither_has_file(self, store):
        """If neither has a company file, company_file_merged should be False."""
        report = await cascade_merge(
            store, "winner_id", "loser_id", "test", "test"
        )

        assert report["company_file_merged"] is False


# =============================================================================
# REVIEW COLLISION
# =============================================================================

class TestReviewCollision:
    """Test review_items UNIQUE constraint handling on merge."""

    @pytest.mark.asyncio
    async def test_both_pending_reviews(self, store):
        """When both have pending reviews, one should be rejected with evidence merged."""
        winner_review = await _insert_review(store, "winner_id", "pending", [1, 2])
        loser_review = await _insert_review(store, "loser_id", "pending", [3, 4])

        report = await cascade_merge(
            store, "winner_id", "loser_id", "test", "test"
        )

        assert report["reviews_merged"] is True

        # Non-primary should be rejected
        cursor = await store._db.execute(
            "SELECT status, reason FROM review_items WHERE id = ?",
            (loser_review,)
        )
        row = await cursor.fetchone()
        assert row[0] == "rejected"
        assert "merged_into:" in row[1]

        # Primary should have merged evidence
        cursor = await store._db.execute(
            "SELECT evidence_bundle FROM review_items WHERE id = ?",
            (winner_review,)
        )
        bundle = json.loads((await cursor.fetchone())[0])
        assert sorted(bundle["signal_ids"]) == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_precedence_publish_queued_wins(self, store):
        """publish_queued should take precedence over pending."""
        # Insert winner with pending
        pending_id = await _insert_review(store, "winner_id", "pending", [1])
        # Insert loser with approved -> publish_queued
        pq_id = await _insert_review(store, "loser_id", "publish_queued", [2])

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        # The publish_queued review should be primary (not rejected)
        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (pq_id,)
        )
        row = await cursor.fetchone()
        # It gets reassigned to winner but keeps its status
        # The pending one should be rejected
        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (pending_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "rejected"

    @pytest.mark.asyncio
    async def test_no_active_reviews(self, store):
        """Merge with no active reviews should work fine."""
        report = await cascade_merge(
            store, "winner_id", "loser_id", "test", "test"
        )

        assert report["reviews_merged"] is False

    @pytest.mark.asyncio
    async def test_only_loser_has_review(self, store):
        """Loser's review should be reassigned to winner without collision."""
        loser_review = await _insert_review(store, "loser_id", "pending", [1])

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        cursor = await store._db.execute(
            "SELECT company_id, status FROM review_items WHERE id = ?",
            (loser_review,)
        )
        row = await cursor.fetchone()
        assert row[0] == "winner_id"
        assert row[1] == "pending"  # Status preserved


# =============================================================================
# AUDIT LOG
# =============================================================================

class TestAuditLog:
    """Test audit trail for merge operations."""

    @pytest.mark.asyncio
    async def test_audit_entry_created(self, store):
        """cascade_merge should create an audit_log entry."""
        await _insert_signal(store, 1, "loser_id")

        await cascade_merge(
            store, "winner_id", "loser_id", "identity_merge", "pipeline"
        )

        cursor = await store._db.execute(
            """SELECT action_type, entity_type, entity_id, actor, details
               FROM audit_log
               WHERE action_type = 'cascade_merge'"""
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "cascade_merge"
        assert row[1] == "company"
        assert row[2] == "winner_id"
        assert row[3] == "pipeline"

        details = json.loads(row[4])
        assert details["winner"] == "winner_id"
        assert details["loser"] == "loser_id"
        assert details["signals_reassigned"] == 1

    @pytest.mark.asyncio
    async def test_audit_includes_merge_stats(self, store):
        """Audit details should include all merge statistics."""
        await _insert_company_file(store, "winner_id")
        await _insert_company_file(store, "loser_id")
        await _insert_signal(store, 1, "loser_id")

        await cascade_merge(
            store, "winner_id", "loser_id", "test", "operator"
        )

        cursor = await store._db.execute(
            """SELECT details FROM audit_log
               WHERE action_type = 'cascade_merge'"""
        )
        details = json.loads((await cursor.fetchone())[0])

        assert details["signals_reassigned"] == 1
        assert details["company_file_merged"] is True
        assert details["reason"] == "test"


# =============================================================================
# IDEMPOTENCY
# =============================================================================

class TestIdempotency:
    """Test that re-running merge is safe."""

    @pytest.mark.asyncio
    async def test_rerun_merge_is_safe(self, store):
        """Running cascade_merge twice should be idempotent."""
        await _insert_signal(store, 1, "loser_id")
        await _insert_company_file(store, "winner_id")
        await _insert_company_file(store, "loser_id")

        await cascade_merge(store, "winner_id", "loser_id", "test", "test")
        report2 = await cascade_merge(store, "winner_id", "loser_id", "test", "test")

        # Second run should find nothing to reassign
        assert report2["signals_reassigned"] == 0
        assert report2["company_file_merged"] is False


# =============================================================================
# TRANSACTION PARAMETER
# =============================================================================

class TestTransactionParameter:
    """Test optional tx parameter."""

    @pytest.mark.asyncio
    async def test_with_external_transaction(self, store):
        """cascade_merge should work within an external transaction."""
        await _insert_signal(store, 1, "loser_id")

        async with store.transaction_immediate() as tx:
            report = await cascade_merge(
                store, "winner_id", "loser_id", "test", "test", tx=tx
            )

        assert report["signals_reassigned"] == 1

        # Verify committed
        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id = 1"
        )
        assert (await cursor.fetchone())[0] == "winner_id"
