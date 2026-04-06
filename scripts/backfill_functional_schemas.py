"""Backfill functional schemas for existing signals.

Queries signals without active functional schemas, extracts via
FunctionalExtractor (Gemini), and saves the results.

Respects 15 RPM / 1500 RPD Gemini rate limits.

Usage:
    python scripts/backfill_functional_schemas.py [--db signals.db] [--limit 50] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from storage.signal_store import SignalStore
from consumer.functional_extractor import FunctionalExtractor, FunctionalSchema
from utils.db_path_helper import resolve_db_path_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Rate limit: 15 RPM → 4 seconds between requests (conservative)
MIN_INTERVAL_SECONDS = 4.0


async def find_signals_without_schemas(store: SignalStore, limit: int) -> list:
    """Find signals whose company_id has no active functional schema."""
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    cursor = await db.execute(
        """SELECT DISTINCT s.company_id, s.company_name, s.canonical_key, s.id,
                  s.raw_data
           FROM signals s
           WHERE s.company_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1 FROM functional_schemas fs
                 WHERE fs.company_id = s.company_id AND fs.is_active = 1
             )
           GROUP BY s.company_id
           ORDER BY s.detected_at DESC
           LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()

    results = []
    for row in rows:
        results.append({
            "company_id": row[0],
            "company_name": row[1],
            "canonical_key": row[2],
            "signal_id": row[3],
            "raw_data": row[4],
        })
    return results


async def backfill(
    db_path: str = "signals.db",
    limit: int = 50,
    dry_run: bool = True,
) -> dict:
    """Run the backfill process.

    Args:
        db_path: Path to signals.db
        limit: Max signals to process
        dry_run: If True, report only — no writes

    Returns:
        Report dict with counts and details.
    """
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        candidates = await find_signals_without_schemas(store, limit)
        logger.info("Found %d companies without functional schemas", len(candidates))

        if dry_run:
            logger.info("[DRY RUN] Would extract schemas for %d companies", len(candidates))
            for c in candidates:
                logger.info(
                    "  %s (%s) — signal %d",
                    c["company_name"], c["canonical_key"], c["signal_id"],
                )
            return {
                "mode": "dry_run",
                "candidates": len(candidates),
                "extracted": 0,
                "skipped": 0,
                "errors": 0,
            }

        # Check for API key
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.error("GOOGLE_API_KEY not set — cannot extract schemas")
            return {
                "mode": "live",
                "candidates": len(candidates),
                "extracted": 0,
                "skipped": 0,
                "errors": 1,
                "error": "GOOGLE_API_KEY not set",
            }

        extractor = FunctionalExtractor(api_key=api_key)

        extracted = 0
        skipped = 0
        errors = 0
        last_request_time = 0.0

        for i, candidate in enumerate(candidates):
            company_id = candidate["company_id"]

            # Double-check idempotency: skip if schema was created between query and now
            if await store.has_active_schema(company_id):
                logger.info("  Skipping %s — schema already exists", company_id)
                skipped += 1
                continue

            # Rate limiting
            elapsed = time.monotonic() - last_request_time
            if elapsed < MIN_INTERVAL_SECONDS and last_request_time > 0:
                wait = MIN_INTERVAL_SECONDS - elapsed
                logger.debug("  Rate limiting: waiting %.1fs", wait)
                await asyncio.sleep(wait)

            try:
                raw_data = json.loads(candidate["raw_data"]) if isinstance(candidate["raw_data"], str) else candidate["raw_data"]
                signal_data = {
                    "company_name": candidate["company_name"],
                    "canonical_key": candidate["canonical_key"],
                    **raw_data,
                }

                last_request_time = time.monotonic()
                schema = await extractor.extract(signal_data, company_id=company_id)

                if schema:
                    schema.evidence_signal_ids = [candidate["signal_id"]]
                    schema_id = await store.save_functional_schema(schema.to_storage_dict())
                    logger.info(
                        "  [%d/%d] Extracted schema for %s (id=%d, archetype=%s, confidence=%.2f)",
                        i + 1, len(candidates),
                        candidate["company_name"], schema_id,
                        schema.customer_archetype, schema.schema_confidence,
                    )
                    extracted += 1
                else:
                    logger.warning(
                        "  [%d/%d] No schema extracted for %s",
                        i + 1, len(candidates), candidate["company_name"],
                    )
                    skipped += 1

            except Exception as e:
                logger.error(
                    "  [%d/%d] Error extracting schema for %s: %s",
                    i + 1, len(candidates), candidate["company_name"], e,
                )
                errors += 1

        return {
            "mode": "live",
            "candidates": len(candidates),
            "extracted": extracted,
            "skipped": skipped,
            "errors": errors,
        }

    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill functional schemas for existing signals."
    )
    parser.add_argument(
        "--db", default=resolve_db_path_env(),
        help="Path to signals database (default: signals.db)",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Maximum companies to process (default: 50)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Report only, no writes",
    )
    args = parser.parse_args()

    result = asyncio.run(backfill(
        db_path=args.db,
        limit=args.limit,
        dry_run=args.dry_run,
    ))

    print("\n" + "=" * 60)
    print("BACKFILL FUNCTIONAL SCHEMAS — REPORT")
    print("=" * 60)
    print(f"Mode:       {result['mode']}")
    print(f"Candidates: {result['candidates']}")
    print(f"Extracted:  {result.get('extracted', 0)}")
    print(f"Skipped:    {result.get('skipped', 0)}")
    print(f"Errors:     {result.get('errors', 0)}")
    if result.get("error"):
        print(f"Error:      {result['error']}")
    print("=" * 60)


if __name__ == "__main__":
    # Windows console encoding fix
    if sys.platform == "win32":
        sys.stdout.reconfigure(errors='replace')
    main()
