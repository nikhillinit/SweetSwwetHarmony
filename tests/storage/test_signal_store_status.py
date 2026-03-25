"""
Tests for SignalStore status/processing operations.

Covers:
- mark_pushed: Mark signal as pushed to Notion
- mark_rejected: Mark signal as rejected
- mark_queued: Mark signal as queued
- update_signal_status: Generic status update
- get_signals_by_status: Query by status
- get_status_counts: Dashboard stats
- get_processing_stats: Processing statistics
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, StoredSignal


# =============================================================================
# MARK PUSHED TESTS
# =============================================================================

class TestMarkPushed:
    """Tests for mark_pushed method."""

    @pytest.mark.asyncio
    async def test_mark_pushed_updates_status(self, store_with_signals: SignalStore):
        """mark_pushed should update status to 'pushed'."""
        await store_with_signals.mark_pushed(1, "notion-page-abc")

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "pushed"

    @pytest.mark.asyncio
    async def test_mark_pushed_sets_notion_page_id(self, store_with_signals: SignalStore):
        """mark_pushed should store the Notion page ID."""
        notion_id = "notion-page-xyz-123"
        await store_with_signals.mark_pushed(1, notion_id)

        signal = await store_with_signals.get_signal(1)
        assert signal.notion_page_id == notion_id

    @pytest.mark.asyncio
    async def test_mark_pushed_sets_timestamp(self, store_with_signals: SignalStore):
        """mark_pushed should set processed_at timestamp."""
        before = datetime.now(timezone.utc)
        await store_with_signals.mark_pushed(1, "notion-page-123")

        signal = await store_with_signals.get_signal(1)
        assert signal.processed_at is not None
        assert signal.processed_at >= before

    @pytest.mark.asyncio
    async def test_mark_pushed_with_metadata(self, store_with_signals: SignalStore):
        """mark_pushed should store metadata."""
        metadata = {"batch_id": "batch-001", "confidence_boost": 0.1}
        await store_with_signals.mark_pushed(1, "notion-page-123", metadata=metadata)

        # Verify via direct DB query
        cursor = await store_with_signals._db.execute(
            "SELECT metadata FROM signal_processing WHERE signal_id = ?", (1,)
        )
        row = await cursor.fetchone()
        stored_metadata = json.loads(row[0])
        assert stored_metadata == metadata


# =============================================================================
# MARK REJECTED TESTS
# =============================================================================

class TestMarkRejected:
    """Tests for mark_rejected method."""

    @pytest.mark.asyncio
    async def test_mark_rejected_updates_status(self, store_with_signals: SignalStore):
        """mark_rejected should update status to 'rejected'."""
        await store_with_signals.mark_rejected(1, "Not a thesis fit")

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "rejected"

    @pytest.mark.asyncio
    async def test_mark_rejected_with_reason(self, store_with_signals: SignalStore):
        """mark_rejected should store rejection reason."""
        reason = "Company is B2B enterprise"
        await store_with_signals.mark_rejected(1, reason)

        signal = await store_with_signals.get_signal(1)
        assert signal.error_message == reason

    @pytest.mark.asyncio
    async def test_mark_rejected_sets_timestamp(self, store_with_signals: SignalStore):
        """mark_rejected should set processed_at timestamp."""
        before = datetime.now(timezone.utc)
        await store_with_signals.mark_rejected(1, "Rejected")

        signal = await store_with_signals.get_signal(1)
        assert signal.processed_at is not None
        assert signal.processed_at >= before


# =============================================================================
# MARK QUEUED TESTS
# =============================================================================

class TestMarkQueued:
    """Tests for mark_queued method."""

    @pytest.mark.asyncio
    async def test_mark_queued_for_review(self, store_with_signals: SignalStore):
        """mark_queued should update status to 'queued'."""
        await store_with_signals.mark_queued(1)

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "queued"

    @pytest.mark.asyncio
    async def test_mark_queued_clears_notion_page_id(self, store_with_signals: SignalStore):
        """mark_queued should clear notion_page_id."""
        # First mark as pushed
        await store_with_signals.mark_pushed(1, "notion-old-id")

        # Then mark as queued (re-processing)
        await store_with_signals.mark_queued(1)

        signal = await store_with_signals.get_signal(1)
        assert signal.notion_page_id is None

    @pytest.mark.asyncio
    async def test_mark_queued_with_metadata(self, store_with_signals: SignalStore):
        """mark_queued should store metadata."""
        metadata = {"review_reason": "Low confidence", "priority": "high"}
        await store_with_signals.mark_queued(1, metadata=metadata)

        cursor = await store_with_signals._db.execute(
            "SELECT metadata FROM signal_processing WHERE signal_id = ?", (1,)
        )
        row = await cursor.fetchone()
        stored_metadata = json.loads(row[0])
        assert stored_metadata == metadata


# =============================================================================
# UPDATE SIGNAL STATUS TESTS
# =============================================================================

class TestUpdateSignalStatus:
    """Tests for update_signal_status method."""

    @pytest.mark.asyncio
    async def test_update_signal_status_generic(self, store_with_signals: SignalStore):
        """Should update status for all signals with canonical key."""
        result = await store_with_signals.update_signal_status(
            canonical_key="ein:123456789",
            status="qualified",
        )

        assert result is True

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "qualified"

    @pytest.mark.asyncio
    async def test_update_signal_status_with_error_message(self, store_with_signals: SignalStore):
        """Should store error message."""
        await store_with_signals.update_signal_status(
            canonical_key="ein:123456789",
            status="held",
            error_message="Awaiting manual review",
        )

        signal = await store_with_signals.get_signal(1)
        assert signal.error_message == "Awaiting manual review"

    @pytest.mark.asyncio
    async def test_update_signal_status_not_found(self, store_with_signals: SignalStore):
        """Should return False for non-existent canonical key."""
        result = await store_with_signals.update_signal_status(
            canonical_key="domain:nonexistent.com",
            status="rejected",
        )

        assert result is False


# =============================================================================
# GET SIGNALS BY STATUS TESTS
# =============================================================================

class TestGetSignalsByStatus:
    """Tests for get_signals_by_status method."""

    @pytest.mark.asyncio
    async def test_get_signals_by_status_filtering(self, store_with_signals: SignalStore):
        """Should return only signals with matching status."""
        # Mark one as pushed
        await store_with_signals.mark_pushed(1, "notion-123")

        pending = await store_with_signals.get_signals_by_status("pending")
        pushed = await store_with_signals.get_signals_by_status("pushed")

        assert len(pending) == 1
        assert len(pushed) == 1
        assert pushed[0].id == 1

    @pytest.mark.asyncio
    async def test_get_signals_by_status_respects_limit(self, store: SignalStore):
        """Should respect limit parameter."""
        # Create 5 signals
        for i in range(5):
            await store.save_signal(
                signal_type="test",
                source_api="test_api",
                canonical_key=f"domain:test{i}.com",
                confidence=0.5,
                raw_data={},
            )

        signals = await store.get_signals_by_status("pending", limit=3)
        assert len(signals) == 3

    @pytest.mark.asyncio
    async def test_get_signals_by_status_empty(self, store_with_signals: SignalStore):
        """Should return empty list for status with no signals."""
        signals = await store_with_signals.get_signals_by_status("pushed")
        assert signals == []


# =============================================================================
# GET STATUS COUNTS TESTS
# =============================================================================

class TestGetStatusCounts:
    """Tests for get_status_counts method."""

    @pytest.mark.asyncio
    async def test_get_status_counts_accurate(self, store_with_signals: SignalStore):
        """Should return accurate counts per status."""
        await store_with_signals.mark_pushed(1, "notion-123")

        counts = await store_with_signals.get_status_counts()

        assert counts["pending"] == 1
        assert counts["pushed"] == 1

    @pytest.mark.asyncio
    async def test_get_status_counts_empty_db(self, store: SignalStore):
        """Should return empty dict for empty database."""
        counts = await store.get_status_counts()
        assert counts == {}

    @pytest.mark.asyncio
    async def test_get_status_counts_all_statuses(self, store_with_signals: SignalStore):
        """Should track all status types."""
        await store_with_signals.mark_pushed(1, "notion-123")
        await store_with_signals.mark_rejected(2, "Not a fit")

        counts = await store_with_signals.get_status_counts()

        assert counts["pushed"] == 1
        assert counts["rejected"] == 1


# =============================================================================
# GET PROCESSING STATS TESTS
# =============================================================================

class TestGetProcessingStats:
    """Tests for get_processing_stats method."""

    @pytest.mark.asyncio
    async def test_get_processing_stats(self, store_with_signals: SignalStore):
        """Should return processing statistics."""
        stats = await store_with_signals.get_processing_stats()

        assert "pending" in stats
        assert stats["pending"] == 2


# =============================================================================
# STATUS TRANSITION TESTS
# =============================================================================

class TestStatusTransitions:
    """Tests for valid status transitions."""

    @pytest.mark.asyncio
    async def test_status_transition_pending_to_pushed(self, store_with_signals: SignalStore):
        """pending -> pushed should work."""
        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "pending"

        await store_with_signals.mark_pushed(1, "notion-123")

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "pushed"

    @pytest.mark.asyncio
    async def test_status_transition_pending_to_rejected(self, store_with_signals: SignalStore):
        """pending -> rejected should work."""
        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "pending"

        await store_with_signals.mark_rejected(1, "Not a fit")

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "rejected"

    @pytest.mark.asyncio
    async def test_status_transition_pending_to_queued(self, store_with_signals: SignalStore):
        """pending -> queued should work."""
        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "pending"

        await store_with_signals.mark_queued(1)

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "queued"

    @pytest.mark.asyncio
    async def test_status_transition_idempotent(self, store_with_signals: SignalStore):
        """Transitioning to same status twice should be safe."""
        await store_with_signals.mark_pushed(1, "notion-123")
        await store_with_signals.mark_pushed(1, "notion-456")  # Update with new ID

        signal = await store_with_signals.get_signal(1)
        assert signal.processing_status == "pushed"
        assert signal.notion_page_id == "notion-456"


# =============================================================================
# EDGE CASES
# =============================================================================

class TestStatusEdgeCases:
    """Edge cases for status operations."""

    @pytest.mark.asyncio
    async def test_mark_nonexistent_signal(self, store: SignalStore):
        """Marking non-existent signal should not raise."""
        # Should not raise - just no-op
        await store.mark_pushed(99999, "notion-123")

        # Verify nothing was created
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signal_processing WHERE notion_page_id = ?",
            ("notion-123",)
        )
        count = (await cursor.fetchone())[0]
        assert count == 0

    @pytest.mark.asyncio
    async def test_status_counts_sum_to_total(self, store_with_signals: SignalStore):
        """Sum of status counts should equal total signals."""
        # Make some status changes
        await store_with_signals.mark_pushed(1, "notion-123")

        counts = await store_with_signals.get_status_counts()
        total = sum(counts.values())

        cursor = await store_with_signals._db.execute(
            "SELECT COUNT(*) FROM signal_processing"
        )
        actual_total = (await cursor.fetchone())[0]

        assert total == actual_total
