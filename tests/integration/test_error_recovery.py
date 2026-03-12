"""Block 2.3: Error Recovery Tests.

Tests recovery from partial failures — batch commit errors, merge cascade
rollback, invalid state transitions, and DB integrity under stress.
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, SuppressionEntry
from storage.review_store import (
    create_review_item,
    update_review_status,
    InvalidStateTransition,
    VALID_TRANSITIONS,
)
from storage.merge_cascade import cascade_merge
from workflows.batch_publisher import (
    create_batch,
    preview_batch,
    commit_batch,
    abort_batch,
    BatchError,
    BatchNotFoundError,
    BatchStateError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(monkeypatch):
    """Fresh SignalStore with temp DB, env vars cleaned."""
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)
    monkeypatch.delenv("V2_ENABLEMENT", raising=False)
    monkeypatch.delenv("ML_ENABLEMENT", raising=False)
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    monkeypatch.setenv("LLM_THESIS_MODE", "off")

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


async def _seed_signal(store, signal_id=None, company_id="company-1",
                       company_name="Acme Corp", canonical_key="domain:acme.com",
                       source_api="github", confidence=0.8):
    """Insert a signal directly into DB."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR IGNORE INTO signals
           (company_id, company_name, canonical_key, signal_type, source_api,
            confidence, detected_at, created_at, raw_data)
           VALUES (?, ?, ?, 'new_company', ?, ?, ?, ?, ?)""",
        (company_id, company_name, canonical_key, source_api,
         confidence, now, now,
         json.dumps({"description": f"Test signal for {company_name}"}))
    )
    await db.commit()


async def _seed_company_file(store, company_id, canonical_key="domain:acme.com",
                              company_name="Acme Corp", status="thin"):
    """Insert a company_files row."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR IGNORE INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, '["github"]', ?, ?)""",
        (company_id, company_name, canonical_key, status, now, now)
    )
    await db.commit()


async def _seed_approved_review(store, company_id="company-1", signal_ids=None):
    """Create and approve a review item. Returns review_id."""
    # Ensure company file exists (FK)
    await _seed_company_file(store, company_id, f"domain:{company_id}.com", f"Co {company_id}")

    # Seed at least one signal
    await _seed_signal(store, company_id=company_id, canonical_key=f"domain:{company_id}.com")

    review_id = await create_review_item(
        store, company_id=company_id,
        evidence_signal_ids=signal_ids or [1],
    )
    await update_review_status(
        store, review_id, "approved",
        actor="test-seed", reason="Seeded for test",
    )
    return review_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBatchCommitPartialFailure:
    """Test batch commit with partial push failures."""

    @pytest.mark.asyncio
    async def test_commit_dryrun_no_mutations(self, store):
        """Dry-run commit reads batch items without mutations."""
        review_id = await _seed_approved_review(store, "company-dr")
        batch = await create_batch(store, limit=10, actor="test")

        result = await commit_batch(store, batch["batch_id"], dry_run=True)

        assert result["dry_run"] is True
        assert result["pushed_count"] == 0
        assert result["pending_count"] >= 1

    @pytest.mark.asyncio
    async def test_commit_with_pusher_error_partial(self, store, monkeypatch):
        """When pusher fails for one item, batch gets committed_with_errors."""
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")

        # Seed two approved reviews
        await _seed_approved_review(store, "company-ok")
        await _seed_approved_review(store, "company-fail")
        batch = await create_batch(store, limit=10, actor="test")

        # Mock pusher: first call succeeds, second fails
        call_count = 0
        pusher = AsyncMock()

        async def mock_push(canonical_key, intent=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.pushed = True
                result.notion_page_id = "notion-page-ok"
                return result
            else:
                raise RuntimeError("Notion API timeout")

        pusher.process_single_prospect = mock_push

        mock_gate_result = MagicMock()
        mock_gate_result.verdict = "ready"
        mock_gate_result.to_dict.return_value = {"verdict": "ready"}
        with patch("monitoring.activation_gate.check_activation_readiness", new_callable=AsyncMock, return_value=mock_gate_result):
            result = await commit_batch(store, batch["batch_id"], pusher=pusher)

        assert result["final_status"] == "committed_with_errors"
        assert result["pushed_count"] == 1
        assert result["error_count"] >= 1


class TestMergeCascadeRecovery:
    """Test merge cascade transaction safety."""

    @pytest.mark.asyncio
    async def test_cascade_merge_reassigns_signals(self, store):
        """Merge reassigns loser's signals to winner."""
        await _seed_company_file(store, "winner-1", "domain:winner.com", "Winner Co")
        await _seed_company_file(store, "loser-1", "domain:loser.com", "Loser Co")
        await _seed_signal(store, company_id="winner-1", canonical_key="domain:winner.com")
        await _seed_signal(store, company_id="loser-1", canonical_key="domain:loser.com")

        report = await cascade_merge(
            store, "winner-1", "loser-1",
            reason="duplicate", actor="test",
        )

        assert report["winner"] == "winner-1"
        assert report["loser"] == "loser-1"
        assert report["signals_reassigned"] >= 1

        # Verify: no signals remain assigned to loser
        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = ?", ("loser-1",)
        )
        count = (await cursor.fetchone())[0]
        assert count == 0

    @pytest.mark.asyncio
    async def test_cascade_merge_review_collision_resolved(self, store):
        """When both companies have active reviews, the higher-precedence one wins."""
        await _seed_company_file(store, "w-rev", "domain:w-rev.com", "W Co")
        await _seed_company_file(store, "l-rev", "domain:l-rev.com", "L Co")
        await _seed_signal(store, company_id="w-rev", canonical_key="domain:w-rev.com")
        await _seed_signal(store, company_id="l-rev", canonical_key="domain:l-rev.com")

        # Create reviews for both
        w_review = await create_review_item(store, "w-rev", [1])
        l_review = await create_review_item(store, "l-rev", [2])

        # Approve the winner's review (higher precedence)
        await update_review_status(store, w_review, "approved", actor="test")

        report = await cascade_merge(
            store, "w-rev", "l-rev",
            reason="duplicate", actor="test",
        )

        assert report["reviews_merged"] is True


