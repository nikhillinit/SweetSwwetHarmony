"""Reverse cascade for merge rollback (Wave 4 — Phase A).

Restores both winner and loser entities to their pre-merge state
using compensation data stored in merge_proposals.before_snapshot.

Steps (order matters):
1. Delete entity_migrations row (prevents stale resolution chain)
2. Reassign loser's signals back from winner
3. Restore winner's review evidence bundle to pre-merge state
4. Reopen loser's rejected reviews
5. Restore winner's company_files to pre-merge values
6. Recreate loser's company_files from snapshot
7. Emit audit_events entry with correlation_id
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

_SNAPSHOT_SCHEMA_VERSION = 1


class RollbackError(RuntimeError):
    """Raised when rollback cannot proceed."""
    pass


async def reverse_cascade(
    store: "SignalStore",
    proposal: Dict[str, Any],
    actor: str,
    tx: "aiosqlite.Connection",
) -> Dict[str, Any]:
    """Execute reverse cascade to undo a merge.

    Args:
        store: Initialized SignalStore
        proposal: Full merge_proposals row as dict (must include before_snapshot, after_snapshot)
        actor: Who is performing the rollback
        tx: Active transaction connection (caller manages BEGIN/COMMIT)

    Returns:
        Report dict with rollback statistics.

    Raises:
        RollbackError: If snapshot is missing or incompatible.
    """
    before_raw = proposal.get("before_snapshot")
    if not before_raw:
        raise RollbackError("Cannot rollback: before_snapshot is missing")

    try:
        snapshot = json.loads(before_raw) if isinstance(before_raw, str) else before_raw
    except (json.JSONDecodeError, TypeError) as e:
        raise RollbackError(f"Cannot rollback: before_snapshot is corrupt: {e}")

    schema_ver = snapshot.get("snapshot_schema_version")
    if schema_ver != _SNAPSHOT_SCHEMA_VERSION:
        raise RollbackError(
            f"Cannot rollback: snapshot schema version {schema_ver} "
            f"!= expected {_SNAPSHOT_SCHEMA_VERSION}"
        )

    winner_data = snapshot.get("winner", {})
    loser_data = snapshot.get("loser", {})
    migration_data = snapshot.get("entity_migration", {})

    winner_id = winner_data.get("company_id") or proposal.get("winner_company_id")
    loser_id = loser_data.get("company_id") or proposal.get("loser_company_id")

    if not winner_id or not loser_id:
        raise RollbackError("Cannot rollback: winner or loser company_id missing from snapshot")

    now_iso = datetime.now(timezone.utc).isoformat()
    report: Dict[str, Any] = {
        "winner": winner_id,
        "loser": loser_id,
        "proposal_id": proposal.get("id"),
        "signals_restored": 0,
        "reviews_restored": 0,
        "company_files_restored": False,
        "entity_migration_deleted": False,
    }

    # -------------------------------------------------------------------------
    # Step 1: Delete entity_migrations row FIRST
    # -------------------------------------------------------------------------
    if migration_data.get("from_entity_id") and migration_data.get("to_entity_id"):
        cursor = await tx.execute(
            """DELETE FROM entity_migrations
               WHERE from_entity_id = ? AND to_entity_id = ?""",
            (migration_data["from_entity_id"], migration_data["to_entity_id"]),
        )
        if cursor.rowcount > 0:
            report["entity_migration_deleted"] = True
            logger.info(
                "Rollback: deleted entity_migration %s -> %s",
                migration_data["from_entity_id"],
                migration_data["to_entity_id"],
            )

    # -------------------------------------------------------------------------
    # Step 2: Reassign loser's signals back from winner
    # -------------------------------------------------------------------------
    loser_signal_ids = loser_data.get("signal_ids", [])
    if loser_signal_ids:
        placeholders = ",".join("?" for _ in loser_signal_ids)
        cursor = await tx.execute(
            f"""UPDATE signals SET company_id = ?
                WHERE company_id = ? AND id IN ({placeholders})""",
            [loser_id, winner_id] + loser_signal_ids,
        )
        report["signals_restored"] = cursor.rowcount

    # -------------------------------------------------------------------------
    # Step 3: Restore winner's review evidence bundle
    # -------------------------------------------------------------------------
    winner_review_signal_ids = winner_data.get("review_evidence_signal_ids")
    if winner_review_signal_ids is not None:
        # Find winner's active review
        cursor = await tx.execute(
            """SELECT id, evidence_bundle FROM review_items
               WHERE company_id = ? AND status IN ('pending', 'approved', 'publish_queued')
               ORDER BY updated_at DESC LIMIT 1""",
            (winner_id,),
        )
        winner_review = await cursor.fetchone()
        if winner_review:
            review_id = winner_review[0]
            restored_bundle = json.dumps({"signal_ids": sorted(winner_review_signal_ids)})
            await tx.execute(
                """UPDATE review_items SET evidence_bundle = ?, updated_at = ?
                   WHERE id = ?""",
                (restored_bundle, now_iso, review_id),
            )

    # -------------------------------------------------------------------------
    # Step 4: Reopen loser's rejected reviews
    # -------------------------------------------------------------------------
    loser_review_id = loser_data.get("review_id")
    loser_review_status = loser_data.get("review_status", "pending")
    if loser_review_id:
        # Restore the review status and reassign company_id back to loser
        cursor = await tx.execute(
            """UPDATE review_items
               SET status = ?,
                   company_id = ?,
                   reason = NULL,
                   decided_at = NULL,
                   decided_by = NULL,
                   updated_at = ?
               WHERE id = ? AND status = 'rejected'""",
            (loser_review_status, loser_id, now_iso, loser_review_id),
        )
        if cursor.rowcount > 0:
            report["reviews_restored"] += 1
    else:
        # Find reviews rejected due to merge and restore them
        cursor = await tx.execute(
            """SELECT id FROM review_items
               WHERE company_id = ? AND status = 'rejected'
               AND reason LIKE 'merged_into:%'""",
            (winner_id,),
        )
        merge_rejected = await cursor.fetchall()
        for row in merge_rejected:
            await tx.execute(
                """UPDATE review_items
                   SET status = ?, company_id = ?,
                       reason = NULL, decided_at = NULL, decided_by = NULL,
                       updated_at = ?
                   WHERE id = ?""",
                (loser_review_status, loser_id, now_iso, row[0]),
            )
            report["reviews_restored"] += 1

    # -------------------------------------------------------------------------
    # Step 5: Restore winner's company_files to pre-merge values
    # -------------------------------------------------------------------------
    winner_file = winner_data.get("company_file")
    loser_file = loser_data.get("company_file")

    if winner_file:
        source_apis = json.dumps(winner_file.get("source_apis", []))
        first_seen = winner_file.get("first_seen_at")
        last_seen = winner_file.get("last_seen_at")
        if first_seen and last_seen:
            await tx.execute(
                """UPDATE company_files
                   SET source_apis = ?, first_seen_at = ?, last_seen_at = ?
                   WHERE company_id = ?""",
                (source_apis, first_seen, last_seen, winner_id),
            )
            report["company_files_restored"] = True

    # -------------------------------------------------------------------------
    # Step 6: Recreate loser's company_files from snapshot
    # -------------------------------------------------------------------------
    if loser_file:
        source_apis = json.dumps(loser_file.get("source_apis", []))
        first_seen = loser_file.get("first_seen_at")
        last_seen = loser_file.get("last_seen_at")

        # Check if loser's file already exists (shouldn't after merge, but be safe)
        cursor = await tx.execute(
            "SELECT 1 FROM company_files WHERE company_id = ?",
            (loser_id,),
        )
        exists = await cursor.fetchone()

        if exists:
            await tx.execute(
                """UPDATE company_files
                   SET source_apis = ?, first_seen_at = ?, last_seen_at = ?
                   WHERE company_id = ?""",
                (source_apis, first_seen, last_seen, loser_id),
            )
        else:
            # Get company name from signals or snapshot
            cursor = await tx.execute(
                "SELECT company_name, canonical_key FROM signals WHERE company_id = ? LIMIT 1",
                (loser_id,),
            )
            name_row = await cursor.fetchone()
            company_name = name_row[0] if name_row else loser_id
            canonical_key = name_row[1] if name_row else f"company:{loser_id}"

            await tx.execute(
                """INSERT INTO company_files
                   (company_id, company_name, canonical_key, source_apis,
                    first_seen_at, last_seen_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'thin')""",
                (loser_id, company_name, canonical_key, source_apis,
                 first_seen, last_seen),
            )
        report["company_files_restored"] = True

    # -------------------------------------------------------------------------
    # Step 7: Audit event
    # -------------------------------------------------------------------------
    correlation_id = proposal.get("correlation_id")
    details = json.dumps({
        "proposal_id": proposal.get("id"),
        "winner": winner_id,
        "loser": loser_id,
        "signals_restored": report["signals_restored"],
        "reviews_restored": report["reviews_restored"],
        "company_files_restored": report["company_files_restored"],
        "entity_migration_deleted": report["entity_migration_deleted"],
    })

    await tx.execute(
        """INSERT INTO audit_log
           (action_type, entity_type, entity_id, actor, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("cascade_rollback", "company", winner_id, actor, details, now_iso),
    )

    logger.info(
        "Reverse cascade: %s <- %s (proposal %s, %d signals restored)",
        winner_id, loser_id, proposal.get("id"), report["signals_restored"],
    )

    return report


