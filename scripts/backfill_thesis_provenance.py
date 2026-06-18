"""Backfill thesis_classifications provenance metadata.

Stamps prompt_version and model on historical rows that predate provenance
tracking.

Usage:
  python scripts/backfill_thesis_provenance.py --dry-run
  python scripts/backfill_thesis_provenance.py --commit
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_path_helper import resolve_db_path_env
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report

LOCK_TIMEOUT_SECONDS = 5


class BackfillThesisProvenanceError(DBToolError):
    """Thesis provenance backfill failure carrying partial progress evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        llm_missing: int = 0,
        kw_missing: int = 0,
        llm_updated_attempted: int = 0,
        kw_updated_attempted: int = 0,
        remaining_missing: int | None = None,
        dry_run: bool | None = None,
        preflight_data_version: int | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "llm_missing": llm_missing,
                "kw_missing": kw_missing,
                "llm_updated_attempted": llm_updated_attempted,
                "kw_updated_attempted": kw_updated_attempted,
                "remaining_missing": remaining_missing,
                "dry_run": dry_run,
                "preflight_data_version": preflight_data_version,
            },
        )


def run(
    db_path: str,
    *,
    dry_run: bool = True,
    preflight_data_version: int | None = None,
) -> dict[str, Any]:
    """Backfill missing thesis provenance fields and return metrics."""
    phase = "preflight_data_version"
    llm_missing = 0
    kw_missing = 0
    llm_updated_attempted = 0
    kw_updated_attempted = 0
    remaining: int | None = None
    transaction_started = False
    conn: sqlite3.Connection | None = None

    try:
        if preflight_data_version is None:
            preflight_data_version = read_sqlite_data_version(db_path)

        phase = "connect"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        phase = "count"
        llm_missing = conn.execute(
            "SELECT COUNT(*) FROM thesis_classifications "
            "WHERE rationale IS NOT NULL AND rationale != '' "
            "AND (prompt_version IS NULL OR prompt_version = '')"
        ).fetchone()[0]

        kw_missing = conn.execute(
            "SELECT COUNT(*) FROM thesis_classifications "
            "WHERE (rationale IS NULL OR rationale = '') "
            "AND (prompt_version IS NULL OR prompt_version = '')"
        ).fetchone()[0]

        already_stamped = conn.execute(
            "SELECT COUNT(*) FROM thesis_classifications "
            "WHERE prompt_version IS NOT NULL AND prompt_version != ''"
        ).fetchone()[0]

        total = conn.execute("SELECT COUNT(*) FROM thesis_classifications").fetchone()[0]

        if dry_run or llm_missing + kw_missing == 0:
            remaining = llm_missing + kw_missing
            return {
                "total": total,
                "already_stamped": already_stamped,
                "llm_missing": llm_missing,
                "kw_missing": kw_missing,
                "llm_updated": llm_missing,
                "kw_updated": kw_missing,
                "remaining_missing": remaining,
                "dry_run": dry_run,
                "preflight_data_version": preflight_data_version,
            }

        phase = "begin"
        conn.execute("BEGIN IMMEDIATE")
        transaction_started = True

        if llm_missing > 0:
            phase = "update_llm"
            llm_updated_attempted = llm_missing
            cursor = conn.execute(
                "UPDATE thesis_classifications "
                "SET prompt_version = 'pre-provenance', model = 'gemini-2.0-flash' "
                "WHERE rationale IS NOT NULL AND rationale != '' "
                "AND (prompt_version IS NULL OR prompt_version = '')"
            )
            llm_updated = cursor.rowcount
        else:
            llm_updated = 0

        if kw_missing > 0:
            phase = "update_keyword"
            kw_updated_attempted = kw_missing
            cursor = conn.execute(
                "UPDATE thesis_classifications "
                "SET prompt_version = 'keyword-only' "
                "WHERE (rationale IS NULL OR rationale = '') "
                "AND (prompt_version IS NULL OR prompt_version = '')"
            )
            kw_updated = cursor.rowcount
        else:
            kw_updated = 0

        phase = "verify"
        remaining = conn.execute(
            "SELECT COUNT(*) FROM thesis_classifications "
            "WHERE prompt_version IS NULL OR prompt_version = ''"
        ).fetchone()[0]

        if remaining > 0:
            raise BackfillThesisProvenanceError(
                f"{remaining} rows still missing provenance",
                phase=phase,
                llm_missing=llm_missing,
                kw_missing=kw_missing,
                llm_updated_attempted=llm_updated_attempted,
                kw_updated_attempted=kw_updated_attempted,
                remaining_missing=remaining,
                dry_run=dry_run,
                preflight_data_version=preflight_data_version,
            )

        phase = "commit"
        conn.commit()
        transaction_started = False

        return {
            "total": total,
            "already_stamped": already_stamped,
            "llm_missing": llm_missing,
            "kw_missing": kw_missing,
            "llm_updated": llm_updated,
            "kw_updated": kw_updated,
            "remaining_missing": remaining,
            "dry_run": dry_run,
            "preflight_data_version": preflight_data_version,
        }

    except BackfillThesisProvenanceError:
        if transaction_started and conn is not None:
            conn.rollback()
        raise
    except Exception as exc:
        if transaction_started and conn is not None:
            conn.rollback()
        raise BackfillThesisProvenanceError(
            f"thesis provenance backfill failed: {exc}",
            phase=phase,
            llm_missing=llm_missing,
            kw_missing=kw_missing,
            llm_updated_attempted=llm_updated_attempted,
            kw_updated_attempted=kw_updated_attempted,
            remaining_missing=remaining,
            dry_run=dry_run,
            preflight_data_version=preflight_data_version,
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def _print_summary(metrics: dict[str, Any]) -> None:
    print(json.dumps(metrics, indent=2))
    if metrics["dry_run"] and metrics["llm_missing"] + metrics["kw_missing"] > 0:
        print("\n[DRY RUN] Use --commit to apply these provenance updates.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill thesis provenance metadata")
    parser.add_argument("--db", default=None, help="Database path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview without writing")
    mode.add_argument("--commit", action="store_true", help="Apply provenance updates")
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()
    args.db = resolve_db_path_env(args.db)

    tool_name = "backfill_thesis_provenance"
    action = "backfill_thesis_provenance"
    dry_run = not args.commit
    started_at = datetime.now(timezone.utc)
    report_path = args.report or None
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None

    try:
        preflight_data_version = read_sqlite_data_version(args.db)
    except Exception as exc:
        report = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.commit:
        lock = DBToolLock(args.db, tool_name=tool_name)
        if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
            holder = lock.get_holder_info()
            error = f"Could not acquire DB tool lock. Holder: {holder}"
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="lock_blocked",
                details={
                    "holder": holder,
                    "commit": True,
                    "preflight_data_version": preflight_data_version,
                },
            )
            report = create_report(
                command=action,
                ok=False,
                db_path=args.db,
                started_at=started_at,
                metrics={
                    "holder": holder,
                    "preflight_data_version": preflight_data_version,
                },
                errors=[error],
            )
            if report_path:
                write_report(report, report_path)
            print(f"ERROR: {error}", file=sys.stderr)
            return 2

    try:
        metrics = run(
            args.db,
            dry_run=dry_run,
            preflight_data_version=preflight_data_version,
        )
        report = create_report(
            command=action,
            ok=True,
            db_path=args.db,
            started_at=started_at,
            metrics=metrics,
        )
        if report_path:
            write_report(report, report_path)
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="success",
                details=metrics,
            )
        _print_summary(metrics)
        return 0
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="error",
                details=details,
            )
        report = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        details = {"error": str(exc), "preflight_data_version": preflight_data_version}
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="error",
                details=details,
            )
        report = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics={"preflight_data_version": preflight_data_version},
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
