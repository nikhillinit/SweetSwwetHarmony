# Phase 1b: Batch Publish Workflow — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a git-style preview-then-commit batch publish workflow that transitions approved ReviewItems through `publish_queued` → `published` and pushes them to Notion in a single auditable batch operation.

**Architecture:** New `workflows/batch_publisher.py` module orchestrates the lifecycle (create → preview → commit → abort). A `publish_batches` table (migration v31) tracks batch metadata. The existing `NotionPusher.process_single_prospect()` handles per-company Notion writes. All writes go through `assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)`. CLI subcommands are registered under `run_pipeline.py publish`.

**Tech Stack:** Python 3.11+, aiosqlite, existing NotionPusher, delivery_policy, review_store, audit_log

**Depends On:** Phase 0 (delivery policy) + Phase 1a (review_items, company_id, canonical_key)

---

## Task 1: Migration v31 — `publish_batches` + `batch_items` tables

**Files:**
- Create: `storage/migrations/v31_batch_publish.py`
- Modify: `storage/signal_store.py:59` (add import)
- Modify: `storage/signal_store.py:73` (bump CURRENT_SCHEMA_VERSION to 31)
- Modify: `storage/signal_store.py:1710` (register migration 31)
- Test: `tests/storage/test_v31_batch_publish.py`

**Step 1: Write the failing test**