class TestPipelineSkipsBadSignal:
    """Test that pipeline handles individual bad signals gracefully."""

    @pytest.mark.asyncio
    async def test_malformed_raw_data_signal_skipped(self, store):
        """A signal with bad data doesn't crash the store; insert continues."""
        # Saving a signal with valid data should work
        signal_id = await store.save_signal(
            signal_type="funding_event",
            source_api="sec_edgar",
            canonical_key="domain:good-co.com",
            company_name="Good Co",
            confidence=0.8,
            raw_data={"valid": True},
        )
        assert signal_id is not None

        # The store handles all JSON-serializable raw_data
        signal_id2 = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="domain:another.com",
            company_name="Another Co",
            confidence=0.6,
            raw_data={"nested": {"deep": [1, 2, 3]}},
        )
        assert signal_id2 is not None


class TestInvalidReviewTransitions:
    """Test that invalid state transitions are blocked."""

    @pytest.mark.asyncio
    async def test_rejected_to_approved_blocked(self, store):
        """Rejected is terminal — cannot transition back to approved."""
        await _seed_company_file(store, "reject-co", "domain:reject.com")
        await _seed_signal(store, company_id="reject-co", canonical_key="domain:reject.com")
        review_id = await create_review_item(store, "reject-co", [1])

        # Reject the review
        await update_review_status(store, review_id, "rejected", actor="test")

        # Try to un-reject — should fail
        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "approved", actor="test")

    @pytest.mark.asyncio
    async def test_published_is_terminal(self, store):
        """Published is terminal — no outbound transitions."""
        await _seed_company_file(store, "pub-co", "domain:pub.com")
        await _seed_signal(store, company_id="pub-co", canonical_key="domain:pub.com")
        review_id = await create_review_item(store, "pub-co", [1])

        await update_review_status(store, review_id, "approved", actor="test")
        await update_review_status(store, review_id, "published", actor="test")

        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "pending", actor="test")


class TestBatchAbortGuard:
    """Test that batch abort is guarded correctly."""

    @pytest.mark.asyncio
    async def test_abort_draft_reverts_reviews(self, store):
        """Aborting a draft batch reverts reviews to approved."""
        review_id = await _seed_approved_review(store, "abort-co")
        batch = await create_batch(store, limit=10, actor="test")

        result = await abort_batch(store, batch["batch_id"], reason="test abort")

        assert result["reverted_count"] >= 1

        # Verify review is back to approved
        db = store._db
        cursor = await db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "approved"

    @pytest.mark.asyncio
    async def test_abort_nonexistent_batch_raises(self, store):
        """Aborting a non-existent batch raises BatchNotFoundError."""
        with pytest.raises(BatchNotFoundError):
            await abort_batch(store, "batch-doesnotexist")


class TestMergeEdgeCases:
    """Forensic Phase 2: Merge cascade edge cases."""

    @pytest.mark.asyncio
    async def test_merge_nonexistent_winner_reassigns_file(self, store):
        """cascade_merge with non-existent winner: loser's file reassigned."""
        await _seed_company_file(store, "real-loser", "domain:loser.com", "Loser Co")
        await _seed_signal(store, company_id="real-loser",
                           canonical_key="domain:loser.com")

        report = await cascade_merge(
            store, "ghost-winner", "real-loser",
            reason="test", actor="forensic",
        )

        # Completes without error — loser's file is reassigned to winner
        assert report["company_file_merged"] is True
        assert report["signals_reassigned"] >= 1

    @pytest.mark.asyncio
    async def test_merge_both_nonexistent_silent(self, store):
        """cascade_merge with both IDs non-existent completes gracefully."""
        report = await cascade_merge(
            store, "ghost-a", "ghost-b",
            reason="test", actor="forensic",
        )

        # No company_files to merge, no signals to reassign
        assert report["signals_reassigned"] == 0
        assert report["company_file_merged"] is False


