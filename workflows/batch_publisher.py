"""Batch Publish Workflow — git-style create → preview → commit/abort.

Provides:
- create_batch: Atomically claim approved reviews into a new batch
- preview_batch: Deterministic preview of batch contents
- commit_batch: Push batch items to Notion (with dry-run support)
- abort_batch: Revert batch and release reviews back to approved
- list_batches: List recent batches with status filter

Batch lifecycle:
    draft -> committing -> committed | committed_with_errors
    draft -> aborted

Item lifecycle:
    pending -> in_progress -> pushed | error
    pending -> skipped (if claim fails)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from storage.review_store import VALID_TRANSITIONS, update_review_status
from workflows.delivery_policy import (
    assert_notion_write_allowed,
    DeliveryIntent,
    DeliveryPolicyError,
)

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from workflows.notion_pusher import NotionPusher

logger = logging.getLogger(__name__)


# =============================================================================
# EXCEPTIONS
# =============================================================================

class BatchError(RuntimeError):
    """Base class for batch publish errors."""
    pass


class BatchNotFoundError(BatchError):
    """Raised when a batch ID does not exist."""
    pass


class BatchStateError(BatchError):
    """Raised when a batch operation is invalid for the current state."""
    pass


class ActivationGateError(Exception):
    """Raised when commit is attempted with a non-ready activation gate and no override."""
    pass


# =============================================================================
# BATCH ID GENERATION
# =============================================================================

def _generate_batch_id() -> str:
    """Generate a collision-proof batch ID: batch-YYYYMMDD-HHMMSS-<6hex>."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)  # 6 hex chars
    return f"batch-{ts}-{suffix}"


# =============================================================================
# CREATE BATCH
# =============================================================================

