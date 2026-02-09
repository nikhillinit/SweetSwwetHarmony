"""End-to-end integration tests for batch publish lifecycle (Phase 1b Task 5).

Scenarios:
1. Full lifecycle: create → preview → commit (dry_run) — zero mutations
2. Full lifecycle: create → abort — reviews revert to approved
3. Staging mode blocks commit — DeliveryPolicyError
4. Abort guard — refuse abort when items have been pushed
5. Preview determinism — stable output across calls
6. Create → abort → re-create — reviews can be re-batched
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

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


async def _seed_signals_and_reviews(store, count=3):
    """Seed signals, company_files, and approved reviews for e2e testing."""
    now = datetime.now(timezone.utc).isoformat()
    review_ids = []

    # Insert signals and company_files
    for i in range(count):
        company_id = f"e2e-comp-{i:03d}"
        canonical_key = f"domain:e2e{i}.com"

        # Insert a signal with company_id
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name, confidence,
                raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "test_signal", "github", canonical_key,
                f"E2E Corp {i}", 0.75 + i * 0.05,
                json.dumps({"test": True}), now, now, company_id,
            ),
        )

        # Insert company_file
        await store._db.execute(
            """INSERT OR IGNORE INTO company_files
               (company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at)
               VALUES (?, ?, ?, 'promoted', '["github"]', ?, ?)""",
            (company_id, f"E2E Corp {i}", canonical_key, now, now),
        )
    await store._db.commit()

    # Create and approve reviews
    for i in range(count):
        company_id = f"e2e-comp-{i:03d}"
        review_id = await create_review_item(store, company_id, [i + 1])
        await update_review_status(store, review_id, "approved", actor="test")
        review_ids.append(review_id)

    return review_ids


class TestFullLifecycleDryRun:
    """create → preview → commit (dry-run) should have zero DB mutations."""

    @pytest.mark.asyncio
    async def test_dry_run_leaves_batch_and_items_intact(self, store):
        """Dry-run commit should not mutate batch or item statuses."""
        await _seed_signals_and_reviews(store, 3)

        # Create batch
        batch = await create_batch(store)
        batch_id = batch["batch_id"]
        assert batch["item_count"] == 3

        # Preview
        preview = await preview_batch(store, batch_id)
        assert preview["status"] == "draft"
        assert len(preview["items"]) == 3

        # Commit dry-run
        result = await commit_batch(store, batch_id, dry_run=True)
        assert result["dry_run"] is True
        assert result["pending_count"] == 3

        # Verify: batch still draft
        cursor = await store._db.execute(
            "SELECT status FROM publish_batches WHERE id = ?", (batch_id,)
        )
        assert (await cursor.fetchone())[0] == "draft"

        # Verify: all items still pending
        cursor = await store._db.execute(
            "SELECT DISTINCT status FROM batch_items WHERE batch_id = ?",
            (batch_id,),
        )
        statuses = {row[0] for row in await cursor.fetchall()}
        assert statuses == {"pending"}

        # Verify: reviews still publish_queued (not mutated by dry-run)
        cursor = await store._db.execute(
            "SELECT DISTINCT status FROM review_items WHERE status = 'publish_queued'"
        )
        assert (await cursor.fetchone())[0] == "publish_queued"


class TestFullLifecycleAbort:
    """create → abort should revert reviews to approved."""

    @pytest.mark.asyncio
    async def test_abort_reverts_all_reviews(self, store):
        """After abort, all reviews should be back to approved."""
        review_ids = await _seed_signals_and_reviews(store, 3)

        batch = await create_batch(store)
        batch_id = batch["batch_id"]

        # Verify reviews are publish_queued
        for rid in review_ids:
            cursor = await store._db.execute(
                "SELECT status FROM review_items WHERE id = ?", (rid,)
            )
            assert (await cursor.fetchone())[0] == "publish_queued"

        # Abort
        result = await abort_batch(store, batch_id, reason="e2e test abort")
        assert result["reverted_count"] == 3

        # Verify reviews reverted to approved
        for rid in review_ids:
            cursor = await store._db.execute(
                "SELECT status FROM review_items WHERE id = ?", (rid,)
            )
            assert (await cursor.fetchone())[0] == "approved"

        # Verify batch is aborted
        cursor = await store._db.execute(
            "SELECT status FROM publish_batches WHERE id = ?", (batch_id,)
        )
        assert (await cursor.fetchone())[0] == "aborted"


class TestStagingBlocksCommit:
    """DELIVERY_MODE=staging_only should block real commits."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_staging_blocks_real_commit(self, store):
        """Real commit in staging_only mode should raise DeliveryPolicyError."""
        from workflows.delivery_policy import DeliveryPolicyError
        from unittest.mock import MagicMock

        await _seed_signals_and_reviews(store, 1)
        batch = await create_batch(store)

        mock_pusher = MagicMock()
        with pytest.raises(DeliveryPolicyError):
            await commit_batch(
                store, batch["batch_id"],
                pusher=mock_pusher, dry_run=False,
            )

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_staging_allows_dry_run(self, store):
        """Dry-run should work even in staging_only mode."""
        await _seed_signals_and_reviews(store, 1)
        batch = await create_batch(store)

        result = await commit_batch(store, batch["batch_id"], dry_run=True)
        assert result["dry_run"] is True


