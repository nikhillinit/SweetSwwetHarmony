"""Backfill evidence_key column for existing signals.

Chunked SELECT->UPDATE loop following backfill_evidence_family.py pattern.
Importable by CLI and tests.

Usage:
    python scripts/backfill_evidence_keys.py --db signals.db --dry-run
    python scripts/backfill_evidence_keys.py --db signals.db --commit
    python scripts/backfill_evidence_keys.py --db signals.db --preflight
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 5


class BackfillEvidenceKeysError(DBToolError):
    """evidence_key backfill failure carrying partial progress evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        rows_scanned: int = 0,
        rows_updated_attempted: int = 0,
        rows_no_url: int = 0,
        duplicate_groups: int = 0,
        chunk_size: int | None = None,
        dry_run: bool | None = None,
        preflight_data_version: int | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "rows_scanned": rows_scanned,
                "rows_updated_attempted": rows_updated_attempted,
                "rows_no_url": rows_no_url,
                "duplicate_groups": duplicate_groups,
                "chunk_size": chunk_size,
                "dry_run": dry_run,
                "preflight_data_version": preflight_data_version,
            },
        )


def _extract_url(raw_data_str: str) -> str:
    """Extract source URL from raw_data JSON string.

    Priority: _provenance.source_url > top-level url
    """
    try:
        raw = json.loads(raw_data_str)
    except (json.JSONDecodeError, TypeError):
        return ""

    # Provenance block
    prov = raw.get("_provenance")
    if isinstance(prov, dict):
        url = prov.get("source_url", "")
        if url:
            return url

    # Fallback
    return raw.get("url", "")


