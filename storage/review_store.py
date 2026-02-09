"""ReviewItem state machine store (Task 7).

Provides:
- create_review_item: Create a pending review with one-active-per-company enforcement
- update_review_status: Transition with validation + audit trail
- get_review_queue: Query reviews with optional status filter

State transitions:
    pending -> approved, rejected, deferred
    approved -> published, publish_queued
    publish_queued -> published, rejected (emergency halt), approved (batch abort revert)
    deferred -> pending (reopen)
    rejected, published -> terminal (no outbound)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


class InvalidStateTransition(Exception):
    """Raised when an invalid review state transition is attempted."""
    pass


# Valid outbound transitions per status
VALID_TRANSITIONS: Dict[str, List[str]] = {
    "pending": ["approved", "rejected", "deferred"],
    "approved": ["published", "publish_queued"],
    "publish_queued": ["published", "rejected", "approved"],  # rejected = emergency halt, approved = batch abort revert
    "deferred": ["pending"],  # reopen
    "rejected": [],  # terminal
    "published": [],  # terminal
}

# Statuses that count as "decided" (set decided_at/decided_by)
_DECISION_STATUSES = {"approved", "rejected", "deferred", "published"}


async def create_review_item(
    store: SignalStore,
    company_id: str,
    evidence_signal_ids: List[int],
) -> int:
    """Create a pending review item with one-active-per-company enforcement.

    Uses INSERT ON CONFLICT DO NOTHING + rowcount check per the plan (A9).
    If an active review already exists, returns its ID instead.

    Args:
        store: Initialized SignalStore
        company_id: Company identifier
        evidence_signal_ids: List of signal IDs as evidence

    Returns:
        Review item ID (new or existing active)
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    now_iso = datetime.now(timezone.utc).isoformat()
    evidence_bundle = json.dumps({
        "signal_ids": sorted(evidence_signal_ids),
        "schema_version": 1,
    })

    async with store.transaction_immediate() as tx:
        cursor = await tx.execute(
            """INSERT INTO review_items
               (company_id, status, evidence_bundle, created_at, updated_at)
               VALUES (?, 'pending', ?, ?, ?)
               ON CONFLICT(company_id)
               WHERE status IN ('pending', 'approved', 'publish_queued')
               DO NOTHING""",
            (company_id, evidence_bundle, now_iso, now_iso)
        )

        if cursor.rowcount == 0:
            # Active review already exists — return existing ID
            cursor = await tx.execute(
                """SELECT id FROM review_items
                   WHERE company_id = ?
                   AND status IN ('pending', 'approved', 'publish_queued')""",
                (company_id,)
            )
            row = await cursor.fetchone()
            return row[0]

        return cursor.lastrowid


async def update_review_status(
    store: SignalStore,
    review_id: int,
    new_status: str,
    actor: str,
    reason: Optional[str] = None,
) -> None:
    """Transition a review item to a new status with validation + audit.

    Args:
        store: Initialized SignalStore
        review_id: Review item ID
        new_status: Target status
        actor: Who initiated (user or system)
        reason: Why the transition

    Raises:
        InvalidStateTransition: If transition is not allowed
        ValueError: If review_id not found
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        # Fetch current status
        cursor = await tx.execute(
            "SELECT status FROM review_items WHERE id = ?",
            (review_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Review item {review_id} not found")

        current_status = row[0]

        # Validate transition
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise InvalidStateTransition(
                f"Cannot transition review {review_id} from "
                f"'{current_status}' to '{new_status}'. "
                f"Allowed: {allowed}"
            )

        # Build UPDATE
        update_fields = ["status = ?", "updated_at = ?"]
        update_values: list = [new_status, now_iso]

        if new_status in _DECISION_STATUSES:
            update_fields.extend(["decided_at = ?", "decided_by = ?"])
            update_values.extend([now_iso, actor])

        if reason:
            update_fields.append("reason = ?")
            update_values.append(reason)

        update_values.append(review_id)

        await tx.execute(
            f"UPDATE review_items SET {', '.join(update_fields)} WHERE id = ?",
            tuple(update_values)
        )

        # Audit log entry
        details = json.dumps({
            "before": {"status": current_status},
            "after": {"status": new_status},
            "reason": reason,
        })
        now_audit = datetime.now(timezone.utc).isoformat()

        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "status_transition",
                "review_item",
                str(review_id),
                actor,
                details,
                now_audit,
            )
        )

    logger.debug(
        f"Review {review_id}: {current_status} -> {new_status} "
        f"by {actor} ({reason})"
    )


async def get_review_queue(
    store: SignalStore,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Query review items, optionally filtering by status.

    Args:
        store: Initialized SignalStore
        status: Filter by status (None = all)
        limit: Max results

    Returns:
        List of review dicts with id, company_id, status, evidence_bundle,
        reason, created_at, updated_at, decided_at, decided_by.
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    if status:
        cursor = await db.execute(
            """SELECT id, company_id, status, evidence_bundle, reason,
                      created_at, updated_at, decided_at, decided_by
               FROM review_items
               WHERE status = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (status, limit)
        )
    else:
        cursor = await db.execute(
            """SELECT id, company_id, status, evidence_bundle, reason,
                      created_at, updated_at, decided_at, decided_by
               FROM review_items
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,)
        )

    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "company_id": row[1],
            "status": row[2],
            "evidence_bundle": json.loads(row[3]) if row[3] else None,
            "reason": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "decided_at": row[7],
            "decided_by": row[8],
        }
        for row in rows
    ]
