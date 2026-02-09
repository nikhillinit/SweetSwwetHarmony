#!/usr/bin/env python3
"""
Garbage Collection for Archived Company Files (Phase 1a, Task 10).

Purges company_files rows that have been in 'archived' status beyond a
configurable retention period (default 365 days).  Also cleans up orphaned
review_items that reference the purged company_ids -- but only terminal
statuses (rejected, deferred), never active reviews.

Safety invariants:
  - Only rows with status='archived' are ever deleted.
  - Only review_items with status IN ('rejected', 'deferred') are cleaned.
  - Active reviews (pending, approved, publish_queued) are never touched.
  - Dry-run is the default; --apply is required to mutate the database.
  - Each batch deletion is logged to stderr for operational visibility.
  - A single audit_log entry summarises the entire GC run.

Per accepted review item A8: GC cutoff uses archived_at (NOT last_seen_at).

Usage:
    # Dry run (default) -- report what would be deleted
    python scripts/gc_thin_files.py --db signals.db

    # Apply with custom retention
    python scripts/gc_thin_files.py --db signals.db --apply --days 180

    # Smaller batches for large databases
    python scripts/gc_thin_files.py --db signals.db --apply --batch-size 100

Exit codes:
    0 - Success (dry run or apply completed)
    1 - Error (database not found, runtime failure, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.signal_store import SignalStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Maximum number of sample company_ids to include in the report
_MAX_SAMPLE_IDS = 10

# Review statuses that are safe to clean up as orphans.
# Active statuses (pending, approved, publish_queued) are NEVER deleted.
_ORPHAN_REVIEW_STATUSES = ("rejected", "deferred")


async def _find_gc_candidates(
    store: SignalStore,
    cutoff_iso: str,
) -> List[str]:
    """Return company_ids of archived company_files older than the cutoff.

    Only selects rows where status='archived' AND archived_at < cutoff.
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    cursor = await db.execute(
        """SELECT company_id
           FROM company_files
           WHERE status = 'archived'
             AND archived_at IS NOT NULL
             AND archived_at < ?""",
        (cutoff_iso,),
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def _delete_company_files_batch(
    store: SignalStore,
    company_ids: List[str],
) -> int:
    """Delete a batch of archived company_files by company_id.

    Returns the number of rows actually deleted.  The WHERE clause
    re-asserts status='archived' as a defence-in-depth measure.
    """
    if not company_ids:
        return 0

    placeholders = ", ".join("?" for _ in company_ids)

    async with store.transaction_immediate() as tx:
        cursor = await tx.execute(
            f"""DELETE FROM company_files
                WHERE company_id IN ({placeholders})
                  AND status = 'archived'""",
            tuple(company_ids),
        )
        return cursor.rowcount


async def _clean_orphaned_reviews(
    store: SignalStore,
    company_ids: List[str],
) -> int:
    """Delete orphaned review_items for the given company_ids.

    Only deletes reviews in terminal/safe statuses (rejected, deferred).
    Active reviews (pending, approved, publish_queued) are never touched.

    Returns the number of review rows deleted.
    """
    if not company_ids:
        return 0

    id_placeholders = ", ".join("?" for _ in company_ids)
    status_placeholders = ", ".join("?" for _ in _ORPHAN_REVIEW_STATUSES)

    async with store.transaction_immediate() as tx:
        cursor = await tx.execute(
            f"""DELETE FROM review_items
                WHERE company_id IN ({id_placeholders})
                  AND status IN ({status_placeholders})""",
            tuple(company_ids) + _ORPHAN_REVIEW_STATUSES,
        )
        return cursor.rowcount


async def _write_audit_entry(
    store: SignalStore,
    report: dict,
) -> None:
    """Write a single audit_log entry summarising the GC run."""
    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "gc_thin_files",
                "company_file",
                "batch",
                "gc_script",
                json.dumps(report, default=str),
                now_iso,
            ),
        )