async def create_batch(
    store: SignalStore,
    limit: int = 50,
    actor: str = "operator",
) -> Dict[str, Any]:
    """Atomically create a batch from approved reviews.

    Within a single IMMEDIATE transaction:
    1. SELECT approved reviews (up to limit)
    2. INSERT publish_batches row
    3. For each review: validate transition, claim via conditional UPDATE,
       INSERT batch_items row
    4. INSERT audit_log entry

    Args:
        store: Initialized SignalStore
        limit: Max reviews to include
        actor: Who initiated

    Returns:
        Dict with batch_id, item_count, items (list of company_id/canonical_key)

    Raises:
        BatchError: If no approved reviews found
    """
    batch_id = _generate_batch_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    items_created = []

    async with store.transaction_immediate() as tx:
        # 1. Find approved reviews with their company info
        cursor = await tx.execute(
            """SELECT ri.id, ri.company_id,
                      COALESCE(
                          (SELECT cf.canonical_key FROM company_files cf
                           WHERE cf.company_id = ri.company_id LIMIT 1),
                          'unknown:' || ri.company_id
                      ) AS canonical_key
               FROM review_items ri
               WHERE ri.status = 'approved'
               ORDER BY ri.updated_at ASC
               LIMIT ?""",
            (limit,),
        )
        approved_reviews = await cursor.fetchall()

        if not approved_reviews:
            raise BatchError("No approved reviews available for batching")

        # 2. Insert batch header
        await tx.execute(
            """INSERT INTO publish_batches
               (id, status, item_count, actor, created_at)
               VALUES (?, 'draft', ?, ?, ?)""",
            (batch_id, len(approved_reviews), actor, now_iso),
        )

        # 3. For each review: validate + claim + insert batch_item
        for row in approved_reviews:
            review_id, company_id, canonical_key = row[0], row[1], row[2]

            # Validate transition in Python (approved -> publish_queued)
            allowed = VALID_TRANSITIONS.get("approved", [])
            if "publish_queued" not in allowed:
                raise BatchError(
                    f"publish_queued not in allowed transitions from approved: {allowed}"
                )

            # Claim via conditional UPDATE (merge_cascade pattern)
            cursor = await tx.execute(
                """UPDATE review_items
                   SET status = 'publish_queued', updated_at = ?
                   WHERE id = ? AND status = 'approved'""",
                (now_iso, review_id),
            )
            if cursor.rowcount != 1:
                logger.warning(
                    "Review %d: claim failed (status changed concurrently), skipping",
                    review_id,
                )
                continue

            # Insert batch_item
            await tx.execute(
                """INSERT INTO batch_items
                   (batch_id, review_id, company_id, canonical_key, status, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (batch_id, review_id, company_id, canonical_key, now_iso),
            )
            items_created.append({
                "review_id": review_id,
                "company_id": company_id,
                "canonical_key": canonical_key,
            })

        # Update actual item count (some may have been skipped)
        await tx.execute(
            "UPDATE publish_batches SET item_count = ? WHERE id = ?",
            (len(items_created), batch_id),
        )

        # 4. Audit log
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "batch_create",
                "publish_batch",
                batch_id,
                actor,
                json.dumps({
                    "item_count": len(items_created),
                    "review_ids": [item["review_id"] for item in items_created],
                }),
                now_iso,
            ),
        )

    logger.info("Created batch %s with %d items", batch_id, len(items_created))
    return {
        "batch_id": batch_id,
        "item_count": len(items_created),
        "items": items_created,
    }


# =============================================================================
# PREVIEW BATCH
# =============================================================================

async def preview_batch(
    store: SignalStore,
    batch_id: str,
) -> Dict[str, Any]:
    """Deterministic preview of batch contents. Read-only, no mutations.

    Uses correlated subqueries for deterministic metadata per item.

    Args:
        store: Initialized SignalStore
        batch_id: Batch identifier

    Returns:
        Dict with batch_id, status, item_count, items list

    Raises:
        BatchNotFoundError: If batch_id doesn't exist
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    # Fetch batch header
    cursor = await db.execute(
        """SELECT id, status, item_count, pushed_count, error_count,
                  actor, created_at, committed_at
           FROM publish_batches WHERE id = ?""",
        (batch_id,),
    )
    batch_row = await cursor.fetchone()
    if not batch_row:
        raise BatchNotFoundError(f"Batch {batch_id} not found")

    # Fetch items with deterministic metadata
    cursor = await db.execute(
        """SELECT bi.id, bi.review_id, bi.company_id, bi.canonical_key, bi.status,
                  bi.notion_page_id, bi.error_message,
                  (SELECT s.company_name FROM signals s
                   WHERE s.company_id = bi.company_id
                   ORDER BY s.confidence DESC LIMIT 1) AS company_name,
                  (SELECT MAX(s.confidence) FROM signals s
                   WHERE s.company_id = bi.company_id) AS confidence
           FROM batch_items bi
           WHERE bi.batch_id = ?
           ORDER BY bi.id""",
        (batch_id,),
    )
    item_rows = await cursor.fetchall()

    items = []
    for row in item_rows:
        items.append({
            "id": row[0],
            "review_id": row[1],
            "company_id": row[2],
            "canonical_key": row[3],
            "status": row[4],
            "notion_page_id": row[5],
            "error_message": row[6],
            "company_name": row[7],
            "confidence": row[8],
        })

    return {
        "batch_id": batch_row[0],
        "status": batch_row[1],
        "item_count": batch_row[2],
        "pushed_count": batch_row[3],
        "error_count": batch_row[4],
        "actor": batch_row[5],
        "created_at": batch_row[6],
        "committed_at": batch_row[7],
        "items": items,
    }


# =============================================================================
# COMMIT BATCH
# =============================================================================

async def commit_batch(
    store: SignalStore,
    batch_id: str,
    pusher: Optional[NotionPusher] = None,
    dry_run: bool = False,
    actor: str = "operator",
    override_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Commit a batch: push items to Notion.

    Dry-run mode: reads batch_items and reports counts with zero mutations.
    Real mode: claims each item, pushes to Notion, updates statuses.

    Args:
        store: Initialized SignalStore
        batch_id: Batch identifier
        pusher: NotionPusher instance (required for real commits)
        dry_run: If True, report only — no mutations
        actor: Who initiated
        override_reason: If provided, overrides a non-ready activation gate with audit trail

    Returns:
        Dict with batch_id, pushed_count, error_count, dry_run flag, items

    Raises:
        BatchNotFoundError: If batch doesn't exist
        BatchStateError: If batch is not in draft status
        DeliveryPolicyError: If delivery mode blocks batch push
        ActivationGateError: If gate is non-ready and no override_reason provided
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    # Verify batch exists and is in draft
    cursor = await db.execute(
        "SELECT status, item_count FROM publish_batches WHERE id = ?",
        (batch_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise BatchNotFoundError(f"Batch {batch_id} not found")
    if row[0] != "draft":
        raise BatchStateError(
            f"Batch {batch_id} is in '{row[0]}' status, expected 'draft'"
        )

    # --- DRY RUN ---
    if dry_run:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM batch_items WHERE batch_id = ? AND status = 'pending'",
            (batch_id,),
        )
        pending_count = (await cursor.fetchone())[0]

        # Audit dry-run
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "batch_commit_dry_run",
                "publish_batch",
                batch_id,
                actor,
                json.dumps({"pending_count": pending_count}),
                now_iso,
            ),
        )
        await db.commit()

        return {
            "batch_id": batch_id,
            "dry_run": True,
            "pending_count": pending_count,
            "pushed_count": 0,
            "error_count": 0,
        }

    # --- REAL COMMIT ---
    # Check delivery policy upfront
    assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)

    # Hard activation gate (non-dry-run real commits only)
    gate_metadata = None
    if pusher is not None:
        try:
            from monitoring.activation_gate import check_activation_readiness

            gate_result = await asyncio.wait_for(
                check_activation_readiness(store, step=4),
                timeout=2.0,
            )
            gate_metadata = gate_result.to_dict()
        except asyncio.TimeoutError:
            gate_metadata = {"verdict": "timeout"}
        except Exception:
            gate_metadata = {"verdict": "error"}

        if gate_metadata.get("verdict") != "ready":
            if not override_reason:
                raise ActivationGateError(
                    f"Activation gate verdict={gate_metadata['verdict']}. "
                    f"Pass override_reason to proceed."
                )
            # Persist override to audit_events
            try:
                from storage.audit_events import record_event

                await record_event(
                    store,
                    action_type="batch_commit_gate_override",
                    entity_type="batch",
                    entity_id=batch_id,
                    actor_id=actor,
                    reason=override_reason,
                    metadata={
                        "verdict": gate_metadata["verdict"],
                        "gate_details": gate_metadata,
                    },
                )
            except Exception:
                logger.debug("Failed to record gate override audit event")

    if pusher is None:
        raise BatchError("NotionPusher required for real commits (not dry-run)")

    now_iso = datetime.now(timezone.utc).isoformat()

    # Set batch to committing
    await db.execute(
        "UPDATE publish_batches SET status = 'committing' WHERE id = ?",
        (batch_id,),
    )
    await db.commit()

    # Fetch pending items
    cursor = await db.execute(
        """SELECT id, review_id, company_id, canonical_key
           FROM batch_items
           WHERE batch_id = ? AND status = 'pending'
           ORDER BY id""",
        (batch_id,),
    )
    pending_items = await cursor.fetchall()

    pushed_count = 0
    error_count = 0
    results = []

    for item_row in pending_items:
        item_id, review_id, company_id, canonical_key = item_row

        # Claim item via conditional UPDATE
        cursor = await db.execute(
            "UPDATE batch_items SET status = 'in_progress' WHERE id = ? AND status = 'pending'",
            (item_id,),
        )
        await db.commit()
        if cursor.rowcount != 1:
            results.append({"item_id": item_id, "status": "skipped"})
            continue

        # Push to Notion
        try:
            push_result = await pusher.process_single_prospect(
                canonical_key, intent=DeliveryIntent.BATCH_PUSH,
                override_hold=True,
            )

            if push_result.pushed and push_result.notion_page_id:
                # Success
                await db.execute(
                    """UPDATE batch_items
                       SET status = 'pushed', notion_page_id = ?
                       WHERE id = ?""",
                    (push_result.notion_page_id, item_id),
                )
                await db.commit()

                # Transition review to published (outside batch tx — safe)
                try:
                    await update_review_status(
                        store, review_id, "published",
                        actor=actor, reason=f"batch:{batch_id}"
                    )
                except Exception as e:
                    logger.warning(
                        "Review %d: status transition to published failed: %s",
                        review_id, e,
                    )

                pushed_count += 1
                results.append({
                    "item_id": item_id,
                    "status": "pushed",
                    "notion_page_id": push_result.notion_page_id,
                })
            else:
                # Push returned but no page created (rejected by confidence, etc.)
                error_msg = push_result.error or f"Not pushed: {push_result.decision.value}"
                await db.execute(
                    "UPDATE batch_items SET status = 'error', error_message = ? WHERE id = ?",
                    (error_msg, item_id),
                )
                await db.commit()
                error_count += 1
                results.append({
                    "item_id": item_id,
                    "status": "error",
                    "error": error_msg,
                })

        except Exception as e:
            error_msg = str(e)[:500]
            await db.execute(
                "UPDATE batch_items SET status = 'error', error_message = ? WHERE id = ?",
                (error_msg, item_id),
            )
            await db.commit()
            error_count += 1
            results.append({
                "item_id": item_id,
                "status": "error",
                "error": error_msg,
            })
            logger.error("Batch item %d push failed: %s", item_id, e)

    # Finalize batch status
    commit_time = datetime.now(timezone.utc).isoformat()
    final_status = "committed" if error_count == 0 else "committed_with_errors"
    await db.execute(
        """UPDATE publish_batches
           SET status = ?, pushed_count = ?, error_count = ?,
               committed_at = ?
           WHERE id = ?""",
        (final_status, pushed_count, error_count, commit_time, batch_id),
    )
    await db.commit()

    # Audit log
    await db.execute(
        """INSERT INTO audit_log
           (action_type, entity_type, entity_id, actor, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "batch_commit",
            "publish_batch",
            batch_id,
            actor,
            json.dumps({
                "pushed_count": pushed_count,
                "error_count": error_count,
                "final_status": final_status,
            }),
            commit_time,
        ),
    )
    await db.commit()

    logger.info(
        "Batch %s committed: %d pushed, %d errors → %s",
        batch_id, pushed_count, error_count, final_status,
    )

    result = {
        "batch_id": batch_id,
        "dry_run": False,
        "pushed_count": pushed_count,
        "error_count": error_count,
        "final_status": final_status,
        "items": results,
    }
    if gate_metadata is not None:
        result["activation_gate"] = gate_metadata
    return result


# =============================================================================
# ABORT BATCH
# =============================================================================

async def abort_batch(
    store: SignalStore,
    batch_id: str,
    reason: str = "",
    actor: str = "operator",
) -> Dict[str, Any]:
    """Abort a draft batch and revert reviews to approved.

    Guarded: refuses abort if any items already pushed to Notion.

    Args:
        store: Initialized SignalStore
        batch_id: Batch identifier
        reason: Why aborting
        actor: Who initiated

    Returns:
        Dict with batch_id, reverted_count

    Raises:
        BatchNotFoundError: If batch doesn't exist
        BatchStateError: If batch not in draft status or has pushed items
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    reverted_count = 0

    async with store.transaction_immediate() as tx:
        # Verify batch exists and is draft
        cursor = await tx.execute(
            "SELECT status FROM publish_batches WHERE id = ?",
            (batch_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise BatchNotFoundError(f"Batch {batch_id} not found")
        if row[0] != "draft":
            raise BatchStateError(
                f"Batch {batch_id} is in '{row[0]}' status, can only abort 'draft'"
            )

        # Guard: refuse if any items already pushed
        cursor = await tx.execute(
            "SELECT COUNT(*) FROM batch_items WHERE batch_id = ? AND status = 'pushed'",
            (batch_id,),
        )
        pushed_count = (await cursor.fetchone())[0]
        if pushed_count > 0:
            raise BatchStateError(
                f"Cannot abort batch {batch_id}: {pushed_count} items already pushed to Notion"
            )

        # Abort the batch
        await tx.execute(
            """UPDATE publish_batches
               SET status = 'aborted', details = ?
               WHERE id = ?""",
            (json.dumps({"reason": reason}), batch_id),
        )

        # Revert each review: publish_queued -> approved
        cursor = await tx.execute(
            "SELECT review_id FROM batch_items WHERE batch_id = ?",
            (batch_id,),
        )
        review_rows = await cursor.fetchall()

        for (review_id,) in review_rows:
            # Validate transition in Python
            allowed = VALID_TRANSITIONS.get("publish_queued", [])
            if "approved" not in allowed:
                raise BatchError(
                    f"approved not in allowed transitions from publish_queued: {allowed}"
                )

            cursor = await tx.execute(
                """UPDATE review_items
                   SET status = 'approved', updated_at = ?
                   WHERE id = ? AND status = 'publish_queued'""",
                (now_iso, review_id),
            )
            if cursor.rowcount == 1:
                reverted_count += 1

        # Audit log
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "batch_abort",
                "publish_batch",
                batch_id,
                actor,
                json.dumps({
                    "reason": reason,
                    "reverted_count": reverted_count,
                }),
                now_iso,
            ),
        )

    logger.info(
        "Batch %s aborted: %d reviews reverted to approved",
        batch_id, reverted_count,
    )
    return {
        "batch_id": batch_id,
        "reverted_count": reverted_count,
    }


# =============================================================================
# LIST BATCHES
# =============================================================================

async def list_batches(
    store: SignalStore,
    status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List recent batches, optionally filtered by status.

    Args:
        store: Initialized SignalStore
        status: Filter by batch status (None = all)
        limit: Max results

    Returns:
        List of batch dicts
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    if status:
        cursor = await db.execute(
            """SELECT id, status, item_count, pushed_count, error_count,
                      actor, created_at, committed_at
               FROM publish_batches
               WHERE status = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (status, limit),
        )
    else:
        cursor = await db.execute(
            """SELECT id, status, item_count, pushed_count, error_count,
                      actor, created_at, committed_at
               FROM publish_batches
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )

    rows = await cursor.fetchall()
    return [
        {
            "batch_id": row[0],
            "status": row[1],
            "item_count": row[2],
            "pushed_count": row[3],
            "error_count": row[4],
            "actor": row[5],
            "created_at": row[6],
            "committed_at": row[7],
        }
        for row in rows
    ]