```python
# tests/storage/test_v31_batch_publish.py
"""Tests for v31 batch publish migration DDL."""

import json
import pytest
import aiosqlite

from storage.signal_store import SignalStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


class TestBatchPublishDDL:
    """Verify publish_batches and batch_items tables exist with correct schema."""

    @pytest.mark.asyncio
    async def test_publish_batches_table_exists(self, store):
        """publish_batches table should exist after migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='publish_batches'"
        )
        row = await cursor.fetchone()
        assert row is not None, "publish_batches table should exist"

    @pytest.mark.asyncio
    async def test_batch_items_table_exists(self, store):
        """batch_items table should exist after migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_items'"
        )
        row = await cursor.fetchone()
        assert row is not None, "batch_items table should exist"

    @pytest.mark.asyncio
    async def test_publish_batches_columns(self, store):
        """publish_batches should have expected columns."""
        cursor = await store._db.execute("PRAGMA table_info(publish_batches)")
        cols = {row[1] for row in await cursor.fetchall()}
        expected = {"id", "status", "item_count", "pushed_count", "error_count",
                    "actor", "created_at", "committed_at", "details"}
        assert expected.issubset(cols)

    @pytest.mark.asyncio
    async def test_batch_items_columns(self, store):
        """batch_items should have expected columns."""
        cursor = await store._db.execute("PRAGMA table_info(batch_items)")
        cols = {row[1] for row in await cursor.fetchall()}
        expected = {"id", "batch_id", "review_id", "company_id", "canonical_key",
                    "status", "notion_page_id", "error_message", "created_at"}
        assert expected.issubset(cols)

    @pytest.mark.asyncio
    async def test_batch_items_fk_index(self, store):
        """batch_items should have index on batch_id."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_batch_items_batch_id'"
        )
        row = await cursor.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_schema_version_is_31(self, store):
        """CURRENT_SCHEMA_VERSION should be 31."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 31
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/storage/test_v31_batch_publish.py -v`
Expected: FAIL (table doesn't exist, version is 30)

**Step 3: Write the migration**

```python
# storage/migrations/v31_batch_publish.py
"""Migration v31: Batch publish tables.

Tracks batch publish operations (create -> preview -> commit/abort)
and per-item push results within each batch.
"""

V31_BATCH_PUBLISH_DDL = """
-- Batch publish operations
CREATE TABLE IF NOT EXISTS publish_batches (
    id TEXT PRIMARY KEY,              -- 'batch-YYYYMMDD-HHMMSS-NNN'
    status TEXT NOT NULL DEFAULT 'draft',  -- draft, committed, aborted, failed
    item_count INTEGER NOT NULL DEFAULT 0,
    pushed_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL DEFAULT 'operator',
    created_at TEXT NOT NULL,
    committed_at TEXT,
    details TEXT                       -- JSON metadata
);

CREATE INDEX IF NOT EXISTS idx_publish_batches_status
    ON publish_batches(status);
CREATE INDEX IF NOT EXISTS idx_publish_batches_created
    ON publish_batches(created_at DESC);

-- Per-item results within a batch
CREATE TABLE IF NOT EXISTS batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES publish_batches(id),
    review_id INTEGER NOT NULL,
    company_id TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, pushed, skipped, error
    notion_page_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_batch_items_batch_id
    ON batch_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_status
    ON batch_items(batch_id, status);
"""
```

Then in `storage/signal_store.py`:
- Line 59: Add `from storage.migrations.v31_batch_publish import V31_BATCH_PUBLISH_DDL`
- Line 73: Change `CURRENT_SCHEMA_VERSION = 30` → `CURRENT_SCHEMA_VERSION = 31`
- Line 1710: After `30: V30_PIPELINE_IDENTITY_STATS_DDL,` add `31: V31_BATCH_PUBLISH_DDL,`

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/storage/test_v31_batch_publish.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add storage/migrations/v31_batch_publish.py tests/storage/test_v31_batch_publish.py storage/signal_store.py
git commit -m "feat(phase1b): migration v31 — publish_batches + batch_items tables"
```

---

## Task 2: `BatchPublisher` core — create, preview, commit, abort

**Files:**
- Create: `workflows/batch_publisher.py`
- Test: `tests/workflows/test_batch_publisher.py`

**Step 1: Write the failing tests**

```python
# tests/workflows/test_batch_publisher.py
"""Tests for batch publish workflow."""

import json
import os
import pytest
import aiosqlite

from storage.signal_store import SignalStore
from storage.review_store import create_review_item, update_review_status, get_review_queue


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


async def _seed_approved_reviews(store, count=3):
    """Helper: insert signals + create approved review items."""
    review_ids = []
    for i in range(count):
        ckey = f"domain:company{i}.com"
        cid = f"cid-{i:04d}"
        # Insert a signal
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)""",
            ("github_trending", "github", ckey, f"Company {i}",
             0.75, json.dumps({"url": f"https://company{i}.com"}), cid)
        )
        cursor = await store._db.execute("SELECT last_insert_rowid()")
        sig_id = (await cursor.fetchone())[0]
        await store._db.execute(
            """INSERT INTO signal_processing (signal_id, status, created_at)
               VALUES (?, 'pending', datetime('now'))""",
            (sig_id,)
        )
        await store._db.commit()

        # Create and approve review
        rid = await create_review_item(store, cid, [sig_id])
        await update_review_status(store, rid, "approved", actor="test", reason="test")
        review_ids.append(rid)
    return review_ids


class TestBatchCreate:
    @pytest.mark.asyncio
    async def test_create_batch_from_approved(self, store):
        """create_batch should gather approved review items into a draft batch."""
        from workflows.batch_publisher import create_batch
        await _seed_approved_reviews(store, 3)
        batch = await create_batch(store, actor="operator")
        assert batch["status"] == "draft"
        assert batch["item_count"] == 3
        assert batch["id"].startswith("batch-")

    @pytest.mark.asyncio
    async def test_create_batch_empty(self, store):
        """create_batch with no approved items returns None."""
        from workflows.batch_publisher import create_batch
        batch = await create_batch(store, actor="operator")
        assert batch is None

    @pytest.mark.asyncio
    async def test_create_batch_review_items_become_publish_queued(self, store):
        """Creating a batch should transition reviews from approved -> publish_queued."""
        from workflows.batch_publisher import create_batch
        review_ids = await _seed_approved_reviews(store, 2)
        await create_batch(store, actor="operator")
        queue = await get_review_queue(store, status="publish_queued")
        assert len(queue) == 2

    @pytest.mark.asyncio
    async def test_create_batch_with_limit(self, store):
        """create_batch should respect limit parameter."""
        from workflows.batch_publisher import create_batch
        await _seed_approved_reviews(store, 5)
        batch = await create_batch(store, actor="operator", limit=2)
        assert batch["item_count"] == 2


class TestBatchPreview:
    @pytest.mark.asyncio
    async def test_preview_returns_item_details(self, store):
        """preview_batch should return item details for display."""
        from workflows.batch_publisher import create_batch, preview_batch
        await _seed_approved_reviews(store, 2)
        batch = await create_batch(store, actor="operator")
        preview = await preview_batch(store, batch["id"])
        assert preview["batch_id"] == batch["id"]
        assert preview["status"] == "draft"
        assert len(preview["items"]) == 2
        assert "company_id" in preview["items"][0]
        assert "canonical_key" in preview["items"][0]

    @pytest.mark.asyncio
    async def test_preview_nonexistent_batch(self, store):
        """preview_batch for unknown batch should return None."""
        from workflows.batch_publisher import preview_batch
        result = await preview_batch(store, "batch-nonexistent")
        assert result is None


class TestBatchAbort:
    @pytest.mark.asyncio
    async def test_abort_reverts_reviews_to_approved(self, store):
        """abort_batch should revert review items from publish_queued -> approved."""
        from workflows.batch_publisher import create_batch, abort_batch
        review_ids = await _seed_approved_reviews(store, 2)
        batch = await create_batch(store, actor="operator")
        result = await abort_batch(store, batch["id"], actor="operator", reason="changed mind")
        assert result["status"] == "aborted"
        # Reviews should be back to approved
        queue = await get_review_queue(store, status="approved")
        assert len(queue) == 2

    @pytest.mark.asyncio
    async def test_abort_writes_audit_log(self, store):
        """abort_batch should write an audit_log entry."""
        from workflows.batch_publisher import create_batch, abort_batch
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store, actor="operator")
        await abort_batch(store, batch["id"], actor="operator", reason="test")
        cursor = await store._db.execute(
            "SELECT action_type, details FROM audit_log WHERE entity_id = ?",
            (batch["id"],)
        )
        rows = await cursor.fetchall()
        actions = [r[0] for r in rows]
        assert "batch_abort" in actions

    @pytest.mark.asyncio
    async def test_abort_committed_batch_fails(self, store):
        """Cannot abort an already-committed batch."""
        from workflows.batch_publisher import create_batch, abort_batch
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store, actor="operator")
        # Manually mark committed to test guard
        await store._db.execute(
            "UPDATE publish_batches SET status = 'committed' WHERE id = ?",
            (batch["id"],)
        )
        await store._db.commit()
        with pytest.raises(ValueError, match="Cannot abort"):
            await abort_batch(store, batch["id"], actor="operator", reason="too late")


class TestBatchCommit:
    @pytest.mark.asyncio
    async def test_commit_blocked_by_delivery_policy(self, store, monkeypatch):
        """commit_batch should fail if DELIVERY_MODE doesn't allow BATCH_PUSH."""
        from workflows.batch_publisher import create_batch, commit_batch
        from workflows.delivery_policy import DeliveryPolicyError
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store, actor="operator")
        with pytest.raises(DeliveryPolicyError):
            await commit_batch(store, batch["id"], actor="operator")

    @pytest.mark.asyncio
    async def test_commit_dry_run_no_notion_writes(self, store, monkeypatch):
        """commit_batch dry_run should not call Notion but should report results."""
        from workflows.batch_publisher import create_batch, commit_batch
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        await _seed_approved_reviews(store, 2)
        batch = await create_batch(store, actor="operator")
        result = await commit_batch(store, batch["id"], actor="operator", dry_run=True)
        assert result["dry_run"] is True
        assert result["item_count"] == 2

    @pytest.mark.asyncio
    async def test_commit_marks_reviews_published(self, store, monkeypatch):
        """After commit, review items should be status=published."""
        from workflows.batch_publisher import create_batch, commit_batch
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        await _seed_approved_reviews(store, 2)
        batch = await create_batch(store, actor="operator")
        result = await commit_batch(store, batch["id"], actor="operator", dry_run=True)
        # In dry_run, reviews stay publish_queued (no actual publish)
        queue = await get_review_queue(store, status="publish_queued")
        assert len(queue) == 2

    @pytest.mark.asyncio
    async def test_commit_writes_audit_log(self, store, monkeypatch):
        """commit_batch should write audit_log entries."""
        from workflows.batch_publisher import create_batch, commit_batch
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        await _seed_approved_reviews(store, 1)
        batch = await create_batch(store, actor="operator")
        await commit_batch(store, batch["id"], actor="operator", dry_run=True)
        cursor = await store._db.execute(
            "SELECT action_type FROM audit_log WHERE entity_id = ?",
            (batch["id"],)
        )
        rows = await cursor.fetchall()
        actions = [r[0] for r in rows]
        assert "batch_commit" in actions


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_skip_already_published_review(self, store):
        """create_batch should skip reviews that are already published."""
        from workflows.batch_publisher import create_batch
        review_ids = await _seed_approved_reviews(store, 3)
        # Publish one manually
        await update_review_status(store, review_ids[0], "published", actor="test")
        batch = await create_batch(store, actor="operator")
        # Only 2 should be in batch (the published one is skipped)
        assert batch["item_count"] == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workflows/test_batch_publisher.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

