"""Cascade merge for company identity (Task 5).

When two company entities merge, all dependent rows must be reassigned:
- signals.company_id
- company_files (merge metadata, delete loser)
- review_items (handle UNIQUE collision, evidence merge)
- audit_log entry

Uses explicit-column SELECTs + tuple unpacking throughout (no row_factory).
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


# Review status precedence for collision resolution (higher = wins)
_STATUS_PRECEDENCE = {
    "publish_queued": 3,
    "approved": 2,
    "pending": 1,
}


def _pick_primary_review(
    reviews: list[Dict[str, Any]],
    winner_company_id: str,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Pick the primary review from a list of active reviews.

    Precedence: publish_queued > approved > pending, then newest updated_at,
    then prefer the winner's review as tiebreaker.

    Returns:
        (primary, non_primaries)
    """
    sorted_reviews = sorted(
        reviews,
        key=lambda r: (
            _STATUS_PRECEDENCE.get(r["status"], 0),
            r["updated_at"],
            1 if r["company_id"] == winner_company_id else 0,
        ),
        reverse=True,
    )
    return sorted_reviews[0], sorted_reviews[1:]


async def cascade_merge(
    store: SignalStore,
    winner_company_id: str,
    loser_company_id: str,
    reason: str,
    actor: str,
    tx: Optional[aiosqlite.Connection] = None,
) -> Dict[str, Any]:
    """Merge loser company into winner, cascading through all dependent tables.

    If tx is provided, operates within that transaction.
    Otherwise opens a new transaction_immediate().

    Args:
        store: Initialized SignalStore
        winner_company_id: The surviving company ID
        loser_company_id: The company ID being absorbed
        reason: Why the merge is happening
        actor: Who initiated (user or system)
        tx: Optional existing transaction connection

    Returns:
        Report dict with signals_reassigned, reviews_merged, etc.
    """
    if tx is not None:
        return await _cascade_merge_inner(
            store, winner_company_id, loser_company_id, reason, actor, tx
        )
    else:
        async with store.transaction_immediate() as conn:
            return await _cascade_merge_inner(
                store, winner_company_id, loser_company_id, reason, actor, conn
            )


