"""Tests for batch publisher workflow (Phase 1b Task 3).

Covers:
- create_batch: with limit, empty DB, duplicate guard
- preview_batch: deterministic output, not found
- commit_batch: dry-run read-only, delivery policy block, real push
- abort_batch: reverts reviews, audit trail, guard after push
- list_batches: filtering, ordering
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.review_store import create_review_item, update_review_status
from workflows.batch_publisher import (
    create_batch,
    preview_batch,
    commit_batch,
    abort_batch,
    list_batches,
    BatchError,
    BatchNotFoundError,
    BatchStateError,
    _generate_batch_id,
)


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


async def _seed_approved_reviews(store, count=3):
    """Seed approved reviews with company_files for testing."""
    review_ids = []
    now = datetime.now(timezone.utc).isoformat()

    # Insert company_files first, then commit to avoid nested tx
    for i in range(count):
        company_id = f"comp{i:03d}"
        canonical_key = f"domain:test{i}.com"
        await store._db.execute(
            """INSERT OR IGNORE INTO company_files
               (company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at)
               VALUES (?, ?, ?, 'promoted', '["github"]', ?, ?)""",
            (company_id, f"TestCo {i}", canonical_key, now, now),
        )
    await store._db.commit()

    # Create and approve reviews (each uses transaction_immediate internally)
    for i in range(count):
        company_id = f"comp{i:03d}"
        review_id = await create_review_item(store, company_id, [i + 1])
        await update_review_status(store, review_id, "approved", actor="test")
        review_ids.append(review_id)

    return review_ids


# =============================================================================
# BATCH ID GENERATION
# =============================================================================

class TestBatchIdGeneration:

    def test_batch_id_format(self):
        """Batch ID should match batch-YYYYMMDD-HHMMSS-<6hex>."""
        bid = _generate_batch_id()
        assert bid.startswith("batch-")
        parts = bid.split("-")
        # batch, YYYYMMDD, HHMMSS, 6hex
        assert len(parts) == 4
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS
        assert len(parts[3]) == 6  # hex suffix

    def test_batch_ids_are_unique(self):
        """Sequential IDs should not collide."""
        ids = {_generate_batch_id() for _ in range(100)}
        assert len(ids) == 100


# =============================================================================
# CREATE BATCH
# =============================================================================

class TestCreateBatch:

    @pytest.mark.asyncio
    async def test_create_with_approved_reviews(self, store):
        """create_batch should claim approved reviews."""
        review_ids = await _seed_approved_reviews(store, 3)

        result = await create_batch(store, limit=10)

        assert result["item_count"] == 3
        assert len(result["items"]) == 3
        assert result["batch_id"].startswith("batch-")

        # Reviews should now be publish_queued
        for rid in review_ids:
            cursor = await store._db.execute(
                "SELECT status FROM review_items WHERE id = ?", (rid,)
            )
            assert (await cursor.fetchone())[0] == "publish_queued"

    @pytest.mark.asyncio
    async def test_create_with_limit(self, store):
        """create_batch should respect the limit parameter."""
        await _seed_approved_reviews(store, 5)

        result = await create_batch(store, limit=2)

        assert result["item_count"] == 2

    @pytest.mark.asyncio
    async def test_create_empty_raises(self, store):
        """create_batch should raise BatchError if no approved reviews."""
        with pytest.raises(BatchError, match="No approved reviews"):
            await create_batch(store)

    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, store):
        """create_batch should write a batch_create audit entry."""
        await _seed_approved_reviews(store, 1)

        result = await create_batch(store)

        cursor = await store._db.execute(
            """SELECT action_type, entity_type, entity_id, details
               FROM audit_log WHERE action_type = 'batch_create'"""
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "publish_batch"
        assert row[2] == result["batch_id"]
        details = json.loads(row[3])
        assert details["item_count"] == 1

    @pytest.mark.asyncio
    async def test_create_batch_items_have_correct_data(self, store):
        """Batch items should have correct company_id and canonical_key."""
        await _seed_approved_reviews(store, 1)
        result = await create_batch(store)

        cursor = await store._db.execute(
            "SELECT company_id, canonical_key, status FROM batch_items WHERE batch_id = ?",
            (result["batch_id"],),
        )
        row = await cursor.fetchone()
        assert row[0] == "comp000"
        assert row[1] == "domain:test0.com"
        assert row[2] == "pending"


# =============================================================================
# PREVIEW BATCH
# =============================================================================

class TestPreviewBatch:

    @pytest.mark.asyncio
    async def test_preview_returns_items(self, store):
        """preview_batch should return all batch items."""
        await _seed_approved_reviews(store, 2)
        batch = await create_batch(store)

        preview = await preview_batch(store, batch["batch_id"])

        assert preview["batch_id"] == batch["batch_id"]
        assert preview["status"] == "draft"
        assert preview["item_count"] == 2
        assert len(preview["items"]) == 2

    @pytest.mark.asyncio
    async def test_preview_not_found(self, store):
        """preview_batch should raise BatchNotFoundError for unknown ID."""
        with pytest.raises(BatchNotFoundError):
            await preview_batch(store, "batch-nonexistent")

    @pytest.mark.asyncio
    async def test_preview_deterministic_order(self, store):
        """preview_batch should return items in stable order (by id)."""
        await _seed_approved_reviews(store, 3)
        batch = await create_batch(store)

        preview1 = await preview_batch(store, batch["batch_id"])
        preview2 = await preview_batch(store, batch["batch_id"])

        ids1 = [item["id"] for item in preview1["items"]]
        ids2 = [item["id"] for item in preview2["items"]]
        assert ids1 == ids2
        assert ids1 == sorted(ids1)


# =============================================================================
# COMMIT BATCH
# =============================================================================

class TestCommitBatch:

    @pytest.mark.asyncio
    async def test_dry_run_no_mutations(self, store):
        """commit_batch dry_run should not change batch or item statuses."""
        await _seed_approved_reviews(store, 2)
        batch = await create_batch(store)

        result = await commit_batch(store, batch["batch_id"], dry_run=True)

        assert result["dry_run"] is True
        assert result["pending_count"] == 2
        assert result["pushed_count"] == 0

        # Batch should still be draft
        cursor = await store._db.execute(
            "SELECT status FROM publish_batches WHERE id = ?",
            (batch["batch_id"],),
        )
        assert (await cursor.fetchone())[0] == "draft"

        # Items should still be pending
        cursor = await store._db.execute(
            "SELECT status FROM batch_items WHERE batch_id = ?",
            (batch["batch_id"],),
        )
        for row in await cursor.fetchall():
            assert row[0] == "pending"

    @pytest.mark.asyncio
    async def test_dry_run_writes_audit(self, store):
        """commit_batch dry_run should write a batch_commit_dry_run audit entry."""
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        await commit_batch(store, batch["batch_id"], dry_run=True)

        cursor = await store._db.execute(
            "SELECT action_type FROM audit_log WHERE action_type = 'batch_commit_dry_run'"
        )
        assert (await cursor.fetchone()) is not None

    @pytest.mark.asyncio
    async def test_commit_not_draft_raises(self, store):
        """commit_batch should reject non-draft batches."""
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        # Abort to change status
        await abort_batch(store, batch["batch_id"])

        with pytest.raises(BatchStateError, match="expected 'draft'"):
            await commit_batch(store, batch["batch_id"])

    @pytest.mark.asyncio
    async def test_commit_not_found_raises(self, store):
        """commit_batch should raise for unknown batch ID."""
        with pytest.raises(BatchNotFoundError):
            await commit_batch(store, "batch-nonexistent")

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_commit_staging_blocks(self, store):
        """commit_batch should raise DeliveryPolicyError in staging_only mode."""
        from workflows.delivery_policy import DeliveryPolicyError

        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        mock_pusher = MagicMock()
        with pytest.raises(DeliveryPolicyError):
            await commit_batch(
                store, batch["batch_id"], pusher=mock_pusher, dry_run=False
            )

    @pytest.mark.asyncio
    async def test_commit_requires_pusher(self, store):
        """commit_batch (non-dry-run) should raise without pusher."""
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
            with pytest.raises(BatchError, match="NotionPusher required"):
                await commit_batch(
                    store, batch["batch_id"], pusher=None, dry_run=False
                )


# =============================================================================
# ABORT BATCH
# =============================================================================

class TestAbortBatch:

    @pytest.mark.asyncio
    async def test_abort_reverts_reviews(self, store):
        """abort_batch should revert reviews from publish_queued to approved."""
        review_ids = await _seed_approved_reviews(store, 2)
        batch = await create_batch(store)

        result = await abort_batch(store, batch["batch_id"], reason="changed mind")

        assert result["reverted_count"] == 2

        # Reviews should be back to approved
        for rid in review_ids:
            cursor = await store._db.execute(
                "SELECT status FROM review_items WHERE id = ?", (rid,)
            )
            assert (await cursor.fetchone())[0] == "approved"

    @pytest.mark.asyncio
    async def test_abort_sets_batch_aborted(self, store):
        """abort_batch should set batch status to 'aborted'."""
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        await abort_batch(store, batch["batch_id"])

        cursor = await store._db.execute(
            "SELECT status, details FROM publish_batches WHERE id = ?",
            (batch["batch_id"],),
        )
        row = await cursor.fetchone()
        assert row[0] == "aborted"

    @pytest.mark.asyncio
    async def test_abort_writes_audit(self, store):
        """abort_batch should write a batch_abort audit entry."""
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        await abort_batch(store, batch["batch_id"], reason="test abort")

        cursor = await store._db.execute(
            """SELECT details FROM audit_log
               WHERE action_type = 'batch_abort' AND entity_id = ?""",
            (batch["batch_id"],),
        )
        row = await cursor.fetchone()
        assert row is not None
        details = json.loads(row[0])
        assert details["reason"] == "test abort"

    @pytest.mark.asyncio
    async def test_abort_not_draft_raises(self, store):
        """abort_batch should reject non-draft batches."""
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store)

        # Abort once
        await abort_batch(store, batch["batch_id"])

        # Try to abort again
        with pytest.raises(BatchStateError, match="can only abort 'draft'"):
            await abort_batch(store, batch["batch_id"])

    @pytest.mark.asyncio
    async def test_abort_not_found_raises(self, store):
        """abort_batch should raise for unknown batch ID."""
        with pytest.raises(BatchNotFoundError):
            await abort_batch(store, "batch-nonexistent")


# =============================================================================
# LIST BATCHES
# =============================================================================

class TestListBatches:

    @pytest.mark.asyncio
    async def test_list_empty(self, store):
        """list_batches should return empty list when no batches exist."""
        result = await list_batches(store)
        assert result == []

    @pytest.mark.asyncio
    async def test_list_returns_batches(self, store):
        """list_batches should return existing batches."""
        await _seed_approved_reviews(store, 2)
        batch = await create_batch(store)

        result = await list_batches(store)
        assert len(result) == 1
        assert result[0]["batch_id"] == batch["batch_id"]
        assert result[0]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, store):
        """list_batches should filter by status."""
        await _seed_approved_reviews(store, 3)
        batch1 = await create_batch(store, limit=1)
        batch2 = await create_batch(store, limit=1)

        await abort_batch(store, batch1["batch_id"])

        drafts = await list_batches(store, status="draft")
        assert len(drafts) == 1
        assert drafts[0]["batch_id"] == batch2["batch_id"]

        aborted = await list_batches(store, status="aborted")
        assert len(aborted) == 1
        assert aborted[0]["batch_id"] == batch1["batch_id"]