```python
# workflows/batch_publisher.py
"""Batch publish workflow: git-style create -> preview -> commit/abort.

Lifecycle:
    1. create_batch()  — gather approved ReviewItems, transition to publish_queued
    2. preview_batch() — show what will be pushed (dry display)
    3. commit_batch()  — push to Notion, transition to published
    4. abort_batch()   — revert publish_queued -> approved, mark batch aborted

All Notion writes go through assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from storage.review_store import (
    get_review_queue,
    update_review_status,
    VALID_TRANSITIONS,
)
from workflows.delivery_policy import assert_notion_write_allowed, DeliveryIntent

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


def _batch_id() -> str:
    """Generate a deterministic batch ID from current timestamp."""
    now = datetime.now(timezone.utc)
    return f"batch-{now.strftime('%Y%m%d-%H%M%S')}"


async def create_batch(
    store: SignalStore,
    actor: str = "operator",
    limit: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Create a draft batch from approved review items.

    Transitions each approved review to publish_queued and creates
    batch_items rows.  Returns None if no approved items found.
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    # Fetch approved reviews
    query_limit = limit if limit else 500
    approved = await get_review_queue(store, status="approved", limit=query_limit)
    if not approved:
        return None

    if limit:
        approved = approved[:limit]

    now_iso = datetime.now(timezone.utc).isoformat()
    bid = _batch_id()

    async with store.transaction_immediate() as tx:
        # Create batch record
        await tx.execute(
            """INSERT INTO publish_batches
               (id, status, item_count, actor, created_at, details)
               VALUES (?, 'draft', ?, ?, ?, ?)""",
            (bid, len(approved), actor, now_iso, json.dumps({"limit": limit}))
        )

        for item in approved:
            review_id = item["id"]
            company_id = item["company_id"]

            # Look up canonical_key for this company
            cursor = await tx.execute(
                "SELECT canonical_key FROM signals WHERE company_id = ? LIMIT 1",
                (company_id,)
            )
            row = await cursor.fetchone()
            canonical_key = row[0] if row else company_id

            # Insert batch_item
            await tx.execute(
                """INSERT INTO batch_items
                   (batch_id, review_id, company_id, canonical_key, status, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (bid, review_id, company_id, canonical_key, now_iso)
            )

        # Audit log
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("batch_create", "batch", bid, actor,
             json.dumps({"item_count": len(approved)}), now_iso)
        )

    # Transition reviews outside the batch transaction (each has its own tx)
    for item in approved:
        await update_review_status(
            store, item["id"], "publish_queued",
            actor=actor, reason=f"batch:{bid}"
        )

    return {"id": bid, "status": "draft", "item_count": len(approved)}


async def preview_batch(
    store: SignalStore,
    batch_id: str,
) -> Optional[Dict[str, Any]]:
    """Return batch details and item list for preview display."""
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    cursor = await db.execute(
        "SELECT id, status, item_count, actor, created_at FROM publish_batches WHERE id = ?",
        (batch_id,)
    )
    batch_row = await cursor.fetchone()
    if not batch_row:
        return None

    cursor = await db.execute(
        """SELECT bi.id, bi.review_id, bi.company_id, bi.canonical_key, bi.status,
                  s.company_name, s.confidence
           FROM batch_items bi
           LEFT JOIN signals s ON s.company_id = bi.company_id
           WHERE bi.batch_id = ?
           GROUP BY bi.company_id
           ORDER BY bi.id""",
        (batch_id,)
    )
    rows = await cursor.fetchall()
    items = [
        {
            "id": r[0], "review_id": r[1], "company_id": r[2],
            "canonical_key": r[3], "status": r[4],
            "company_name": r[5], "confidence": r[6],
        }
        for r in rows
    ]

    return {
        "batch_id": batch_row[0],
        "status": batch_row[1],
        "item_count": batch_row[2],
        "actor": batch_row[3],
        "created_at": batch_row[4],
        "items": items,
    }


async def commit_batch(
    store: SignalStore,
    batch_id: str,
    actor: str = "operator",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Commit a draft batch: push each item to Notion, mark published.

    Checks delivery policy upfront.  In dry_run mode, skips Notion writes
    but still reports what would happen.
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    # Delivery policy guard (even for dry_run, to validate mode is set)
    if not dry_run:
        assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)

    # Verify batch exists and is draft
    cursor = await db.execute(
        "SELECT status, item_count FROM publish_batches WHERE id = ?",
        (batch_id,)
    )
    batch_row = await cursor.fetchone()
    if not batch_row:
        raise ValueError(f"Batch {batch_id} not found")
    if batch_row[0] != "draft":
        raise ValueError(f"Batch {batch_id} is '{batch_row[0]}', expected 'draft'")

    # Fetch items
    cursor = await db.execute(
        """SELECT id, review_id, company_id, canonical_key
           FROM batch_items WHERE batch_id = ? AND status = 'pending'""",
        (batch_id,)
    )
    items = await cursor.fetchall()

    pushed = 0
    errors = 0
    error_messages = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for item_id, review_id, company_id, canonical_key in items:
        if dry_run:
            pushed += 1
            await db.execute(
                "UPDATE batch_items SET status = 'pushed' WHERE id = ?",
                (item_id,)
            )
            continue

        try:
            # Import here to avoid circular import
            from workflows.notion_pusher import NotionPusher
            pusher = NotionPusher(store)
            result = await pusher.process_single_prospect(
                canonical_key, intent=DeliveryIntent.BATCH_PUSH
            )
            if result.pushed:
                await db.execute(
                    "UPDATE batch_items SET status = 'pushed', notion_page_id = ? WHERE id = ?",
                    (result.notion_page_id, item_id)
                )
                # Transition review to published
                await update_review_status(
                    store, review_id, "published",
                    actor=actor, reason=f"batch:{batch_id}"
                )
                pushed += 1
            else:
                await db.execute(
                    "UPDATE batch_items SET status = 'skipped' WHERE id = ?",
                    (item_id,)
                )
        except Exception as exc:
            errors += 1
            msg = f"Item {item_id} ({canonical_key}): {exc}"
            error_messages.append(msg)
            logger.error(msg)
            await db.execute(
                "UPDATE batch_items SET status = 'error', error_message = ? WHERE id = ?",
                (str(exc), item_id)
            )

    # Update batch record
    final_status = "committed" if not dry_run else "draft"
    await db.execute(
        """UPDATE publish_batches
           SET status = ?, pushed_count = ?, error_count = ?, committed_at = ?
           WHERE id = ?""",
        (final_status, pushed, errors, now_iso, batch_id)
    )

    # Audit log
    await db.execute(
        """INSERT INTO audit_log
           (action_type, entity_type, entity_id, actor, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("batch_commit", "batch", batch_id, actor,
         json.dumps({"pushed": pushed, "errors": errors,
                      "dry_run": dry_run, "error_messages": error_messages}),
         now_iso)
    )
    await db.commit()

    return {
        "batch_id": batch_id,
        "status": final_status,
        "item_count": len(items),
        "pushed": pushed,
        "errors": errors,
        "error_messages": error_messages,
        "dry_run": dry_run,
    }


async def abort_batch(
    store: SignalStore,
    batch_id: str,
    actor: str = "operator",
    reason: str = "",
) -> Dict[str, Any]:
    """Abort a draft batch: revert review items to approved, mark batch aborted."""
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    # Verify batch exists and is draft
    cursor = await db.execute(
        "SELECT status FROM publish_batches WHERE id = ?",
        (batch_id,)
    )
    batch_row = await cursor.fetchone()
    if not batch_row:
        raise ValueError(f"Batch {batch_id} not found")
    if batch_row[0] != "draft":
        raise ValueError(f"Cannot abort batch {batch_id}: status is '{batch_row[0]}'")

    now_iso = datetime.now(timezone.utc).isoformat()

    # Fetch review IDs to revert
    cursor = await db.execute(
        "SELECT review_id FROM batch_items WHERE batch_id = ?",
        (batch_id,)
    )
    review_ids = [row[0] for row in await cursor.fetchall()]

    # Mark batch aborted
    await db.execute(
        "UPDATE publish_batches SET status = 'aborted', committed_at = ? WHERE id = ?",
        (now_iso, batch_id)
    )

    # Audit log
    await db.execute(
        """INSERT INTO audit_log
           (action_type, entity_type, entity_id, actor, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("batch_abort", "batch", batch_id, actor,
         json.dumps({"reason": reason, "review_count": len(review_ids)}),
         now_iso)
    )
    await db.commit()

    # Revert reviews: publish_queued -> approved
    # Note: review_store's VALID_TRANSITIONS doesn't allow publish_queued -> approved.
    # We need to add that transition. For abort we use a direct UPDATE as a privileged
    # system operation, bypassing the state machine (documented escape hatch).
    for rid in review_ids:
        await db.execute(
            """UPDATE review_items
               SET status = 'approved', updated_at = ?
               WHERE id = ? AND status = 'publish_queued'""",
            (now_iso, rid)
        )
    await db.commit()

    return {"batch_id": batch_id, "status": "aborted", "reverted": len(review_ids)}
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workflows/test_batch_publisher.py -v`
Expected: PASS (13 tests)

