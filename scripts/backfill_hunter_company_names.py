"""
Backfill Hunter Company Names

Recomputes company_name and canonical_key for existing hunter_results
using the shared company name extractor. Also updates linked signals
via promoted_signal_id.

Usage:
    python scripts/backfill_hunter_company_names.py --db signals.db           # dry-run
    python scripts/backfill_hunter_company_names.py --db signals.db --apply   # write

Exit codes:
    0 = success or dry-run
    1 = preflight or assertion failure
    2 = lock, transaction, or runtime error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite

from utils.canonical_keys import normalize_domain
from utils.company_name_extractor import (
    extract_company_info,
    is_blocked_domain,
)
from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.hn_title import extract_name_from_hn_body, strip_hn_prefix
from utils.report_envelope import create_report, write_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TOOL_NAME = "backfill_hunter_company_names"
LOCK_TIMEOUT_SECONDS = 5


class BackfillHunterCompanyNamesError(DBToolError):
    """Hunter backfill failure carrying partial progress evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        apply: bool,
        transaction: str,
        total_count: int = 0,
        updates_planned: int = 0,
        signal_updates_planned: int = 0,
        hunter_results_updated_attempted: int = 0,
        signals_updated_attempted: int = 0,
        post_count: int | None = None,
        mismatch_count: int | None = None,
        preflight_data_version: int | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "apply": apply,
                "transaction": transaction,
                "total_count": total_count,
                "updates_planned": updates_planned,
                "signal_updates_planned": signal_updates_planned,
                "hunter_results_updated_attempted": hunter_results_updated_attempted,
                "signals_updated_attempted": signals_updated_attempted,
                "post_count": post_count,
                "mismatch_count": mismatch_count,
                "preflight_data_version": preflight_data_version,
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


def _parse_raw_data(raw_data_str: Any) -> dict[str, Any]:
    if isinstance(raw_data_str, str):
        try:
            raw_data = json.loads(raw_data_str)
        except (json.JSONDecodeError, TypeError):
            return {}
    elif isinstance(raw_data_str, dict):
        raw_data = raw_data_str
    else:
        return {}
    return raw_data if isinstance(raw_data, dict) else {}


def _compute_backfill_target(row: aiosqlite.Row) -> tuple[str, str] | None:
    old_name = row["company_name"] or ""
    old_key = row["canonical_key"] or ""
    source_api = row["source_api"] or ""

    # GitHub results keep their existing extraction (full_name is correct).
    if source_api == "github":
        return None

    raw_data = _parse_raw_data(row["raw_data"])
    title = raw_data.get("title", "")
    description = raw_data.get("description", "")
    url = raw_data.get("url", "")

    # Parse HN prefix for HN-sourced results.
    cleaned_title, hn_prefix = strip_hn_prefix(title)
    effective_title = cleaned_title if hn_prefix else title

    hn_name = None
    if hn_prefix in ("show", "launch", "demo"):
        hn_name = extract_name_from_hn_body(cleaned_title)

    info = extract_company_info(
        effective_title,
        description=description,
        url=url,
        mode="url_promote",
    )
    new_name = info.company_name or hn_name or ""

    new_key = ""
    if info.promoted_domain and not is_blocked_domain(info.promoted_domain):
        new_key = f"domain:{normalize_domain(info.promoted_domain)}"
    elif new_name:
        new_key = f"name_loc:{new_name.lower()}"

    # Invariant: non-empty name requires non-empty key.
    if new_name and not new_key:
        new_name = ""

    # Normalize Unknown.
    if new_name.strip().lower() == "unknown":
        new_name = ""

    if new_name == old_name and new_key == old_key:
        return None
    return new_name, new_key