class TestAbortGuardAfterPush:
    """Abort should be refused if any items were already pushed."""

    @pytest.mark.asyncio
    async def test_abort_refused_after_push(self, store):
        """If an item has status='pushed', abort should raise BatchStateError."""
        await _seed_signals_and_reviews(store, 2)
        batch = await create_batch(store)
        batch_id = batch["batch_id"]

        # Manually mark one item as pushed (simulating partial commit)
        await store._db.execute(
            """UPDATE batch_items SET status = 'pushed'
               WHERE batch_id = ? AND id = (
                   SELECT MIN(id) FROM batch_items WHERE batch_id = ?
               )""",
            (batch_id, batch_id),
        )
        await store._db.commit()

        with pytest.raises(BatchStateError, match="already pushed"):
            await abort_batch(store, batch_id)


class TestPreviewDeterminism:
    """Preview should produce stable output with multiple signals per company."""

    @pytest.mark.asyncio
    async def test_preview_stable_across_calls(self, store):
        """Multiple preview calls should return identical output."""
        now = datetime.now(timezone.utc).isoformat()
        company_id = "det-comp-001"
        canonical_key = "domain:deterministic.com"

        # Seed multiple signals for same company with different confidences
        # Use different source_api to avoid UNIQUE constraint on (canonical_key, signal_type, source_api, detected_at)
        sources = ["github", "sec_edgar", "hacker_news"]
        for conf, source in zip([0.6, 0.9, 0.3], sources):
            await store._db.execute(
                """INSERT INTO signals
                   (signal_type, source_api, canonical_key, company_name, confidence,
                    raw_data, detected_at, created_at, company_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "test_signal", source, canonical_key,
                    "Deterministic Corp", conf,
                    json.dumps({"conf": conf}), now, now, company_id,
                ),
            )

        await store._db.execute(
            """INSERT OR IGNORE INTO company_files
               (company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at)
               VALUES (?, ?, ?, 'promoted', '["github"]', ?, ?)""",
            (company_id, "Deterministic Corp", canonical_key, now, now),
        )
        await store._db.commit()

        review_id = await create_review_item(store, company_id, [1, 2, 3])
        await update_review_status(store, review_id, "approved", actor="test")

        batch = await create_batch(store)

        p1 = await preview_batch(store, batch["batch_id"])
        p2 = await preview_batch(store, batch["batch_id"])

        # Same items in same order
        assert len(p1["items"]) == len(p2["items"])
        for i1, i2 in zip(p1["items"], p2["items"]):
            assert i1["id"] == i2["id"]
            assert i1["company_name"] == i2["company_name"]
            assert i1["confidence"] == i2["confidence"]

        # Confidence should be the MAX (0.9)
        assert p1["items"][0]["confidence"] == 0.9


class TestAbortThenReBatch:
    """After abort, reviews should be re-batchable."""

    @pytest.mark.asyncio
    async def test_rebatch_after_abort(self, store):
        """Reviews reverted by abort should be claimable by a new batch."""
        await _seed_signals_and_reviews(store, 2)

        batch1 = await create_batch(store)
        await abort_batch(store, batch1["batch_id"])

        # Reviews are now approved again — create new batch
        batch2 = await create_batch(store)
        assert batch2["item_count"] == 2
        assert batch2["batch_id"] != batch1["batch_id"]

        # Both batches visible in list
        batches = await list_batches(store)
        assert len(batches) == 2
        statuses = {b["status"] for b in batches}
        assert statuses == {"draft", "aborted"}