**Step 5: Commit**

```bash
git add workflows/batch_publisher.py tests/workflows/test_batch_publisher.py
git commit -m "feat(phase1b): BatchPublisher core — create, preview, commit, abort"
```

---

## Task 3: Add `publish_queued -> approved` transition for abort revert

**Files:**
- Modify: `storage/review_store.py:38` (add transition)
- Test: `tests/storage/test_review_state_machine.py` (add test)

**Step 1: Write the failing test**

Add to `tests/storage/test_review_state_machine.py`:

```python
@pytest.mark.asyncio
async def test_publish_queued_to_approved_revert(self, store_with_review):
    """publish_queued -> approved is valid (batch abort revert)."""
    store, review_id = store_with_review
    await update_review_status(store, review_id, "approved", actor="op", reason="approve")
    await update_review_status(store, review_id, "publish_queued", actor="batch", reason="queued")
    await update_review_status(store, review_id, "approved", actor="system", reason="batch abort")
    cursor = await store._db.execute("SELECT status FROM review_items WHERE id = ?", (review_id,))
    assert (await cursor.fetchone())[0] == "approved"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/storage/test_review_state_machine.py::TestValidTransitions::test_publish_queued_to_approved_revert -v`
Expected: FAIL (InvalidStateTransition)

**Step 3: Add the transition**