async def backfill(
    db_path: str,
    apply: bool = False,
    *,
    preflight_data_version: int | None = None,
) -> dict[str, Any]:
    """Run the hunter company-name backfill and return a JSON-safe report."""
    phase = "preflight_data_version"
    total_count = 0
    updates_planned = 0
    signal_updates_planned = 0
    hunter_results_updated_attempted = 0
    signals_updated_attempted = 0
    transaction_started = False

    try:
        if preflight_data_version is None:
            preflight_data_version = read_sqlite_data_version(db_path)

        phase = "connect"
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            phase = "count"
            cursor = await db.execute("SELECT COUNT(*) FROM hunter_results")
            (total_count,) = await cursor.fetchone()
            logger.info("Total hunter_results rows: %d", total_count)

            phase = "fetch_rows"
            cursor = await db.execute(
                """
                SELECT id, company_name, canonical_key, raw_data, source_api,
                       promoted_signal_id
                FROM hunter_results
                """
            )
            rows = await cursor.fetchall()

            updates: list[tuple[str, str, int]] = []
            signal_updates: list[tuple[str, int]] = []

            phase = "compute_updates"
            for row in rows:
                target = _compute_backfill_target(row)
                if target is None:
                    continue

                row_id = row["id"]
                new_name, new_key = target
                updates.append((new_name, new_key, row_id))

                promoted_signal_id = row["promoted_signal_id"]
                if promoted_signal_id:
                    signal_updates.append((new_name, promoted_signal_id))

                logger.info(
                    "Row %s: '%s' (%s) -> '%s' (%s)",
                    row_id,
                    (row["company_name"] or "")[:40],
                    (row["canonical_key"] or "")[:40],
                    new_name[:40],
                    new_key[:40],
                )

            updates_planned = len(updates)
            signal_updates_planned = len(signal_updates)
            logger.info(
                "Changes: %d/%d hunter_results, %d linked signals",
                updates_planned,
                total_count,
                signal_updates_planned,
            )

            report: dict[str, Any] = {
                "dry_run": not apply,
                "apply": apply,
                "total_count": total_count,
                "updates_planned": updates_planned,
                "signal_updates_planned": signal_updates_planned,
                "hunter_results_updated": 0,
                "signals_updated": 0,
                "transaction": "not_started",
                "preflight_data_version": preflight_data_version,
            }

            if not apply:
                logger.info("DRY RUN - no changes written")
                return report

            if updates_planned > total_count:
                raise BackfillHunterCompanyNamesError(
                    "ASSERTION FAILED: updates exceed total rows",
                    phase="validate_update_count",
                    apply=apply,
                    transaction="not_started",
                    total_count=total_count,
                    updates_planned=updates_planned,
                    signal_updates_planned=signal_updates_planned,
                    preflight_data_version=preflight_data_version,
                )

            if updates:
                try:
                    phase = "begin"
                    await db.execute("BEGIN IMMEDIATE")
                    transaction_started = True
                    report["transaction"] = "started"

                    phase = "apply_hunter_results"
                    hunter_results_updated_attempted = len(updates)
                    await db.executemany(
                        """
                        UPDATE hunter_results
                        SET company_name = ?, canonical_key = ?
                        WHERE id = ?
                        """,
                        updates,
                    )
                    report["hunter_results_updated"] = len(updates)

                    phase = "apply_signals"
                    signals_updated_attempted = len(signal_updates)
                    if signal_updates:
                        await db.executemany(
                            """
                            UPDATE signals
                            SET company_name = ?
                            WHERE id = ?
                            """,
                            signal_updates,
                        )
                    report["signals_updated"] = len(signal_updates)

                    phase = "commit"
                    await db.commit()
                    transaction_started = False
                    report["transaction"] = "committed"
                except Exception as exc:
                    if transaction_started:
                        await db.rollback()
                        transaction_started = False
                    raise BackfillHunterCompanyNamesError(
                        f"hunter company-name backfill failed: {exc}",
                        phase=phase,
                        apply=apply,
                        transaction="rolled_back",
                        total_count=total_count,
                        updates_planned=updates_planned,
                        signal_updates_planned=signal_updates_planned,
                        hunter_results_updated_attempted=hunter_results_updated_attempted,
                        signals_updated_attempted=signals_updated_attempted,
                        preflight_data_version=preflight_data_version,
                    ) from exc

            phase = "post_count"
            cursor = await db.execute("SELECT COUNT(*) FROM hunter_results")
            (post_count,) = await cursor.fetchone()
            if post_count != total_count:
                raise BackfillHunterCompanyNamesError(
                    f"ASSERTION FAILED: row count changed {total_count} -> {post_count}",
                    phase=phase,
                    apply=apply,
                    transaction=report["transaction"],
                    total_count=total_count,
                    updates_planned=updates_planned,
                    signal_updates_planned=signal_updates_planned,
                    hunter_results_updated_attempted=hunter_results_updated_attempted,
                    signals_updated_attempted=signals_updated_attempted,
                    post_count=post_count,
                    preflight_data_version=preflight_data_version,
                )

            if signal_updates:
                phase = "post_consistency"
                cursor = await db.execute(
                    """
                    SELECT hr.id, hr.company_name AS hr_name,
                           s.company_name AS s_name
                    FROM hunter_results hr
                    JOIN signals s ON s.id = hr.promoted_signal_id
                    WHERE hr.promoted_signal_id IS NOT NULL
                      AND hr.company_name != s.company_name
                    """
                )
                mismatches = await cursor.fetchall()
                if mismatches:
                    raise BackfillHunterCompanyNamesError(
                        "ASSERTION FAILED: hunter_results and signals names mismatch",
                        phase=phase,
                        apply=apply,
                        transaction=report["transaction"],
                        total_count=total_count,
                        updates_planned=updates_planned,
                        signal_updates_planned=signal_updates_planned,
                        hunter_results_updated_attempted=hunter_results_updated_attempted,
                        signals_updated_attempted=signals_updated_attempted,
                        mismatch_count=len(mismatches),
                        preflight_data_version=preflight_data_version,
                    )

            logger.info(
                "Backfill complete: %d rows updated, %d signals synced",
                report["hunter_results_updated"],
                report["signals_updated"],
            )
            return report

    except BackfillHunterCompanyNamesError:
        raise
    except Exception as exc:
        raise BackfillHunterCompanyNamesError(
            f"hunter company-name backfill failed: {exc}",
            phase=phase,
            apply=apply,
            transaction="rolled_back" if transaction_started else "not_started",
            total_count=total_count,
            updates_planned=updates_planned,
            signal_updates_planned=signal_updates_planned,
            hunter_results_updated_attempted=hunter_results_updated_attempted,
            signals_updated_attempted=signals_updated_attempted,
            preflight_data_version=preflight_data_version,
        ) from exc


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill hunter_results company names with shared extractor"
    )
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry-run)",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional path to write a JSON report envelope.",
    )
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    report_path = args.report or None
    lock: DBToolLock | None = None
    preflight_data_version: int | None = None

    try:
        preflight_data_version = read_sqlite_data_version(args.db)
    except Exception as exc:
        details = {
            "phase": "preflight_data_version",
            "error": str(exc),
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
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.apply:
        lock = DBToolLock(args.db, tool_name=TOOL_NAME)
        if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
            holder = lock.get_holder_info()
            details = {
                "holder": holder,
                "apply": True,
                "preflight_data_version": preflight_data_version,
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
        report = await backfill(
            args.db,
            apply=args.apply,
            preflight_data_version=preflight_data_version,
        )
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=True,
            metrics=report,
        )
        if args.apply:
            _append_ledger(db_path=args.db, status="success", details=report)
        print(json.dumps(report, indent=2))
        return 0
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if args.apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        if details.get("phase", "").startswith("post_") or details.get("phase") == "validate_update_count":
            return 1
        return 2
    except Exception as exc:
        details = {"error": str(exc), "preflight_data_version": preflight_data_version}
        if args.apply:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            db_path=args.db,
            started_at=started_at,
            ok=False,
            metrics=details,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.release()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