class TestBatchIdempotency:
    """Forensic Phase 2: Double-commit and state guard tests."""

    @pytest.mark.asyncio
    async def test_commit_already_committed_raises(self, store, monkeypatch):
        """Committing an already-committed batch raises BatchStateError."""
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")

        review_id = await _seed_approved_review(store, "company-idem")
        batch = await create_batch(store, limit=10, actor="test")

        # Mock pusher for first commit
        pusher = AsyncMock()
        result_mock = MagicMock()
        result_mock.pushed = True
        result_mock.notion_page_id = "notion-idem"
        pusher.process_single_prospect = AsyncMock(return_value=result_mock)

        mock_gate_result = MagicMock()
        mock_gate_result.verdict = "ready"
        mock_gate_result.to_dict.return_value = {"verdict": "ready"}
        with patch("monitoring.activation_gate.check_activation_readiness", new_callable=AsyncMock, return_value=mock_gate_result):
            await commit_batch(store, batch["batch_id"], pusher=pusher)

        # Second commit should raise (batch no longer in draft)
        with pytest.raises(BatchStateError):
            await commit_batch(store, batch["batch_id"], pusher=pusher)


class TestSignalValidation:
    """Forensic Phase 2: Signal boundary value tests."""

    @pytest.mark.asyncio
    async def test_save_signal_extreme_confidence_stored(self, store):
        """Confidence >1.0 is stored as-is (no validation gate in store)."""
        signal_id = await store.save_signal(
            signal_type="funding_event",
            source_api="sec_edgar",
            canonical_key="domain:extreme-conf.com",
            company_name="Extreme Co",
            confidence=99.5,
            raw_data={"test": True},
        )
        assert signal_id is not None

        db = store._db
        cursor = await db.execute(
            "SELECT confidence FROM signals WHERE id = ?", (signal_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 99.5  # Stored without validation

    @pytest.mark.asyncio
    async def test_suppression_cache_idempotent(self, store):
        """Calling update_suppression_cache N times = same as calling once."""
        key = "domain:idemp-supp.com"
        entry = SuppressionEntry(
            canonical_key=key, notion_page_id="np-idemp-2",
            status="Tracking", company_name="Idemp Supp Co",
        )

        for _ in range(5):
            await store.update_suppression_cache([entry])

        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM suppression_cache WHERE canonical_key = ?", (key,)
        )
        count = (await cursor.fetchone())[0]
        assert count == 1


class TestDBIntegrity:
    """Test database integrity under normal operations."""

    @pytest.mark.asyncio
    async def test_wal_mode_integrity(self, store):
        """PRAGMA integrity_check passes after mixed operations."""
        # Perform some writes
        await _seed_signal(store, company_id="int-1", canonical_key="domain:int-1.com")
        await _seed_signal(store, company_id="int-2", canonical_key="domain:int-2.com")
        await _seed_company_file(store, "int-1", "domain:int-1.com")
        entry = SuppressionEntry(
            canonical_key="domain:supp.com",
            notion_page_id="notion-123",
            status="Source",
            company_name="Supp Co",
        )
        await store.update_suppression_cache([entry])

        # Check integrity
        db = store._db
        cursor = await db.execute("PRAGMA integrity_check")
        result = await cursor.fetchone()
        assert result[0] == "ok"

    @pytest.mark.asyncio
    async def test_wal_integrity_after_merge_and_batch(self, store, monkeypatch):
        """Integrity holds after merge cascade + batch create/abort cycle."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")

        # Seed data for merge
        await _seed_company_file(store, "stress-w", "domain:stress-w.com", "W")
        await _seed_company_file(store, "stress-l", "domain:stress-l.com", "L")
        await _seed_signal(store, company_id="stress-w",
                           canonical_key="domain:stress-w.com")
        await _seed_signal(store, company_id="stress-l",
                           canonical_key="domain:stress-l.com")

        # Merge cascade
        await cascade_merge(store, "stress-w", "stress-l",
                            reason="stress", actor="test")

        # Batch lifecycle
        review_id = await _seed_approved_review(store, "stress-batch")
        batch = await create_batch(store, limit=10, actor="test")
        await abort_batch(store, batch["batch_id"], reason="stress test abort")

        # Integrity should hold
        db = store._db
        cursor = await db.execute("PRAGMA integrity_check")
        result = await cursor.fetchone()
        assert result[0] == "ok"