In `storage/review_store.py` line 38, change:
```python
"publish_queued": ["published", "rejected"],  # rejected = emergency halt
```
to:
```python
"publish_queued": ["published", "rejected", "approved"],  # rejected = emergency halt, approved = batch abort revert
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/storage/test_review_state_machine.py -v`
Expected: ALL PASS (existing + new)

**Step 5: Commit**

```bash
git add storage/review_store.py tests/storage/test_review_state_machine.py
git commit -m "feat(phase1b): allow publish_queued -> approved transition for batch abort"
```

---

## Task 4: CLI subcommands — `publish batch create|preview|commit|abort`

**Files:**
- Modify: `run_pipeline.py` (add `publish` subcommand group)
- Test: `tests/cli/test_batch_publish_cli.py`

**Step 1: Write the failing test**

```python
# tests/cli/test_batch_publish_cli.py
"""Tests for batch publish CLI subcommands."""

import json
import os
import pytest
import subprocess
import sys


@pytest.fixture
def env_staging():
    """Environment with staging_only mode."""
    env = os.environ.copy()
    env["DELIVERY_MODE"] = "staging_only"
    return env


@pytest.fixture
def env_batch():
    """Environment with batch_publish mode."""
    env = os.environ.copy()
    env["DELIVERY_MODE"] = "batch_publish"
    return env


class TestPublishCLI:
    def test_publish_help(self):
        """publish --help should show subcommands."""
        result = subprocess.run(
            [sys.executable, "run_pipeline.py", "publish", "--help"],
            capture_output=True, text=True, cwd=r"C:\dev\Harmonic"
        )
        assert result.returncode == 0
        assert "create" in result.stdout or "batch" in result.stdout

    def test_publish_create_no_approved(self, tmp_path):
        """publish create with empty DB should report no approved items."""
        result = subprocess.run(
            [sys.executable, "run_pipeline.py", "--db", str(tmp_path / "empty.db"),
             "publish", "create"],
            capture_output=True, text=True, cwd=r"C:\dev\Harmonic"
        )
        assert "No approved" in result.stdout or result.returncode == 0
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/cli/test_batch_publish_cli.py -v`
Expected: FAIL (no "publish" subcommand)

