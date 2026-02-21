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
    1 = assertion failure
    2 = transaction/runtime error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite

from utils.company_name_extractor import (
    extract_company_info,
    _is_blocked_domain,
)
from utils.canonical_keys import normalize_domain
from utils.hn_title import strip_hn_prefix, extract_name_from_hn_body

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def backfill(db_path: str, apply: bool = False) -> int:
    """Run the backfill. Returns exit code."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Count total rows
        cursor = await db.execute("SELECT COUNT(*) FROM hunter_results")
        (total_count,) = await cursor.fetchone()
        logger.info("Total hunter_results rows: %d", total_count)

        # Fetch all hunter_results
        cursor = await db.execute(
            """
            SELECT id, company_name, canonical_key, raw_data, source_api,
                   promoted_signal_id
            FROM hunter_results
            """
        )
        rows = await cursor.fetchall()

        updates = []  # (new_company_name, new_canonical_key, row_id)
        signal_updates = []  # (new_company_name, signal_id)

        for row in rows:
            row_id = row["id"]
            old_name = row["company_name"] or ""
            old_key = row["canonical_key"] or ""
            source_api = row["source_api"] or ""
            promoted_signal_id = row["promoted_signal_id"]

            # Parse raw_data
            raw_data_str = row["raw_data"]
            if isinstance(raw_data_str, str):
                try:
                    raw_data = json.loads(raw_data_str)
                except (json.JSONDecodeError, TypeError):
                    raw_data = {}
            elif isinstance(raw_data_str, dict):
                raw_data = raw_data_str
            else:
                raw_data = {}

            title = raw_data.get("title", "")
            description = raw_data.get("description", "")
            url = raw_data.get("url", "")

            # GitHub results keep their existing extraction (full_name is correct)
            if source_api == "github":
                continue

            # Parse HN prefix for HN-sourced results
            cleaned_title, hn_prefix = strip_hn_prefix(title)
            effective_title = cleaned_title if hn_prefix else title

            hn_name = None
            if hn_prefix in ("show", "launch", "demo"):
                hn_name = extract_name_from_hn_body(cleaned_title)

            # Recompute extraction
            info = extract_company_info(
                effective_title, description=description, url=url, mode="url_promote"
            )
            new_name = info.company_name or hn_name or ""

            # Compute canonical key
            new_key = ""
            if info.promoted_domain and not _is_blocked_domain(info.promoted_domain):
                new_key = f"domain:{normalize_domain(info.promoted_domain)}"
            elif new_name:
                new_key = f"name_loc:{new_name.lower()}"

            # Invariant: non-empty name requires non-empty key
            if new_name and not new_key:
                new_name = ""

            # Normalize Unknown
            if new_name.strip().lower() == "unknown":
                new_name = ""

            # Check if anything changed
            if new_name != old_name or new_key != old_key:
                updates.append((new_name, new_key, row_id))

                if promoted_signal_id:
                    signal_updates.append((new_name, promoted_signal_id))

                logger.info(
                    "Row %s: '%s' (%s) -> '%s' (%s)",
                    row_id,
                    old_name[:40],
                    old_key[:40],
                    new_name[:40],
                    new_key[:40],
                )

        logger.info(
            "Changes: %d/%d hunter_results, %d linked signals",
            len(updates),
            total_count,
            len(signal_updates),
        )

        if not apply:
            logger.info("DRY RUN — no changes written")
            return 0

        # Assertion: updated rows <= total rows
        if len(updates) > total_count:
            logger.error(
                "ASSERTION FAILED: updates (%d) > total rows (%d)",
                len(updates),
                total_count,
            )
            return 1

        # Apply in one transaction
        try:
            async with db.execute("BEGIN IMMEDIATE"):
                pass
        except Exception:
            await db.execute("BEGIN IMMEDIATE")

        try:
            for new_name, new_key, row_id in updates:
                await db.execute(
                    """
                    UPDATE hunter_results
                    SET company_name = ?, canonical_key = ?
                    WHERE id = ?
                    """,
                    (new_name, new_key, row_id),
                )

            for new_name, signal_id in signal_updates:
                await db.execute(
                    """
                    UPDATE signals
                    SET company_name = ?
                    WHERE id = ?
                    """,
                    (new_name, signal_id),
                )

            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("Transaction failed: %s", e)
            return 2

        # Post-check: total row count unchanged
        cursor = await db.execute("SELECT COUNT(*) FROM hunter_results")
        (post_count,) = await cursor.fetchone()
        if post_count != total_count:
            logger.error(
                "ASSERTION FAILED: row count changed %d -> %d",
                total_count,
                post_count,
            )
            return 1

        # Deterministic consistency: verify signal<->hunter name match
        if signal_updates:
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
                logger.error(
                    "ASSERTION FAILED: %d name mismatches between "
                    "hunter_results and signals",
                    len(mismatches),
                )
                return 1

        logger.info(
            "Backfill complete: %d rows updated, %d signals synced",
            len(updates),
            len(signal_updates),
        )
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Backfill hunter_results company names with shared extractor"
    )
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry-run)",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(backfill(args.db, apply=args.apply))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