def compute_entity_fingerprint_sync(
    signals: list[int],
    review_state: Optional[Dict[str, Any]],
    file_state: Optional[Dict[str, Any]],
) -> str:
    """Compute a deterministic fingerprint of entity state.

    Used for drift detection during rollback eligibility checks.

    Args:
        signals: Sorted list of signal IDs owned by the entity
        review_state: Dict with status + evidence bundle
        file_state: Dict with source_apis + last_seen_at

    Returns:
        SHA256[:16] hex digest
    """
    import hashlib
    payload = json.dumps({
        "signals": sorted(signals),
        "review": review_state,
        "file": file_state,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def compute_entity_fingerprint(
    tx: "aiosqlite.Connection",
    company_id: str,
) -> str:
    """Compute entity fingerprint from current DB state within a transaction.

    Args:
        tx: Active transaction connection
        company_id: Company to fingerprint

    Returns:
        SHA256[:16] hex digest
    """
    # Signal IDs
    cursor = await tx.execute(
        "SELECT id FROM signals WHERE company_id = ? ORDER BY id",
        (company_id,),
    )
    signal_ids = [row[0] for row in await cursor.fetchall()]

    # Review state
    cursor = await tx.execute(
        """SELECT status, evidence_bundle FROM review_items
           WHERE company_id = ? AND status IN ('pending', 'approved', 'publish_queued')
           ORDER BY id DESC LIMIT 1""",
        (company_id,),
    )
    review_row = await cursor.fetchone()
    review_state = None
    if review_row:
        review_state = {"status": review_row[0], "evidence_bundle": review_row[1]}

    # Company file state
    cursor = await tx.execute(
        "SELECT source_apis, last_seen_at FROM company_files WHERE company_id = ?",
        (company_id,),
    )
    file_row = await cursor.fetchone()
    file_state = None
    if file_row:
        file_state = {"source_apis": file_row[0], "last_seen_at": file_row[1]}

    return compute_entity_fingerprint_sync(signal_ids, review_state, file_state)