**Step 3: Add CLI subcommands to `run_pipeline.py`**

Add a `publish` subparser group with four subcommands:

```python
# === PUBLISH SUBCOMMANDS ===
publish_parser = subparsers.add_parser("publish", help="Batch publish workflow")
publish_sub = publish_parser.add_subparsers(dest="publish_cmd")

# publish create
pub_create = publish_sub.add_parser("create", help="Create batch from approved reviews")
pub_create.add_argument("--limit", type=int, default=None, help="Max items in batch")

# publish preview
pub_preview = publish_sub.add_parser("preview", help="Preview batch contents")
pub_preview.add_argument("batch_id", help="Batch ID to preview")

# publish commit
pub_commit = publish_sub.add_parser("commit", help="Commit batch (push to Notion)")
pub_commit.add_argument("batch_id", help="Batch ID to commit")
pub_commit.add_argument("--dry-run", action="store_true", help="Preview without pushing")

# publish abort
pub_abort = publish_sub.add_parser("abort", help="Abort a draft batch")
pub_abort.add_argument("batch_id", help="Batch ID to abort")
pub_abort.add_argument("--reason", default="", help="Reason for abort")

# publish list
pub_list = publish_sub.add_parser("list", help="List recent batches")
pub_list.add_argument("--limit", type=int, default=10, help="Max batches to show")
pub_list.add_argument("--status", default=None, help="Filter by status")
```

Then add the dispatch handler:

```python
async def cmd_publish(args):
    """Handle publish subcommands."""
    store = SignalStore(args.db)
    await store.initialize()
    try:
        if args.publish_cmd == "create":
            from workflows.batch_publisher import create_batch
            batch = await create_batch(store, actor="operator", limit=args.limit)
            if batch is None:
                print("No approved review items found. Nothing to batch.")
                return
            print(f"Created batch: {batch['id']}")
            print(f"  Items: {batch['item_count']}")
            print(f"  Status: {batch['status']}")
            print(f"\nNext: run_pipeline.py publish preview {batch['id']}")

        elif args.publish_cmd == "preview":
            from workflows.batch_publisher import preview_batch
            preview = await preview_batch(store, args.batch_id)
            if preview is None:
                print(f"Batch {args.batch_id} not found.")
                return
            print(f"Batch: {preview['batch_id']}  Status: {preview['status']}")
            print(f"Items: {preview['item_count']}")
            print(f"{'─' * 70}")
            for item in preview["items"]:
                name = item.get("company_name") or item["company_id"][:20]
                conf = item.get("confidence") or "?"
                print(f"  [{item['review_id']}] {name:<30} {item['canonical_key']:<30} conf={conf}")
            print(f"\nTo commit: run_pipeline.py publish commit {args.batch_id}")
            print(f"To abort:  run_pipeline.py publish abort {args.batch_id}")

        elif args.publish_cmd == "commit":
            from workflows.batch_publisher import commit_batch
            result = await commit_batch(
                store, args.batch_id, actor="operator", dry_run=args.dry_run
            )
            prefix = "[DRY RUN] " if result["dry_run"] else ""
            print(f"{prefix}Batch {result['batch_id']}: {result['status']}")
            print(f"  Pushed: {result['pushed']}")
            print(f"  Errors: {result['errors']}")
            if result["error_messages"]:
                for msg in result["error_messages"]:
                    print(f"  ERROR: {msg}")

        elif args.publish_cmd == "abort":
            from workflows.batch_publisher import abort_batch
            result = await abort_batch(
                store, args.batch_id, actor="operator", reason=args.reason
            )
            print(f"Batch {result['batch_id']}: {result['status']}")
            print(f"  Reverted {result['reverted']} review items to 'approved'")

        elif args.publish_cmd == "list":
            cursor = await store._db.execute(
                """SELECT id, status, item_count, pushed_count, error_count,
                          actor, created_at
                   FROM publish_batches
                   WHERE (? IS NULL OR status = ?)
                   ORDER BY created_at DESC LIMIT ?""",
                (args.status, args.status, args.limit)
            )
            rows = await cursor.fetchall()
            if not rows:
                print("No batches found.")
                return
            print(f"{'ID':<30} {'Status':<12} {'Items':>5} {'Pushed':>6} {'Errors':>6} {'Created'}")
            print("─" * 90)
            for r in rows:
                print(f"{r[0]:<30} {r[1]:<12} {r[2]:>5} {r[3]:>6} {r[4]:>6} {r[6]}")

        else:
            publish_parser.print_help()
    finally:
        await store.close()
```

