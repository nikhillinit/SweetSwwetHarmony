#!/usr/bin/env python3
"""
Shadow backfill: Safely generate shadow logs from existing signals.

This script processes existing signals in signals.db and generates
shadow_log entries for v1/v2 comparison WITHOUT:
- Mutating signal status
- Triggering Notion pushes
- Activating outbox work
- Calling process_pending()

Usage:
    python scripts/shadow_backfill.py --limit 600
    python scripts/shadow_backfill.py --db-path signals.db --limit 500 --min-text-len 20

Phase 0C-0: Parity Validation on Real Corpus
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.signal_store import SignalStore
from utils.thesis_matcher import ThesisMatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def extract_text(raw_data: dict, company_name: str | None) -> str:
    """Extract scorable text from signal raw_data.

    Pulls text from common description fields and combines with company name.

    Args:
        raw_data: Raw signal data dict
        company_name: Company name from signal

    Returns:
        Combined text for thesis matching
    """
    parts = []

    # Try multiple common description fields
    for field in [
        "description",
        "short_description",
        "about",
        "bio",
        "tagline",
        "summary",
        "overview",
    ]:
        value = raw_data.get(field)
        if value and isinstance(value, str):
            parts.append(value)

    # Also try category for additional context
    category = raw_data.get("category")
    if category and isinstance(category, str):
        parts.append(category)

    # Add company name for context
    if company_name:
        parts.append(company_name)

    return " ".join(parts)


async def backfill(
    db_path: str,
    limit: int = 500,
    min_text_len: int = 20,
    dry_run: bool = False,
) -> int:
    """Backfill shadow logs from existing signals.

    Reads signals from the database, runs thesis matching in shadow mode,
    and logs the v1/v2 comparison data to shadow_log table.

    Args:
        db_path: Path to signals.db
        limit: Max signals to process
        min_text_len: Skip signals with less text than this
        dry_run: If True, don't write to database

    Returns:
        Number of shadow logs created
    """
    store = SignalStore(db_path)
    await store.initialize()

    # Create matcher in shadow mode with v2 execution ENABLED
    # This is critical: shadow mode returns v1 but computes v2 and attaches diff
    matcher = ThesisMatcher(
        v2_enablement="shadow",
        v2_execution_enabled=True,
    )

    logger.info(
        f"Initialized ThesisMatcher: v2_enablement=shadow, "
        f"v2_execution_enabled=True, policy_hash={matcher._policy_hash[:8] if matcher._policy_hash else 'N/A'}"
    )

    # Query signals directly (no status filter - safe, read-only)
    async with store.transaction() as conn:
        cursor = await conn.execute(
            """
            SELECT id, canonical_key, company_name, raw_data
            FROM signals
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

    logger.info(f"Retrieved {len(rows)} signals from database")

    logged = 0
    skipped_no_text = 0
    skipped_no_v2_shadow = 0
    errors = 0

    for row in rows:
        signal_id, canonical_key, company_name, raw_data_json = row

        # Parse raw_data
        try:
            raw_data = json.loads(raw_data_json) if raw_data_json else {}
        except (json.JSONDecodeError, TypeError):
            errors += 1
            continue

        # Extract text for scoring
        text = extract_text(raw_data, company_name)
        if len(text) < min_text_len:
            skipped_no_text += 1
            continue

        # Run thesis matching in shadow mode
        try:
            fit = matcher.score(text, company_name=company_name)
        except Exception as e:
            logger.warning(f"Scoring failed for signal {signal_id}: {e}")
            errors += 1
            continue

        # Check if v2_shadow was generated
        if not fit.trace or not fit.trace.v2_shadow:
            skipped_no_v2_shadow += 1
            continue

        # Build computed_value dict for shadow log
        # shadow_report.py expects v2_shadow at top level of computed_value
        fit_dict = fit.to_dict()
        computed_value = {
            "keyword_score": fit.score,
            "keyword_category": fit.thesis.value,
            "v2_shadow": fit.trace.v2_shadow,
            # Include full fit_dict for debugging if needed
            "fit": fit_dict,
        }

        if dry_run:
            logger.debug(
                f"[DRY RUN] Would log: signal_id={signal_id}, "
                f"canonical_key={canonical_key}, "
                f"v1_score={fit.trace.v2_shadow['v1']['score']:.3f}, "
                f"v2_score={fit.trace.v2_shadow['v2']['score']:.3f}"
            )
        else:
            # Log to shadow_log table
            await store.log_shadow_computation(
                feature_name="thesis_match",
                canonical_key=canonical_key,
                computed_value=computed_value,
                signal_id=signal_id,
            )

        logged += 1

        # Progress logging
        if logged % 100 == 0:
            logger.info(f"Progress: {logged} shadow logs created...")

    await store.close()

    # Summary
    logger.info(
        f"Backfill complete: "
        f"logged={logged}, "
        f"skipped_no_text={skipped_no_text}, "
        f"skipped_no_v2_shadow={skipped_no_v2_shadow}, "
        f"errors={errors}"
    )

    return logged


def main():
    parser = argparse.ArgumentParser(
        description="Backfill shadow logs from existing signals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="signals.db",
        help="Path to signals database (default: signals.db)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum signals to process (default: 500)",
    )
    parser.add_argument(
        "--min-text-len",
        type=int,
        default=20,
        help="Minimum text length to process (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to database, just log what would be done",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    count = asyncio.run(
        backfill(
            db_path=args.db_path,
            limit=args.limit,
            min_text_len=args.min_text_len,
            dry_run=args.dry_run,
        )
    )

    print(f"\nCreated {count} shadow logs")

    if args.dry_run:
        print("(dry run - no data written)")


if __name__ == "__main__":
    main()
