"""
Backfill script: re-process historical signals through the new company name extractor.

Reads existing signals from the database, runs extract_company_info() on their
title/description, and optionally updates canonical_key + raw_data.

Usage:
    # Dry-run (report only, no DB writes)
    python scripts/backfill_company_extraction.py --db signals.db

    # Apply changes
    python scripts/backfill_company_extraction.py --db signals.db --apply

    # Specific mode override
    python scripts/backfill_company_extraction.py --db signals.db --mode ner_active
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from utils.company_name_extractor import (
    ExtractionMode,
    extract_company_info,
    warmup_ner,
)
from utils.canonical_keys import build_canonical_key_candidates

logger = logging.getLogger(__name__)


async def run_backfill(
    db_path: str,
    mode: ExtractionMode = "ner_active",
    apply: bool = False,
    limit: int = 0,
) -> dict:
    """
    Re-process signals through the new extractor.

    Args:
        db_path: Path to signals.db
        mode: Extraction mode to use
        apply: If True, write changes to DB. If False, dry-run only.
        limit: Max signals to process (0 = all)

    Returns:
        Summary dict with counts and examples
    """
    # Pre-load NER model if needed
    if mode == "ner_active":
        warmup_ner()

    stats = {
        "total_processed": 0,
        "would_change_key": 0,
        "would_add_domain": 0,
        "collisions_skipped": 0,
        "errors": 0,
        "examples": [],
    }

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Get signals from news_api and rss_feeds collectors
        query = """
            SELECT id, canonical_key, raw_data, source_api
            FROM signals
            WHERE source_api IN ('news_api', 'rss_feeds')
            ORDER BY created_at DESC
        """
        if limit > 0:
            query += f" LIMIT {limit}"

        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()

        logger.info("Processing %d signals (apply=%s, mode=%s)", len(rows), apply, mode)

        # Build set of existing canonical keys for collision detection
        existing_keys: set[str] = set()
        async with db.execute("SELECT DISTINCT canonical_key FROM signals WHERE canonical_key IS NOT NULL") as cursor:
            async for row in cursor:
                existing_keys.add(row[0])

        for row in rows:
            signal_id = row["id"]
            old_key = row["canonical_key"]
            source_api = row["source_api"]

            try:
                raw_data = json.loads(row["raw_data"]) if isinstance(row["raw_data"], str) else row["raw_data"]
            except (json.JSONDecodeError, TypeError):
                stats["errors"] += 1
                continue

            title = raw_data.get("title", "")
            description = raw_data.get("description", "")
            url = raw_data.get("url", "")

            if not title:
                stats["total_processed"] += 1
                continue

            # Run extraction
            try:
                result = extract_company_info(
                    title=title,
                    description=description,
                    url=url,
                    mode=mode,
                )
            except Exception as e:
                logger.warning("Extraction error for signal %s: %s", signal_id, e)
                stats["errors"] += 1
                continue

            stats["total_processed"] += 1

            # Build new canonical key
            domain_for_key = result.promoted_domain or ""
            new_candidates = build_canonical_key_candidates(
                domain_or_website=domain_for_key,
                fallback_company_name=result.company_name or "",
            )
            new_key = new_candidates[0] if new_candidates else old_key

            # Track changes
            key_changed = new_key != old_key and new_key and new_key != signal_id
            has_new_domain = result.promoted_domain is not None

            if has_new_domain:
                stats["would_add_domain"] += 1

            if key_changed:
                # Collision detection
                if new_key in existing_keys and new_key != old_key:
                    stats["collisions_skipped"] += 1
                    if len(stats["examples"]) < 20:
                        stats["examples"].append({
                            "signal_id": signal_id,
                            "old_key": old_key,
                            "new_key": new_key,
                            "status": "COLLISION_SKIPPED",
                            "company_name": result.company_name,
                            "method": result.company_name_method,
                            "promoted_domain": result.promoted_domain,
                        })
                    continue

                stats["would_change_key"] += 1

                if len(stats["examples"]) < 20:
                    stats["examples"].append({
                        "signal_id": signal_id,
                        "old_key": old_key,
                        "new_key": new_key,
                        "status": "WOULD_CHANGE" if not apply else "CHANGED",
                        "company_name": result.company_name,
                        "method": result.company_name_method,
                        "promoted_domain": result.promoted_domain,
                    })

                if apply:
                    # Store old key for revert capability
                    raw_data["old_canonical_key"] = old_key
                    raw_data["company_name_method"] = result.company_name_method
                    raw_data["candidate_domains"] = result.candidate_domains
                    raw_data["promoted_domain"] = result.promoted_domain

                    await db.execute(
                        "UPDATE signals SET canonical_key = ?, raw_data = ? WHERE id = ?",
                        (new_key, json.dumps(raw_data), signal_id),
                    )
                    existing_keys.add(new_key)

        if apply:
            await db.commit()

    return stats


def print_report(stats: dict, apply: bool) -> None:
    """Print human-readable backfill report."""
    prefix = "Applied" if apply else "Dry-run"
    print(f"\n{'=' * 60}")
    print(f"COMPANY EXTRACTION BACKFILL REPORT ({prefix})")
    print(f"{'=' * 60}")
    print(f"Total processed:     {stats['total_processed']}")
    print(f"Would change key:    {stats['would_change_key']}")
    print(f"Would add domain:    {stats['would_add_domain']}")
    print(f"Collisions skipped:  {stats['collisions_skipped']}")
    print(f"Errors:              {stats['errors']}")
    print()

    if stats["examples"]:
        print("Sample changes:")
        print("-" * 60)
        for ex in stats["examples"]:
            print(f"  [{ex['status']}] {ex['signal_id']}")
            print(f"    old: {ex['old_key']}")
            print(f"    new: {ex['new_key']}")
            print(f"    method: {ex['method']}, domain: {ex['promoted_domain']}")
            print()

    if not apply and stats["would_change_key"] > 0:
        print("To apply these changes, re-run with --apply")

    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Backfill company extraction for historical signals")
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")
    parser.add_argument("--mode", choices=["baseline", "url_promote", "ner_active"],
                        default="ner_active", help="Extraction mode (default: ner_active)")
    parser.add_argument("--limit", type=int, default=0, help="Max signals to process (0 = all)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    stats = asyncio.run(run_backfill(
        db_path=args.db,
        mode=args.mode,
        apply=args.apply,
        limit=args.limit,
    ))

    print_report(stats, args.apply)


if __name__ == "__main__":
    main()