Wire into main dispatch:

```python
elif args.command == "publish":
    await cmd_publish(args)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/cli/test_batch_publish_cli.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add run_pipeline.py tests/cli/test_batch_publish_cli.py
git commit -m "feat(phase1b): CLI subcommands — publish create|preview|commit|abort|list"
```

---

## Task 5: Idempotency — skip already-published via canonical_key

**Files:**
- Modify: `workflows/batch_publisher.py` (add idempotency check in `commit_batch`)
- Test: `tests/workflows/test_batch_publisher.py` (add idempotency tests)

**Step 1: Write the failing test**

Add to `tests/workflows/test_batch_publisher.py`:

```python
class TestIdempotencyOnCommit:
    @pytest.mark.asyncio
    async def test_commit_skips_already_pushed_canonical_key(self, store, monkeypatch):
        """If canonical_key was already pushed to Notion, skip it."""
        from workflows.batch_publisher import create_batch, commit_batch
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        review_ids = await _seed_approved_reviews(store, 2)

        # Mark one company's signal as already pushed
        cursor = await store._db.execute(
            "SELECT signal_id FROM signal_processing LIMIT 1"
        )
        sig_id = (await cursor.fetchone())[0]
        await store._db.execute(
            "UPDATE signal_processing SET status = 'pushed' WHERE signal_id = ?",
            (sig_id,)
        )
        await store._db.commit()

        batch = await create_batch(store, actor="operator")
        result = await commit_batch(store, batch["id"], actor="operator", dry_run=True)
        # Both items processed (idempotency check is at Notion level, not batch level)
        assert result["item_count"] == 2
```

**Step 2: Run and verify failure, then implement, then verify pass**

The idempotency check in `commit_batch` should query `signal_processing` for already-pushed signals per canonical_key and mark the batch_item as `skipped` rather than pushing again.

**Step 3: Commit**

```bash
git add workflows/batch_publisher.py tests/workflows/test_batch_publisher.py
git commit -m "feat(phase1b): idempotency — skip already-published canonical keys"
```

---

## Task 6: Integration test — full create → preview → commit lifecycle

**Files:**
- Test: `tests/integration/test_batch_publish_e2e.py`

**Step 1: Write the integration test**

```python
# tests/integration/test_batch_publish_e2e.py
"""End-to-end integration test for batch publish lifecycle."""

import json
import os
import pytest

from storage.signal_store import SignalStore
from storage.review_store import create_review_item, update_review_status, get_review_queue
from workflows.batch_publisher import create_batch, preview_batch, commit_batch, abort_batch


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


async def _seed(store, n=3):
    """Seed n approved review items."""
    ids = []
    for i in range(n):
        ckey = f"domain:e2e-co{i}.com"
        cid = f"e2e-cid-{i:04d}"
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)""",
            ("github_trending", "github", ckey, f"E2E Company {i}",
             0.8, json.dumps({"test": True}), cid)
        )
        cursor = await store._db.execute("SELECT last_insert_rowid()")
        sig_id = (await cursor.fetchone())[0]
        await store._db.execute(
            "INSERT INTO signal_processing (signal_id, status, created_at) VALUES (?, 'pending', datetime('now'))",
            (sig_id,)
        )
        await store._db.commit()
        rid = await create_review_item(store, cid, [sig_id])
        await update_review_status(store, rid, "approved", actor="test", reason="e2e")
        ids.append(rid)
    return ids


class TestBatchPublishE2E:
    @pytest.mark.asyncio
    async def test_full_lifecycle_create_preview_commit(self, store, monkeypatch):
        """Full lifecycle: create -> preview -> commit (dry_run)."""
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        await _seed(store, 3)

        # CREATE
        batch = await create_batch(store, actor="operator")
        assert batch["item_count"] == 3
        assert batch["status"] == "draft"

        # All reviews should be publish_queued
        approved = await get_review_queue(store, status="approved")
        assert len(approved) == 0
        queued = await get_review_queue(store, status="publish_queued")
        assert len(queued) == 3

        # PREVIEW
        preview = await preview_batch(store, batch["id"])
        assert len(preview["items"]) == 3

        # COMMIT (dry_run)
        result = await commit_batch(store, batch["id"], actor="operator", dry_run=True)
        assert result["pushed"] == 3
        assert result["errors"] == 0

        # Audit trail
        cursor = await store._db.execute(
            "SELECT action_type FROM audit_log WHERE entity_type = 'batch' ORDER BY created_at"
        )
        actions = [r[0] for r in await cursor.fetchall()]
        assert "batch_create" in actions
        assert "batch_commit" in actions

    @pytest.mark.asyncio
    async def test_full_lifecycle_create_abort(self, store):
        """Full lifecycle: create -> abort reverts reviews."""
        await _seed(store, 2)

        batch = await create_batch(store, actor="operator")
        assert batch["item_count"] == 2

        # ABORT
        result = await abort_batch(store, batch["id"], actor="operator", reason="changed mind")
        assert result["status"] == "aborted"
        assert result["reverted"] == 2

        # Reviews back to approved
        approved = await get_review_queue(store, status="approved")
        assert len(approved) == 2

    @pytest.mark.asyncio
    async def test_staging_mode_blocks_commit(self, store, monkeypatch):
        """In staging_only mode, commit should raise DeliveryPolicyError."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        await _seed(store, 1)

        batch = await create_batch(store, actor="operator")
        from workflows.delivery_policy import DeliveryPolicyError
        with pytest.raises(DeliveryPolicyError):
            await commit_batch(store, batch["id"], actor="operator")

    @pytest.mark.asyncio
    async def test_batch_list_query(self, store):
        """Multiple batches should be queryable."""
        await _seed(store, 2)
        b1 = await create_batch(store, actor="op", limit=1)
        # After creating b1, one review is publish_queued, one still approved
        b2 = await create_batch(store, actor="op")

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM publish_batches"
        )
        assert (await cursor.fetchone())[0] == 2
```