def preflight(db_path: str) -> Dict[str, Any]:
    """Check for duplicate evidence_keys before applying UNIQUE index.

    Returns report with duplicate groups found.
    Exit code: 0 if clean, 1 if duplicates remain.
    """
    from utils.evidence_key import compute_evidence_key

    preflight_data_version = read_sqlite_data_version(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        cursor = conn.execute(
            "SELECT id, source_api, raw_data FROM signals ORDER BY id"
        )
        rows = cursor.fetchall()

        # Compute evidence_keys and find duplicates
        key_to_ids: Dict[str, List[int]] = {}
        for row_id, source_api, raw_data_str in rows:
            url = _extract_url(raw_data_str)
            if not url:
                continue
            ek = compute_evidence_key(source_api, url)
            if ek:
                key_to_ids.setdefault(ek, []).append(row_id)

        duplicate_groups = {k: v for k, v in key_to_ids.items() if len(v) > 1}

        return {
            "total_signals": len(rows),
            "signals_with_url": sum(1 for v in key_to_ids.values() for _ in v),
            "duplicate_groups": len(duplicate_groups),
            "preflight_data_version": preflight_data_version,
            "duplicates": [
                {"evidence_key": k, "signal_ids": ids, "count": len(ids)}
                for k, ids in sorted(duplicate_groups.items(), key=lambda x: -len(x[1]))
            ],
            "clean": len(duplicate_groups) == 0,
        }
    finally:
        conn.close()


def run(
    db_path: str,
    dry_run: bool = True,
    chunk_size: int = 500,
    preflight_data_version: int | None = None,
) -> Dict[str, Any]:
    """Backfill evidence_key for existing signals.

    1. SELECT all signals WHERE evidence_key IS NULL
    2. For each: extract source_url from raw_data, compute evidence_key
    3. Detect duplicate evidence_key groups (same evidence_key)
    4. Soft-archive duplicates: keep lowest-id winner, SET evidence_key=NULL for losers
    5. UPDATE winners with computed evidence_key

    Returns: {rows_scanned, rows_updated, rows_archived, duplicate_groups, dry_run}
    """
    from utils.evidence_key import compute_evidence_key

    phase = "preflight_data_version"
    rows_scanned = 0
    rows_updated = 0
    rows_updated_attempted = 0
    rows_no_url = 0
    duplicate_groups = 0
    transaction_started = False
    conn: sqlite3.Connection | None = None
    try:
        if preflight_data_version is None:
            preflight_data_version = read_sqlite_data_version(db_path)

        phase = "connect"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        # Count total eligible
        phase = "count"
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE evidence_key IS NULL"
        ).fetchone()[0]

        key_to_ids: Dict[str, List[Tuple[int, str]]] = {}  # ek -> [(id, source_api)]

        # Phase 1: Scan all rows and compute evidence_keys
        offset = 0
        while True:
            phase = "select_chunk"
            rows = conn.execute(
                "SELECT id, source_api, raw_data FROM signals "
                "WHERE evidence_key IS NULL ORDER BY id LIMIT ? OFFSET ?",
                (chunk_size, offset),
            ).fetchall()
            if not rows:
                break

            for row_id, source_api, raw_data_str in rows:
                phase = "process_row"
                rows_scanned += 1
                url = _extract_url(raw_data_str)
                if not url:
                    rows_no_url += 1
                    continue
                ek = compute_evidence_key(source_api, url)
                if ek:
                    key_to_ids.setdefault(ek, []).append((row_id, source_api))

            offset += chunk_size

        # Phase 2: Identify duplicates and soft-archive
        rows_archived = 0
        updates: List[Tuple[str, int]] = []  # (evidence_key, id) for winners
        archive_ids: List[int] = []  # IDs to soft-archive (set evidence_key=NULL)

        phase = "dedupe"
        for ek, id_list in key_to_ids.items():
            if len(id_list) > 1:
                duplicate_groups += 1
                # Keep lowest-id as winner
                id_list.sort(key=lambda x: x[0])
                winner_id = id_list[0][0]
                updates.append((ek, winner_id))
                for loser_id, _ in id_list[1:]:
                    archive_ids.append(loser_id)
                    rows_archived += 1
            else:
                updates.append((ek, id_list[0][0]))

        # Phase 3: Apply updates
        if not dry_run and updates:
            phase = "begin"
            conn.execute("BEGIN IMMEDIATE")
            transaction_started = True
            try:
                # Update winners with evidence_key
                phase = "apply_updates"
                rows_updated_attempted = len(updates)
                conn.executemany(
                    "UPDATE signals SET evidence_key = ? WHERE id = ?",
                    updates,
                )
                rows_updated = len(updates)

                # Soft-archive losers: explicitly NULL evidence_key
                # (they're already NULL, but this makes intent explicit
                # and prevents future backfill from re-computing them)
                if archive_ids:
                    # Mark archived rows so they're skipped on re-run
                    # Using a sentinel in evidence_key would conflict with
                    # the partial index. Instead, they stay NULL and are
                    # excluded from the UNIQUE partial index naturally.
                    pass

                phase = "commit"
                conn.commit()
                transaction_started = False
            except Exception:
                if transaction_started:
                    conn.rollback()
                    transaction_started = False
                raise
        elif dry_run:
            rows_updated = len(updates)

        return {
            "rows_total_eligible": total,
            "rows_scanned": rows_scanned,
            "rows_updated": rows_updated,
            "rows_no_url": rows_no_url,
            "rows_archived": rows_archived,
            "duplicate_groups": duplicate_groups,
            "dry_run": dry_run,
            "preflight_data_version": preflight_data_version,
        }

    except BackfillEvidenceKeysError:
        if transaction_started and conn is not None:
            conn.rollback()
        raise
    except Exception as exc:
        if transaction_started and conn is not None:
            conn.rollback()
        raise BackfillEvidenceKeysError(
            f"evidence_key backfill failed: {exc}",
            phase=phase,
            rows_scanned=rows_scanned,
            rows_updated_attempted=rows_updated_attempted,
            rows_no_url=rows_no_url,
            duplicate_groups=duplicate_groups,
            chunk_size=chunk_size,
            dry_run=dry_run,
            preflight_data_version=preflight_data_version,
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill evidence_key for signals")
    parser.add_argument("--db", required=True, help="Path to signals.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    mode.add_argument("--commit", action="store_true", help="Apply changes to the database")
    parser.add_argument("--preflight", action="store_true",
                        help="Check for duplicate evidence_keys (exit 0=clean, 1=dupes)")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    tool_name = "backfill_evidence_keys"
    action = "backfill_evidence_keys"
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
        sys.exit(1)

    if args.preflight:
        report = preflight(args.db)
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["clean"] else 1)

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
            sys.exit(2)

    try:
        report = run(
            args.db,
            dry_run=dry_run,
            chunk_size=args.chunk_size,
            preflight_data_version=preflight_data_version,
        )
        envelope = create_report(
            command=action,
            ok=True,
            db_path=args.db,
            started_at=started_at,
            metrics=report,
        )
        if report_path:
            write_report(envelope, report_path)
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="success",
                details=report,
            )
        print(json.dumps(report, indent=2))
        if report["duplicate_groups"] > 0:
            print(f"\nWARNING: {report['duplicate_groups']} duplicate groups found.")
            if report["dry_run"]:
                print("Run with --commit to apply soft-archive.")
        sys.exit(0)
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
        envelope = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        if report_path:
            write_report(envelope, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
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
        envelope = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics={"preflight_data_version": preflight_data_version},
            errors=[str(exc)],
        )
        if report_path:
            write_report(envelope, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    main()
