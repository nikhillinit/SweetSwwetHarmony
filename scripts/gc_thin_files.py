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
from typing import Any, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.signal_store import SignalStore
from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TOOL_NAME = "gc_thin_files"
LOCK_TIMEOUT_SECONDS = 5

# Maximum number of sample company_ids to include in the report
_MAX_SAMPLE_IDS = 10

# Review statuses that are safe to clean up as orphans.
# Active statuses (pending, approved, publish_queued) are NEVER deleted.
_ORPHAN_REVIEW_STATUSES = ("rejected", "deferred")


class GcThinFilesError(DBToolError):
    """GC failure carrying partial evidence for DB ops ledger rows."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        preflight_data_version: int | None,
        apply: bool,
        transaction: str,
        cutoff_days: int | None = None,
        batch_size: int | None = None,
        company_files_found: int | None = None,
        company_files_deleted: int | None = None,
        orphaned_reviews_cleaned: int | None = None,
        audit_log_written: bool | None = None,
        current_batch_index: int | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "preflight_data_version": preflight_data_version,
                "apply": apply,
                "transaction": transaction,
                "cutoff_days": cutoff_days,
                "batch_size": batch_size,
                "company_files_found": company_files_found,
                "company_files_deleted": company_files_deleted,
                "orphaned_reviews_cleaned": orphaned_reviews_cleaned,
                "audit_log_written": audit_log_written,
                "current_batch_index": current_batch_index,
            },
        )


def _append_ledger(*, db_path: str, status: str, details: dict[str, Any]) -> None:
    append_db_ops_ledger(
        tool_name=TOOL_NAME,
        db_path=db_path,
        action=TOOL_NAME,
        status=status,
        details=details,
    )


def _write_report_if_requested(
    *,
    report_path: str | None,
    db_path: str,
    started_at: datetime,
    ok: bool,
    metrics: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> None:
    if not report_path:
        return
    report = create_report(
        command=TOOL_NAME,
        ok=ok,
        db_path=db_path,
        started_at=started_at,
        metrics=metrics or {},
        errors=errors or [],
    )
    write_report(report, report_path)


def _transaction_state_for_failure(report: dict[str, Any], phase: str) -> str:
    if report.get("company_files_deleted") or report.get("orphaned_reviews_cleaned"):
        return "partial_committed"
    if phase in {"delete_company_files", "clean_orphaned_reviews", "write_audit_log"}:
        return "rolled_back"
    return "not_started"


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
    *,
    preflight_data_version: int | None = None,
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
    phase = "preflight_data_version"
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "dry_run": not apply,
        "cutoff_days": days,
        "cutoff_date": None,
        "batch_size": batch_size,
        "company_files_found": 0,
        "company_files_deleted": 0,
        "orphaned_reviews_cleaned": 0,
        "sample_company_ids": [],
        "preflight_data_version": preflight_data_version,
        "audit_log_written": False,
        "transaction": "not_started",
    }
    current_batch_index: int | None = None

    try:
        if preflight_data_version is None:
            preflight_data_version = read_sqlite_data_version(db_path)
            report["preflight_data_version"] = preflight_data_version
    except Exception as exc:
        raise GcThinFilesError(
            f"GC run failed during preflight_data_version: {exc}",
            phase="preflight_data_version",
            preflight_data_version=None,
            apply=apply,
            transaction="not_started",
            cutoff_days=days,
            batch_size=batch_size,
        ) from exc

    store = SignalStore(db_path)
    try:
        phase = "initialize"
        await store.initialize()

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        report["cutoff_date"] = cutoff_iso

        # --- Discover candidates ---
        phase = "find_candidates"
        candidate_ids = await _find_gc_candidates(store, cutoff_iso)
        report["company_files_found"] = len(candidate_ids)
        report["sample_company_ids"] = candidate_ids[:_MAX_SAMPLE_IDS]

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
        phase = "delete_company_files"
        for i in range(0, len(candidate_ids), batch_size):
            current_batch_index = (i // batch_size) + 1
            batch = candidate_ids[i : i + batch_size]
            deleted = await _delete_company_files_batch(store, batch)
            total_deleted += deleted
            report["company_files_deleted"] = total_deleted
            logger.info(
                "Batch %d: deleted %d/%d company_files.",
                current_batch_index,
                deleted,
                len(batch),
            )

        report["company_files_deleted"] = total_deleted

        # --- Clean orphaned reviews ---
        phase = "clean_orphaned_reviews"
        orphans_cleaned = await _clean_orphaned_reviews(store, candidate_ids)
        report["orphaned_reviews_cleaned"] = orphans_cleaned
        if orphans_cleaned:
            logger.info(
                "Cleaned %d orphaned review_items (rejected/deferred).",
                orphans_cleaned,
            )

        # --- Audit log ---
        phase = "write_audit_log"
        await _write_audit_entry(store, report)
        report["audit_log_written"] = True
        report["transaction"] = "committed"
        logger.info("Audit log entry written.")

        return report

    except DBToolError:
        raise
    except Exception as exc:
        raise GcThinFilesError(
            f"GC run failed during {phase}: {exc}",
            phase=phase,
            preflight_data_version=preflight_data_version,
            apply=apply,
            transaction=_transaction_state_for_failure(report, phase),
            cutoff_days=days,
            batch_size=batch_size,
            company_files_found=report.get("company_files_found"),
            company_files_deleted=report.get("company_files_deleted"),
            orphaned_reviews_cleaned=report.get("orphaned_reviews_cleaned"),
            audit_log_written=report.get("audit_log_written"),
            current_batch_index=current_batch_index,
        ) from exc
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
    parser.add_argument(
        "--report",
        default="",
        help="Optional path to write a JSON report envelope.",
    )
    return parser


async def async_main() -> int:
    """Async entry point -- parses args and runs GC."""
    # Reconfigure stdout for Windows console safety
    sys.stdout.reconfigure(errors="replace")

    parser = _build_parser()
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    report_path = args.report or None

    # Validate numeric arguments
    if args.days < 1:
        logger.error("--days must be >= 1 (got %d).", args.days)
        details = {
            "phase": "validate_args",
            "error": "--days must be >= 1",
            "days": args.days,
            "apply": args.apply,
            "preflight_data_version": None,
        }
        if args.apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=details,
            errors=[details["error"]],
        )
        return 1
    if args.batch_size < 1:
        logger.error("--batch-size must be >= 1 (got %d).", args.batch_size)
        details = {
            "phase": "validate_args",
            "error": "--batch-size must be >= 1",
            "batch_size": args.batch_size,
            "apply": args.apply,
            "preflight_data_version": None,
        }
        if args.apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=details,
            errors=[details["error"]],
        )
        return 1

    # --apply overrides the default --dry-run
    apply = args.apply
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None

    try:
        preflight_data_version = read_sqlite_data_version(args.db)
    except Exception as exc:
        details = {
            "phase": "preflight_data_version",
            "error": str(exc),
            "apply": apply,
            "preflight_data_version": None,
        }
        if apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=details,
            errors=[str(exc)],
        )
        logger.error("GC preflight failed: %s", exc)
        return 1

    if apply:
        lock = DBToolLock(args.db, tool_name=TOOL_NAME)
        if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
            holder = lock.get_holder_info()
            details = {
                "holder": holder,
                "preflight_data_version": preflight_data_version,
                "apply": apply,
            }
            _append_ledger(db_path=args.db, status="lock_blocked", details=details)
            _write_report_if_requested(
                report_path=report_path,
                db_path=args.db,
                started_at=started_at,
                ok=False,
                metrics=details,
                errors=["Could not acquire DB tool lock"],
            )
            print(f"ERROR: Could not acquire DB tool lock. Holder: {holder}", file=sys.stderr)
            return 2

    try:
        report = await run_gc(
            db_path=args.db,
            days=args.days,
            batch_size=args.batch_size,
            apply=apply,
            preflight_data_version=preflight_data_version,
        )
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=True,
            metrics=report,
        )
        if apply:
            _append_ledger(db_path=args.db, status="success", details=report)
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=details,
            errors=[str(exc)],
        )
        logger.exception("GC run failed.")
        return 1
    except Exception as exc:
        details = {"error": str(exc), "preflight_data_version": preflight_data_version}
        if apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=details,
            errors=[str(exc)],
        )
        logger.exception("GC run failed.")
        return 1
    finally:
        if lock is not None:
            lock.release()

    # Emit JSON report to stdout
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