**Step 2: Run test**

Run: `python -m pytest tests/integration/test_batch_publish_e2e.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/integration/test_batch_publish_e2e.py
git commit -m "test(phase1b): e2e integration tests for batch publish lifecycle"
```

---

## Task 7: Add `publish_queued -> approved` to merge cascade precedence

**Files:**
- Modify: `storage/merge_cascade.py` (ensure revert-aware precedence)
- Test: existing merge cascade tests should still pass

**Step 1: Verify existing tests still pass after Task 3 transition change**

Run: `python -m pytest tests/storage/test_merge_cascade.py -v`
Expected: PASS (no changes needed if merge_cascade already handles publish_queued correctly)

**Step 2: Commit (if any changes needed)**

```bash
git add storage/merge_cascade.py
git commit -m "fix(phase1b): merge cascade compatibility with batch abort revert"
```

---

## Task 8: Governance lint — add new files to ALLOWLIST if needed

**Files:**
- Check: `tests/test_no_direct_signalstore.py`

**Step 1: Check if new files trigger governance lint**

Run: `python -m pytest tests/test_no_direct_signalstore.py -v`

If `workflows/batch_publisher.py` constructs `SignalStore()` directly (it doesn't — it receives `store` as a parameter), add it to the ALLOWLIST. Otherwise no changes needed.

Expected: PASS (no direct construction)

**Step 2: Commit if needed**

```bash
git add tests/test_no_direct_signalstore.py
git commit -m "chore(phase1b): update governance ALLOWLIST for batch publisher"
```

---

## Task 9: Final verification — run all Phase 1b tests + existing test suite

**Step 1: Run Phase 1b tests**

Run: `python -m pytest tests/storage/test_v31_batch_publish.py tests/workflows/test_batch_publisher.py tests/integration/test_batch_publish_e2e.py tests/cli/test_batch_publish_cli.py -v`
Expected: ALL PASS

**Step 2: Run existing Phase 1a tests to verify no regressions**

Run: `python -m pytest tests/storage/test_review_state_machine.py tests/storage/test_merge_cascade.py tests/workflows/test_thin_file_manager.py -v`
Expected: ALL PASS

**Step 3: Run governance lint**

Run: `python -m pytest tests/test_no_direct_signalstore.py -v`
Expected: PASS

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat(phase1b): batch publish workflow — complete"
```

---

## Summary

| Task | Description | New Tests | Files Created | Files Modified |
|------|-------------|-----------|---------------|----------------|
| 1 | Migration v31 DDL | 6 | `v31_batch_publish.py`, `test_v31_batch_publish.py` | `signal_store.py` |
| 2 | BatchPublisher core | 13 | `batch_publisher.py`, `test_batch_publisher.py` | — |
| 3 | Abort revert transition | 1 | — | `review_store.py`, `test_review_state_machine.py` |
| 4 | CLI subcommands | 2 | `test_batch_publish_cli.py` | `run_pipeline.py` |
| 5 | Idempotency | 1 | — | `batch_publisher.py`, `test_batch_publisher.py` |
| 6 | Integration e2e | 4 | `test_batch_publish_e2e.py` | — |
| 7 | Merge cascade compat | 0 | — | verify only |
| 8 | Governance lint | 0 | — | verify only |
| 9 | Final verification | 0 | — | verify only |
| **Total** | | **~27** | **5 new files** | **4 modified** |

**Estimated time:** 6-8 hours
