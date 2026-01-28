"""
Tests for SignalStore outbox (durable queue) operations.

Covers:
- enqueue_notion_write: Add entry to outbox
- get_pending_outbox: Fetch pending entries
- mark_outbox_sent: Mark as successfully sent
- mark_outbox_failed: Mark as failed with retry
- claim_due_outbox: Atomic claim for processing
- finalize_outbox: Complete processing
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

from storage.signal_store import SignalStore


# =============================================================================
# ENQUEUE TESTS
# =============================================================================

class TestEnqueueNotionWrite:
    """Tests for enqueue_notion_write method."""

    @pytest.mark.asyncio
    async def test_enqueue_notion_write_creates_entry(self, store: SignalStore):
        """Should create entry in outbox."""
        outbox_id = await store.enqueue_notion_write(
            idempotency_key="test-key-001",
            payload={"company_name": "Test Corp", "action": "create"},
        )

        assert outbox_id > 0

        # Verify in DB
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        count = (await cursor.fetchone())[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_enqueue_notion_write_with_payload(self, store: SignalStore):
        """Should store payload correctly."""
        payload = {"company_name": "Test Corp", "confidence": 0.75, "signals": [1, 2, 3]}
        await store.enqueue_notion_write(
            idempotency_key="test-key-002",
            payload=payload,
        )

        pending = await store.get_pending_outbox(limit=1)
        assert len(pending) == 1
        assert pending[0]["payload"] == payload

    @pytest.mark.asyncio
    async def test_enqueue_notion_write_with_event_type(self, store: SignalStore):
        """Should store event type."""
        await store.enqueue_notion_write(
            idempotency_key="test-key-003",
            payload={},
            event_type="profile_update",
        )

        cursor = await store._db.execute(
            "SELECT event_type FROM notion_outbox WHERE idempotency_key = ?",
            ("test-key-003",)
        )
        row = await cursor.fetchone()
        assert row[0] == "profile_update"

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_key_fails(self, store: SignalStore):
        """Duplicate idempotency_key should raise."""
        await store.enqueue_notion_write(
            idempotency_key="unique-key",
            payload={},
        )

        with pytest.raises(Exception):  # IntegrityError
            await store.enqueue_notion_write(
                idempotency_key="unique-key",
                payload={"different": "payload"},
            )


# =============================================================================
# GET PENDING OUTBOX TESTS
# =============================================================================

class TestGetPendingOutbox:
    """Tests for get_pending_outbox method."""

    @pytest.mark.asyncio
    async def test_get_pending_outbox_returns_unprocessed(self, store: SignalStore):
        """Should return only pending entries."""
        await store.enqueue_notion_write("key-1", {"id": 1})
        await store.enqueue_notion_write("key-2", {"id": 2})

        pending = await store.get_pending_outbox()

        assert len(pending) == 2
        for entry in pending:
            assert entry["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_pending_outbox_respects_limit(self, store: SignalStore):
        """Should respect limit parameter."""
        for i in range(5):
            await store.enqueue_notion_write(f"key-{i}", {"id": i})

        pending = await store.get_pending_outbox(limit=3)
        assert len(pending) == 3

    @pytest.mark.asyncio
    async def test_get_pending_outbox_ordered_by_created(self, store: SignalStore):
        """Should return oldest first (FIFO)."""
        for i in range(3):
            await store.enqueue_notion_write(f"key-{i}", {"order": i})
            await asyncio.sleep(0.01)

        pending = await store.get_pending_outbox()

        # Oldest should be first
        assert pending[0]["payload"]["order"] == 0
        assert pending[2]["payload"]["order"] == 2

    @pytest.mark.asyncio
    async def test_get_pending_outbox_excludes_sent(self, store: SignalStore):
        """Should not return sent entries."""
        outbox_id = await store.enqueue_notion_write("key-sent", {})
        await store.mark_outbox_sent(outbox_id)

        pending = await store.get_pending_outbox()
        assert all(entry["idempotency_key"] != "key-sent" for entry in pending)

    @pytest.mark.asyncio
    async def test_get_pending_outbox_respects_next_attempt_at(self, store: SignalStore):
        """Should not return entries where next_attempt_at is in future."""
        outbox_id = await store.enqueue_notion_write("key-delayed", {})
        await store.mark_outbox_failed(outbox_id, "Error", backoff_seconds=3600)

        pending = await store.get_pending_outbox()

        # Entry should not be returned because it's scheduled for future
        assert all(entry["idempotency_key"] != "key-delayed" for entry in pending)


# =============================================================================
# MARK OUTBOX SENT TESTS
# =============================================================================

class TestMarkOutboxSent:
    """Tests for mark_outbox_sent method."""

    @pytest.mark.asyncio
    async def test_mark_outbox_sent_updates_status(self, store: SignalStore):
        """Should update status to 'sent'."""
        outbox_id = await store.enqueue_notion_write("key-to-send", {})
        await store.mark_outbox_sent(outbox_id)

        cursor = await store._db.execute(
            "SELECT status FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "sent"

    @pytest.mark.asyncio
    async def test_mark_outbox_sent_updates_timestamp(self, store: SignalStore):
        """Should update updated_at timestamp."""
        outbox_id = await store.enqueue_notion_write("key-to-send-2", {})
        before = datetime.now(timezone.utc)

        await store.mark_outbox_sent(outbox_id)

        cursor = await store._db.execute(
            "SELECT updated_at FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        updated_at = datetime.fromisoformat(row[0])
        assert updated_at >= before


# =============================================================================
# MARK OUTBOX FAILED TESTS
# =============================================================================

class TestMarkOutboxFailed:
    """Tests for mark_outbox_failed method."""

    @pytest.mark.asyncio
    async def test_mark_outbox_failed_increments_retry(self, store: SignalStore):
        """Should increment attempts counter."""
        outbox_id = await store.enqueue_notion_write("key-fail", {})

        await store.mark_outbox_failed(outbox_id, "First error")

        cursor = await store._db.execute(
            "SELECT attempts FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 1

        await store.mark_outbox_failed(outbox_id, "Second error")

        cursor = await store._db.execute(
            "SELECT attempts FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 2

    @pytest.mark.asyncio
    async def test_mark_outbox_failed_records_error(self, store: SignalStore):
        """Should store error message."""
        outbox_id = await store.enqueue_notion_write("key-error", {})
        error_msg = "Connection timeout to Notion API"

        await store.mark_outbox_failed(outbox_id, error_msg)

        cursor = await store._db.execute(
            "SELECT last_error FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == error_msg

    @pytest.mark.asyncio
    async def test_mark_outbox_failed_schedules_retry(self, store: SignalStore):
        """Should set next_attempt_at."""
        outbox_id = await store.enqueue_notion_write("key-retry", {})
        backoff = 60.0

        await store.mark_outbox_failed(outbox_id, "Error", backoff_seconds=backoff)

        cursor = await store._db.execute(
            "SELECT next_attempt_at FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        next_attempt = datetime.fromisoformat(row[0])
        now = datetime.now(timezone.utc)

        # Should be roughly backoff seconds in the future
        assert (next_attempt - now).total_seconds() > 50  # Allow some slack


# =============================================================================
# CLAIM DUE OUTBOX TESTS
# =============================================================================

class TestClaimDueOutbox:
    """Tests for claim_due_outbox method."""

    @pytest.mark.asyncio
    async def test_claim_due_outbox_returns_due_entries(self, store: SignalStore):
        """Should return entries ready for processing."""
        await store.enqueue_notion_write("key-claim-1", {"id": 1})
        await store.enqueue_notion_write("key-claim-2", {"id": 2})

        claimed = await store.claim_due_outbox(limit=10)

        assert len(claimed) == 2
        for entry in claimed:
            assert entry["payload"]["id"] in [1, 2]

    @pytest.mark.asyncio
    async def test_claim_due_outbox_marks_as_processing(self, store: SignalStore):
        """Should set status to 'processing'."""
        await store.enqueue_notion_write("key-processing", {})

        claimed = await store.claim_due_outbox(limit=1)
        assert len(claimed) == 1

        cursor = await store._db.execute(
            "SELECT status FROM notion_outbox WHERE idempotency_key = ?",
            ("key-processing",)
        )
        row = await cursor.fetchone()
        assert row[0] == "processing"

    @pytest.mark.asyncio
    async def test_claim_due_outbox_respects_max_attempts(self, store: SignalStore):
        """Should not return entries that exceeded max_attempts."""
        outbox_id = await store.enqueue_notion_write("key-exhausted", {})

        # Mark as failed many times
        for i in range(6):  # Default max_attempts is 5
            await store.mark_outbox_failed(outbox_id, f"Error {i}", backoff_seconds=0)

        # Wait for next_attempt_at to pass
        await asyncio.sleep(0.1)

        claimed = await store.claim_due_outbox(limit=10)
        assert all(e["idempotency_key"] != "key-exhausted" for e in claimed)

    @pytest.mark.asyncio
    async def test_claim_due_outbox_filters_by_event_type(self, store: SignalStore):
        """Should filter by event_type."""
        await store.enqueue_notion_write("key-push", {}, event_type="notion_push")
        await store.enqueue_notion_write("key-update", {}, event_type="profile_update")

        push_claimed = await store.claim_due_outbox(event_type="notion_push", limit=10)
        update_claimed = await store.claim_due_outbox(event_type="profile_update", limit=10)

        assert len(push_claimed) == 1
        assert push_claimed[0]["idempotency_key"] == "key-push"


# =============================================================================
# FINALIZE OUTBOX TESTS
# =============================================================================

class TestFinalizeOutbox:
    """Tests for finalize_outbox method."""

    @pytest.mark.asyncio
    async def test_finalize_outbox_success(self, store: SignalStore):
        """Finalize with success should mark as sent."""
        outbox_id = await store.enqueue_notion_write("key-finalize-ok", {})

        await store.finalize_outbox(outbox_id, success=True)

        cursor = await store._db.execute(
            "SELECT status FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "sent"

    @pytest.mark.asyncio
    async def test_finalize_outbox_failure(self, store: SignalStore):
        """Finalize with failure should schedule retry."""
        outbox_id = await store.enqueue_notion_write("key-finalize-fail", {})

        await store.finalize_outbox(outbox_id, success=False, error="Network error")

        cursor = await store._db.execute(
            "SELECT status, attempts, last_error FROM notion_outbox WHERE id = ?",
            (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "pending"  # Back to pending for retry
        assert row[1] == 1  # Attempts incremented
        assert row[2] == "Network error"

    @pytest.mark.asyncio
    async def test_finalize_outbox_max_retries_marks_failed(self, store: SignalStore):
        """Should mark as 'failed' when max attempts reached."""
        outbox_id = await store.enqueue_notion_write("key-max-retries", {})

        # Fail until max attempts
        for i in range(5):  # Default max_attempts is 5
            await store.finalize_outbox(
                outbox_id, success=False, error=f"Error {i+1}"
            )

        cursor = await store._db.execute(
            "SELECT status, attempts FROM notion_outbox WHERE id = ?", (outbox_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "failed"
        assert row[1] == 5


# =============================================================================
# EDGE CASES
# =============================================================================

class TestOutboxEdgeCases:
    """Edge cases for outbox operations."""

    @pytest.mark.asyncio
    async def test_concurrent_outbox_processing(self, store: SignalStore):
        """Concurrent claims should not return same entry."""
        await store.enqueue_notion_write("key-concurrent", {})

        # First claim
        claimed1 = await store.claim_due_outbox(limit=1)
        assert len(claimed1) == 1

        # Second claim should get nothing (already processing)
        claimed2 = await store.claim_due_outbox(limit=1)
        assert len(claimed2) == 0

    @pytest.mark.asyncio
    async def test_outbox_fifo_ordering(self, store: SignalStore):
        """Entries should be processed in FIFO order."""
        for i in range(5):
            await store.enqueue_notion_write(f"fifo-{i}", {"order": i})
            await asyncio.sleep(0.01)

        pending = await store.get_pending_outbox(limit=5)

        orders = [p["payload"]["order"] for p in pending]
        assert orders == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_outbox_empty_payload(self, store: SignalStore):
        """Empty payload should be handled."""
        outbox_id = await store.enqueue_notion_write("key-empty", {})

        pending = await store.get_pending_outbox(limit=1)
        assert pending[0]["payload"] == {}