async def _cascade_merge_inner(
    store: SignalStore,
    winner_company_id: str,
    loser_company_id: str,
    reason: str,
    actor: str,
    tx: aiosqlite.Connection,
) -> Dict[str, Any]:
    """Inner merge implementation — always runs inside a transaction."""
    now_iso = datetime.now(timezone.utc).isoformat()
    report: Dict[str, Any] = {
        "winner": winner_company_id,
        "loser": loser_company_id,
        "reason": reason,
        "signals_reassigned": 0,
        "reviews_merged": False,
        "company_file_merged": False,
    }

    # -------------------------------------------------------------------------
    # Step 1: Resolve review_items UNIQUE collision
    # -------------------------------------------------------------------------
    cursor = await tx.execute(
        """SELECT id, company_id, status, evidence_bundle, updated_at
           FROM review_items
           WHERE company_id IN (?, ?)
           AND status IN ('pending', 'approved', 'publish_queued')""",
        (winner_company_id, loser_company_id),
    )
    active_reviews = [
        {
            "id": row[0],
            "company_id": row[1],
            "status": row[2],
            "evidence_bundle": row[3],
            "updated_at": row[4],
        }
        for row in await cursor.fetchall()
    ]

    if len(active_reviews) >= 2:
        # Both have active reviews — collision resolution
        primary, non_primaries = _pick_primary_review(
            active_reviews, winner_company_id
        )

        # Merge evidence from non-primaries into primary
        primary_bundle = json.loads(primary["evidence_bundle"])
        for npr in non_primaries:
            loser_bundle = json.loads(npr["evidence_bundle"])
            merged_ids = sorted(
                set(primary_bundle["signal_ids"]) | set(loser_bundle["signal_ids"])
            )
            primary_bundle["signal_ids"] = merged_ids

            # Reject non-primary review
            await tx.execute(
                """UPDATE review_items
                   SET status = 'rejected',
                       reason = ?,
                       updated_at = ?,
                       decided_at = ?,
                       decided_by = ?
                   WHERE id = ?""",
                (
                    f"merged_into:{primary['id']}",
                    now_iso,
                    now_iso,
                    actor,
                    npr["id"],
                ),
            )

        # Update primary with merged evidence
        await tx.execute(
            """UPDATE review_items
               SET evidence_bundle = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(primary_bundle), now_iso, primary["id"]),
        )
        report["reviews_merged"] = True

    # Reassign all loser review_items to winner
    await tx.execute(
        """UPDATE review_items
           SET company_id = ?
           WHERE company_id = ?""",
        (winner_company_id, loser_company_id),
    )

    # -------------------------------------------------------------------------
    # Step 2: Reassign signals
    # -------------------------------------------------------------------------
    cursor = await tx.execute(
        """UPDATE signals SET company_id = ? WHERE company_id = ?""",
        (winner_company_id, loser_company_id),
    )
    report["signals_reassigned"] = cursor.rowcount

    # -------------------------------------------------------------------------
    # Step 3: Merge company_files
    # -------------------------------------------------------------------------
    cursor = await tx.execute(
        """SELECT company_id, company_name, canonical_key, source_apis,
                  first_seen_at, last_seen_at
           FROM company_files
           WHERE company_id = ?""",
        (winner_company_id,),
    )
    winner_file = await cursor.fetchone()

    cursor = await tx.execute(
        """SELECT company_id, company_name, canonical_key, source_apis,
                  first_seen_at, last_seen_at
           FROM company_files
           WHERE company_id = ?""",
        (loser_company_id,),
    )
    loser_file = await cursor.fetchone()

    if winner_file and loser_file:
        # Both exist — merge metadata
        w_sources = _safe_parse_json_list(winner_file[3])
        l_sources = _safe_parse_json_list(loser_file[3])
        combined_sources = sorted(list(set(w_sources + l_sources)))

        earliest = min(winner_file[4], loser_file[4])
        latest = max(winner_file[5], loser_file[5])

        await tx.execute(
            """UPDATE company_files
               SET source_apis = ?, first_seen_at = ?, last_seen_at = ?
               WHERE company_id = ?""",
            (
                json.dumps(combined_sources),
                earliest,
                latest,
                winner_company_id,
            ),
        )
        await tx.execute(
            "DELETE FROM company_files WHERE company_id = ?",
            (loser_company_id,),
        )
        report["company_file_merged"] = True

    elif loser_file and not winner_file:
        # Only loser has a file — reassign it
        await tx.execute(
            """UPDATE company_files
               SET company_id = ?
               WHERE company_id = ?""",
            (winner_company_id, loser_company_id),
        )
        report["company_file_merged"] = True

    # If only winner has a file (or neither), nothing to do for company_files.

    # -------------------------------------------------------------------------
    # Step 4: Audit log
    # -------------------------------------------------------------------------
    details = json.dumps({
        "winner": winner_company_id,
        "loser": loser_company_id,
        "reason": reason,
        "signals_reassigned": report["signals_reassigned"],
        "reviews_merged": report["reviews_merged"],
        "company_file_merged": report["company_file_merged"],
    })

    await tx.execute(
        """INSERT INTO audit_log
           (action_type, entity_type, entity_id, actor, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "cascade_merge",
            "company",
            winner_company_id,
            actor,
            details,
            now_iso,
        ),
    )

    logger.info(
        f"Cascade merge: {loser_company_id} -> {winner_company_id} "
        f"({report['signals_reassigned']} signals, "
        f"reviews_merged={report['reviews_merged']})"
    )

    return report


def _safe_parse_json_list(val: Optional[str]) -> list:
    """Parse a JSON string as a list, returning [] on failure."""
    if not val:
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, str) and s]
        return []
    except (json.JSONDecodeError, TypeError):
        return []