async def run_gc(
    db_path: str,
    days: int,
    batch_size: int,
    apply: bool,
) -> dict:
    """Execute the garbage collection workflow.

    Args:
        db_path: Path to the SQLite database.
        days: Retention period in days (archived_at older than this).
        batch_size: Number of company_files to delete per batch.
        apply: If False, dry-run only; if True, actually delete.

    Returns:
        Summary report dict suitable for JSON serialisation.
    """
    store = SignalStore(db_path)
    await store.initialize()

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()

        # --- Discover candidates ---
        candidate_ids = await _find_gc_candidates(store, cutoff_iso)

        report = {
            "mode": "apply" if apply else "dry_run",
            "cutoff_days": days,
            "cutoff_date": cutoff_iso,
            "company_files_found": len(candidate_ids),
            "company_files_deleted": 0,
            "orphaned_reviews_cleaned": 0,
            "sample_company_ids": candidate_ids[:_MAX_SAMPLE_IDS],
        }

        if not candidate_ids:
            logger.info("No archived company_files older than %d days.", days)
            return report

        if not apply:
            logger.info(
                "[DRY RUN] Would delete %d archived company_files "
                "(cutoff: %s).",
                len(candidate_ids),
                cutoff_iso,
            )
            return report

        # --- Apply mode: confirmation banner ---
        logger.warning(
            "APPLY MODE: Will delete %d archived company_files "
            "(cutoff: %s).",
            len(candidate_ids),
            cutoff_iso,
        )

        # --- Delete company_files in batches ---
        total_deleted = 0
        for i in range(0, len(candidate_ids), batch_size):
            batch = candidate_ids[i : i + batch_size]
            deleted = await _delete_company_files_batch(store, batch)
            total_deleted += deleted
            logger.info(
                "Batch %d: deleted %d/%d company_files.",
                (i // batch_size) + 1,
                deleted,
                len(batch),
            )

        report["company_files_deleted"] = total_deleted

        # --- Clean orphaned reviews ---
        orphans_cleaned = await _clean_orphaned_reviews(store, candidate_ids)
        report["orphaned_reviews_cleaned"] = orphans_cleaned
        if orphans_cleaned:
            logger.info(
                "Cleaned %d orphaned review_items (rejected/deferred).",
                orphans_cleaned,
            )

        # --- Audit log ---
        await _write_audit_entry(store, report)
        logger.info("Audit log entry written.")

        return report

    finally:
        await store.close()


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Garbage-collect archived company_files beyond the retention "
            "period.  Dry-run by default; pass --apply to delete."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/gc_thin_files.py --db signals.db\n"
            "  python scripts/gc_thin_files.py --db signals.db --apply --days 180\n"
            "  python scripts/gc_thin_files.py --db signals.db --apply --batch-size 100\n"
        ),
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report what would be deleted without modifying (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually delete records.  Overrides --dry-run.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Retention period: days since archived_at (default: 365).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows to delete per batch (default: 500).",
    )
    return parser


async def async_main() -> None:
    """Async entry point -- parses args and runs GC."""
    # Reconfigure stdout for Windows console safety
    sys.stdout.reconfigure(errors="replace")

    parser = _build_parser()
    args = parser.parse_args()

    # Validate database exists
    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("Database not found: %s", args.db)
        sys.exit(1)

    # Validate numeric arguments
    if args.days < 1:
        logger.error("--days must be >= 1 (got %d).", args.days)
        sys.exit(1)
    if args.batch_size < 1:
        logger.error("--batch-size must be >= 1 (got %d).", args.batch_size)
        sys.exit(1)

    # --apply overrides the default --dry-run
    apply = args.apply

    try:
        report = await run_gc(
            db_path=args.db,
            days=args.days,
            batch_size=args.batch_size,
            apply=apply,
        )
    except Exception:
        logger.exception("GC run failed.")
        sys.exit(1)

    # Emit JSON report to stdout
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(async_main())
