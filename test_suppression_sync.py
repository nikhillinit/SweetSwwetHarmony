"""
Test script for suppression sync job.

This script tests the SuppressionSync workflow in dry-run mode.

Usage:
    python test_suppression_sync.py
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from connectors.notion_connector_v2 import NotionConnector
from storage.signal_store import SignalStore
from workflows.suppression_sync import SuppressionSync

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Test suppression sync."""
    logger.info("Starting suppression sync test...")

    # Load environment variables
    load_dotenv()

    notion_api_key = os.environ.get("NOTION_API_KEY")
    notion_database_id = os.environ.get("NOTION_DATABASE_ID")

    if not notion_api_key or not notion_database_id:
        logger.error(
            "Missing required environment variables: "
            "NOTION_API_KEY, NOTION_DATABASE_ID"
        )
        sys.exit(1)

    # Initialize connectors
    logger.info("Initializing connectors...")
    notion = NotionConnector(
        api_key=notion_api_key,
        database_id=notion_database_id,
    )

    # Use test database
    store = SignalStore(
        db_path="test_suppression_sync.db",
        suppression_ttl_days=7,
    )
    await store.initialize()

    try:
        # Run sync in dry-run mode
        logger.info("Running suppression sync (dry-run)...")
        sync = SuppressionSync(notion, store, ttl_days=7)
        stats = await sync.sync(dry_run=True)

        # Print results
        print("\n" + "=" * 80)
        print("SUPPRESSION SYNC TEST RESULTS")
        print("=" * 80)
        print(f"Status: {'SUCCESS' if not stats.errors else 'FAILED'}")
        print(f"Duration: {stats.duration_seconds:.2f}s")
        print(f"Pages fetched from Notion: {stats.notion_pages_fetched}")
        print(f"Entries processed: {stats.entries_processed}")
        print(f"Entries with canonical key: {stats.entries_with_canonical_key}")
        print(f"Entries without canonical key: {stats.entries_without_canonical_key}")
        print(f"Strong keys: {stats.entries_with_strong_key}")
        print(f"Weak keys: {stats.entries_with_weak_key}")

        if stats.errors:
            print(f"\nErrors encountered: {len(stats.errors)}")
            for i, err in enumerate(stats.errors[:5], 1):
                print(f"  {i}. {err}")

        print("=" * 80)

        # Now run actual sync to test database update
        logger.info("\nRunning suppression sync (live to test DB)...")
        stats = await sync.sync(dry_run=False)

        print("\n" + "=" * 80)
        print("LIVE SYNC RESULTS")
        print("=" * 80)
        print(f"Entries synced to cache: {stats.entries_synced}")
        print(f"Expired entries cleaned: {stats.entries_expired_cleaned}")
        print("=" * 80)

        # Query a few entries to verify
        logger.info("\nQuerying sample entries from cache...")
        db_stats = await store.get_stats()
        print(f"\nCache contains {db_stats['active_suppression_entries']} active entries")

        if not stats.errors:
            logger.info("\n✅ All tests passed!")
            sys.exit(0)
        else:
            logger.error(f"\n❌ Tests completed with {len(stats.errors)} errors")
            sys.exit(1)

    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
