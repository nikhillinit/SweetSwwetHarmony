"""
Example: Suppression Cache Sync

Shows how to use SuppressionSync in your own code.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

from connectors.notion_connector_v2 import NotionConnector
from storage.signal_store import SignalStore
from workflows.suppression_sync import SuppressionSync, run_scheduled_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def example_one_time_sync():
    """Example: Run sync once and exit."""
    logger.info("Example: One-time sync")

    # Load environment
    load_dotenv()

    # Initialize connectors
    notion = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    store = SignalStore(db_path="signals.db")
    await store.initialize()

    try:
        # Create sync job
        sync = SuppressionSync(
            notion_connector=notion,
            signal_store=store,
            ttl_days=7,
        )

        # Run sync
        stats = await sync.sync(dry_run=False)

        # Check results
        if stats.errors:
            logger.error(f"Sync completed with {len(stats.errors)} errors")
            for error in stats.errors[:5]:
                logger.error(f"  - {error}")
        else:
            logger.info(
                f"Sync successful: {stats.entries_synced} synced, "
                f"{stats.entries_expired_cleaned} expired cleaned"
            )

    finally:
        await store.close()


async def example_dry_run():
    """Example: Dry run to preview sync."""
    logger.info("Example: Dry run")

    load_dotenv()

    notion = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    store = SignalStore(db_path="signals.db")
    await store.initialize()

    try:
        sync = SuppressionSync(notion, store, ttl_days=7)

        # Dry run - fetch and process but don't update cache
        stats = await sync.sync(dry_run=True)

        logger.info(f"DRY RUN: Would sync {stats.entries_processed} entries")
        logger.info(f"  - With canonical key: {stats.entries_with_canonical_key}")
        logger.info(f"  - Without canonical key: {stats.entries_without_canonical_key}")
        logger.info(f"  - Strong keys: {stats.entries_with_strong_key}")
        logger.info(f"  - Weak keys: {stats.entries_with_weak_key}")

    finally:
        await store.close()


async def example_scheduled_sync():
    """Example: Run sync on schedule (every 15 minutes)."""
    logger.info("Example: Scheduled sync")

    load_dotenv()

    notion = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    store = SignalStore(db_path="signals.db")
    await store.initialize()

    try:
        # Run forever - sync every 15 minutes
        await run_scheduled_sync(
            interval_seconds=900,  # 15 minutes
            notion_connector=notion,
            signal_store=store,
            ttl_days=7,
        )

    finally:
        await store.close()


async def example_check_suppression():
    """Example: Check if a company is suppressed."""
    logger.info("Example: Check suppression")

    load_dotenv()

    store = SignalStore(db_path="signals.db")
    await store.initialize()

    try:
        # Check if domain is suppressed
        entry = await store.check_suppression("domain:acme.ai")

        if entry:
            logger.info(f"Company IS suppressed:")
            logger.info(f"  - Notion page: {entry.notion_page_id}")
            logger.info(f"  - Status: {entry.status}")
            logger.info(f"  - Company: {entry.company_name}")
            logger.info(f"  - Cached: {entry.cached_at}")
            logger.info(f"  - Expires: {entry.expires_at}")
        else:
            logger.info("Company NOT suppressed - proceed with discovery")

    finally:
        await store.close()


async def example_integration_with_discovery():
    """Example: Integrate suppression check with discovery pipeline."""
    logger.info("Example: Discovery integration")

    load_dotenv()

    notion = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    store = SignalStore(db_path="signals.db")
    await store.initialize()

    try:
        # Step 1: Sync suppression cache (run periodically)
        logger.info("Step 1: Sync suppression cache")
        sync = SuppressionSync(notion, store, ttl_days=7)
        await sync.sync(dry_run=False)

        # Step 2: During discovery, check suppression
        logger.info("Step 2: Check suppression for new signal")

        new_signal_canonical_key = "domain:newstartup.ai"

        suppressed = await store.check_suppression(new_signal_canonical_key)

        if suppressed:
            logger.info(
                f"SKIP: {new_signal_canonical_key} already in Notion "
                f"(status: {suppressed.status})"
            )
        else:
            logger.info(
                f"PROCEED: {new_signal_canonical_key} is new - "
                f"run verification and push to Notion"
            )
            # ... continue with verification gate, push to Notion, etc.

        # Step 3: After pushing to Notion, refresh cache
        logger.info("Step 3: Refresh cache after push")
        await sync.sync(dry_run=False)

    finally:
        await store.close()


if __name__ == "__main__":
    # Run different examples by uncommenting:

    # One-time sync
    asyncio.run(example_one_time_sync())

    # Dry run
    # asyncio.run(example_dry_run())

    # Scheduled sync (runs forever)
    # asyncio.run(example_scheduled_sync())

    # Check suppression
    # asyncio.run(example_check_suppression())

    # Full integration example
    # asyncio.run(example_integration_with_discovery())
