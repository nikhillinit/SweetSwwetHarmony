"""Backfill company name extraction for news/RSS signals.

Re-runs the improved extraction pipeline (verb expansion, appositive,
backs-with-descriptor, NER scoring) on existing news_api / rss_feeds
signals, updating company_name, canonical_key, and raw_data JSON.

Follows backfill_evidence_keys.py pattern (sync sqlite3, chunked).

Usage:
    python scripts/backfill_company_extraction.py --db signals.db --preflight
    python scripts/backfill_company_extraction.py --db signals.db --dry-run
    python scripts/backfill_company_extraction.py --db signals.db --commit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# Source APIs to re-extract
_TARGET_SOURCES = ("news_api", "rss_feeds")


def _parse_raw_data(raw_data_str: str) -> Optional[Dict[str, Any]]:
    """Parse raw_data JSON string, returning None on failure."""
    try:
        return json.loads(raw_data_str)
    except (json.JSONDecodeError, TypeError):
        return None


def _rebuild_canonical_key(
    company_name: Optional[str],
    promoted_domain: Optional[str],
) -> Optional[str]:
    """Rebuild canonical_key from extraction result.

    Priority: domain > name_loc > None (leave unchanged).
    """
    from utils.canonical_keys import build_canonical_key

    if promoted_domain:
        return build_canonical_key(domain_or_website=promoted_domain)
    if company_name:
        return build_canonical_key(fallback_company_name=company_name)
    return None


def preflight(db_path: str) -> Dict[str, Any]:
    """Show summary statistics for news/RSS signals before backfill."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?)",
            _TARGET_SOURCES,
        ).fetchone()[0]

        hash_keys = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND canonical_key LIKE 'rss_%'",
            _TARGET_SOURCES,
        ).fetchone()[0]

        name_loc_keys = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND canonical_key LIKE 'name_loc:%'",
            _TARGET_SOURCES,
        ).fetchone()[0]

        domain_keys = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND canonical_key LIKE 'domain:%'",
            _TARGET_SOURCES,
        ).fetchone()[0]

        no_company = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND (company_name IS NULL OR company_name = '')",
            _TARGET_SOURCES,
        ).fetchone()[0]

        return {
            "total_news_rss_signals": total,
            "hash_canonical_keys": hash_keys,
            "name_loc_keys": name_loc_keys,
            "domain_keys": domain_keys,
            "missing_company_name": no_company,
        }
    finally:
        conn.close()


def run(
    db_path: str,
    dry_run: bool = True,
    chunk_size: int = 100,
) -> Dict[str, Any]:
    """Re-extract company names for news/RSS signals.

    1. SELECT all news/RSS signals
    2. Re-extract company name from raw_data title + description
    3. Rebuild canonical_key if extraction improved
    4. UPDATE company_name, canonical_key, and raw_data (with backfill flag)

    Returns: {total, scanned, updated, unchanged, errors, dry_run, diffs}
    """
    from utils.company_name_extractor import extract_company_info, warmup_ner

    # Warm up NER model once
    warmup_ner()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?)",
            _TARGET_SOURCES,
        ).fetchone()[0]

        scanned = 0
        updated = 0
        unchanged = 0
        errors = 0
        diffs: List[Dict[str, Any]] = []

        offset = 0
        while True:
            rows = conn.execute(
                "SELECT id, source_api, company_name, canonical_key, raw_data "
                "FROM signals WHERE source_api IN (?, ?) "
                "ORDER BY id LIMIT ? OFFSET ?",
                (*_TARGET_SOURCES, chunk_size, offset),
            ).fetchall()
            if not rows:
                break

            batch_updates: List[tuple] = []

            for row_id, source_api, old_name, old_key, raw_data_str in rows:
                scanned += 1
                raw = _parse_raw_data(raw_data_str)
                if raw is None:
                    errors += 1
                    continue

                title = raw.get("title", "")
                description = raw.get("description", "")

                # Re-extract with improved pipeline
                try:
                    result = extract_company_info(
                        title=title,
                        description=description,
                        url=raw.get("url", ""),
                        mode="ner_active",
                    )
                except Exception as e:
                    logger.warning("Extraction error for signal %d: %s", row_id, e)
                    errors += 1
                    continue

                new_name = result.company_name
                new_key = _rebuild_canonical_key(
                    new_name, result.promoted_domain
                )

                # Only update if extraction actually improved
                if not new_name:
                    unchanged += 1
                    continue
                if new_name == old_name and (new_key is None or new_key == old_key):
                    unchanged += 1
                    continue

                # Use old key if rebuild returned None
                final_key = new_key if new_key else old_key

                # Flag backfill in raw_data
                raw["_backfill_extraction"] = True
                if old_name and old_name != new_name:
                    raw["_backfill_old_company_name"] = old_name
                if old_key != final_key:
                    raw["_backfill_old_canonical_key"] = old_key

                diff = {
                    "id": row_id,
                    "old_name": old_name,
                    "new_name": new_name,
                    "old_key": old_key,
                    "new_key": final_key,
                    "method": result.company_name_method,
                }
                diffs.append(diff)

                batch_updates.append((
                    new_name,
                    final_key,
                    json.dumps(raw, ensure_ascii=False),
                    row_id,
                ))

            # Apply batch
            if batch_updates and not dry_run:
                conn.execute("BEGIN")
                try:
                    conn.executemany(
                        "UPDATE signals SET company_name = ?, canonical_key = ?, "
                        "raw_data = ? WHERE id = ?",
                        batch_updates,
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            updated += len(batch_updates)
            offset += chunk_size

        return {
            "total": total,
            "scanned": scanned,
            "updated": updated,
            "unchanged": unchanged,
            "errors": errors,
            "dry_run": dry_run,
            "diffs": diffs if dry_run else [],
        }

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill company name extraction for news/RSS signals"
    )
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview without modifying"
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually apply changes"
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="Show summary statistics only"
    )
    parser.add_argument("--chunk-size", type=int, default=100)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    if args.preflight:
        report = preflight(args.db)
        print(json.dumps(report, indent=2))
        sys.exit(0)

    dry_run = not args.commit
    report = run(args.db, dry_run=dry_run, chunk_size=args.chunk_size)

    if dry_run and report["diffs"]:
        print(f"\n=== DRY RUN: {len(report['diffs'])} signals would be updated ===\n")
        for d in report["diffs"]:
            print(f"  Signal {d['id']}: {d['old_name']!r} -> {d['new_name']!r} "
                  f"(method={d['method']}, key: {d['old_key']} -> {d['new_key']})")

    summary = {k: v for k, v in report.items() if k != "diffs"}
    print(f"\n{json.dumps(summary, indent=2)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
