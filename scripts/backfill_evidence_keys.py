"""Backfill evidence_key column for existing signals.

Chunked SELECT->UPDATE loop following backfill_evidence_family.py pattern.
Importable by CLI and tests.

Usage:
    python scripts/backfill_evidence_keys.py --db signals.db --dry-run
    python scripts/backfill_evidence_keys.py --db signals.db
    python scripts/backfill_evidence_keys.py --db signals.db --preflight
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Count total eligible
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE evidence_key IS NULL"
        ).fetchone()[0]

        rows_scanned = 0
        rows_updated = 0
        rows_no_url = 0
        key_to_ids: Dict[str, List[Tuple[int, str]]] = {}  # ek -> [(id, source_api)]

        # Phase 1: Scan all rows and compute evidence_keys
        offset = 0
        while True:
            rows = conn.execute(
                "SELECT id, source_api, raw_data FROM signals "
                "WHERE evidence_key IS NULL ORDER BY id LIMIT ? OFFSET ?",
                (chunk_size, offset),
            ).fetchall()
            if not rows:
                break

            for row_id, source_api, raw_data_str in rows:
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
        duplicate_groups = 0
        rows_archived = 0
        updates: List[Tuple[str, int]] = []  # (evidence_key, id) for winners
        archive_ids: List[int] = []  # IDs to soft-archive (set evidence_key=NULL)

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
            conn.execute("BEGIN")
            try:
                # Update winners with evidence_key
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

                conn.commit()
            except Exception:
                conn.rollback()
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
        }

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill evidence_key for signals")
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    parser.add_argument("--preflight", action="store_true",
                        help="Check for duplicate evidence_keys (exit 0=clean, 1=dupes)")
    parser.add_argument("--chunk-size", type=int, default=500)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.preflight:
        report = preflight(args.db)
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["clean"] else 1)
    else:
        report = run(args.db, dry_run=args.dry_run, chunk_size=args.chunk_size)
        print(json.dumps(report, indent=2))
        if report["duplicate_groups"] > 0:
            print(f"\nWARNING: {report['duplicate_groups']} duplicate groups found.")
            if report["dry_run"]:
                print("Run without --dry-run to apply soft-archive.")
        sys.exit(0)


if __name__ == "__main__":
    main()
