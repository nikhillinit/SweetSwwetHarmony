"""
Suppression Cache Sync Job for Discovery Engine

Syncs Notion CRM entries to local SQLite suppression cache.

This job fetches all active prospects from Notion (excluding Passed/Lost)
and updates the local suppression cache to prevent duplicate pushes.

Features:
- Fetches all active prospects from Notion
- Extracts canonical keys (or builds from Website/domain)
- Handles missing canonical keys gracefully
- Bulk updates suppression_cache table
- Cleans expired entries
- Supports standalone or scheduled execution
- Comprehensive logging

Usage:
    # Run standalone sync
    python -m workflows.suppression_sync

    # Run with custom DB path
    python -m workflows.suppression_sync --db-path /path/to/signals.db

    # Run on interval (every 15 minutes)
    python -m workflows.suppression_sync --interval 900

    # Dry run (show what would be synced)
    python -m workflows.suppression_sync --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.signal_store import SignalStore, SuppressionEntry
from connectors.notion_connector_v2 import NotionConnector
from utils.canonical_keys import (
    build_canonical_key,
    normalize_domain,
    is_strong_key,
)

logger = logging.getLogger(__name__)


# =============================================================================
# SYNC STATISTICS
# =============================================================================

@dataclass
class SyncStats:
    """Statistics from a suppression cache sync run."""

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Notion fetch stats
    notion_pages_fetched: int = 0
    notion_errors: int = 0

    # Processing stats
    entries_processed: int = 0
    entries_with_canonical_key: int = 0
    entries_without_canonical_key: int = 0
    entries_with_strong_key: int = 0
    entries_with_weak_key: int = 0

    # Cache update stats
    entries_synced: int = 0
    entries_expired_cleaned: int = 0

    # Errors
    errors: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Duration of sync in seconds."""
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Convert stats to dictionary for logging/API."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "notion_pages_fetched": self.notion_pages_fetched,
            "notion_errors": self.notion_errors,
            "entries_processed": self.entries_processed,
            "entries_with_canonical_key": self.entries_with_canonical_key,
            "entries_without_canonical_key": self.entries_without_canonical_key,
            "entries_with_strong_key": self.entries_with_strong_key,
            "entries_with_weak_key": self.entries_with_weak_key,
            "entries_synced": self.entries_synced,
            "entries_expired_cleaned": self.entries_expired_cleaned,
            "errors_count": len(self.errors),
            "errors": self.errors[:10],  # Limit to first 10 errors
        }

    def log_summary(self) -> None:
        """Log a human-readable summary of the sync."""
        logger.info("=" * 80)
        logger.info("SUPPRESSION CACHE SYNC SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Started:  {self.started_at.isoformat()}")
        if self.completed_at:
            logger.info(f"Completed: {self.completed_at.isoformat()}")
            logger.info(f"Duration: {self.duration_seconds:.2f}s")
        logger.info("")
        logger.info("Notion Fetch:")
        logger.info(f"  Pages fetched: {self.notion_pages_fetched}")
        if self.notion_errors > 0:
            logger.warning(f"  Errors: {self.notion_errors}")
        logger.info("")
        logger.info("Processing:")
        logger.info(f"  Entries processed: {self.entries_processed}")
        logger.info(f"  With canonical key: {self.entries_with_canonical_key}")
        logger.info(f"  Without canonical key: {self.entries_without_canonical_key}")
        logger.info(f"  Strong keys (domain/CH/CB): {self.entries_with_strong_key}")
        logger.info(f"  Weak keys (name_loc/github): {self.entries_with_weak_key}")
        logger.info("")
        logger.info("Cache Update:")
        logger.info(f"  Entries synced: {self.entries_synced}")
        logger.info(f"  Expired entries cleaned: {self.entries_expired_cleaned}")

        if self.errors:
            logger.warning("")
            logger.warning(f"Errors encountered: {len(self.errors)}")
            for i, err in enumerate(self.errors[:5], 1):
                logger.warning(f"  {i}. {err}")
            if len(self.errors) > 5:
                logger.warning(f"  ... and {len(self.errors) - 5} more")

        logger.info("=" * 80)


# =============================================================================
# SUPPRESSION SYNC JOB
# =============================================================================

class SuppressionSync:
    """
    Syncs Notion CRM entries to local SQLite suppression cache.

    This job:
    1. Fetches all active prospects from Notion (not Passed/Lost)
    2. Extracts canonical_key, page_id, status, company_name
    3. Builds SuppressionEntry objects for each
    4. Bulk updates the suppression_cache table via SignalStore
    5. Cleans expired entries

    Features:
    - Handles missing canonical keys gracefully (builds from Website if available)
    - Configurable TTL (default 7 days)
    - Comprehensive stats and logging
    - Dry-run mode for testing
    """

    # Notion statuses to include in suppression cache
    # (Passed/Lost are excluded - we DO want to suppress those)
    # Actually, we want ALL statuses except deleted
    SYNC_STATUSES = [
        "Source",
        "Initial Meeting / Call",
        "Dilligence",  # Note the typo - matches Notion
        "Tracking",
        "Committed",
        "Funded",
        "Passed",
        "Lost",
    ]

    def __init__(
        self,
        notion_connector: NotionConnector,
        signal_store: SignalStore,
        ttl_days: int = 7,
    ):
        """
        Initialize suppression sync job.

        Args:
            notion_connector: Notion connector instance
            signal_store: Signal store instance
            ttl_days: How long to cache entries before re-checking (default: 7)
        """
        self.notion = notion_connector
        self.store = signal_store
        self.ttl_days = ttl_days
        self.stats = SyncStats()

    async def sync(self, dry_run: bool = False) -> SyncStats:
        """
        Run the suppression cache sync.

        Args:
            dry_run: If True, fetch and process but don't update cache

        Returns:
            SyncStats with detailed results
        """
        logger.info(
            f"Starting suppression cache sync "
            f"(TTL: {self.ttl_days} days, dry_run: {dry_run})"
        )

        self.stats = SyncStats()

        try:
            # Step 1: Fetch all pages from Notion
            pages = await self._fetch_notion_pages()
            self.stats.notion_pages_fetched = len(pages)

            # Step 2: Process pages into SuppressionEntry objects
            entries = await self._process_pages(pages)
            self.stats.entries_processed = len(entries)

            if dry_run:
                logger.info(f"DRY RUN: Would sync {len(entries)} entries to cache")
                self.stats.entries_synced = len(entries)
            else:
                # Step 3: Bulk update cache
                count = await self.store.update_suppression_cache(entries)
                self.stats.entries_synced = count

                # Step 4: Clean expired entries
                expired = await self.store.clean_expired_cache()
                self.stats.entries_expired_cleaned = expired

            self.stats.completed_at = datetime.now(timezone.utc)

        except Exception as e:
            logger.exception("Error during suppression cache sync")
            self.stats.errors.append(f"Fatal error: {str(e)}")
            self.stats.completed_at = datetime.now(timezone.utc)
            raise

        finally:
            # Always log summary
            self.stats.log_summary()

        return self.stats

    async def _fetch_notion_pages(self) -> List[Dict[str, Any]]:
        """
        Fetch all active prospect pages from Notion.

        Returns:
            List of Notion page objects
        """
        logger.info("Fetching pages from Notion...")

        try:
            # Use NotionConnector's internal method to query by statuses
            # This fetches ALL statuses in SYNC_STATUSES in a single OR query
            import httpx
            async with httpx.AsyncClient() as client:
                pages = await self.notion._query_by_statuses(client, self.SYNC_STATUSES)

            logger.info(f"Fetched {len(pages)} pages from Notion")
            return pages

        except Exception as e:
            logger.error(f"Error fetching from Notion: {e}")
            self.stats.notion_errors += 1
            raise

    async def _process_pages(self, pages: List[Dict[str, Any]]) -> List[SuppressionEntry]:
        """
        Process Notion pages into SuppressionEntry objects.

        Args:
            pages: List of Notion page objects

        Returns:
            List of SuppressionEntry objects
        """
        logger.info(f"Processing {len(pages)} pages...")

        entries: List[SuppressionEntry] = []

        for page in pages:
            try:
                entry = await self._process_page(page)
                if entry:
                    entries.append(entry)
            except Exception as e:
                # Log error but continue processing
                page_id = page.get("id", "unknown")
                logger.warning(f"Error processing page {page_id}: {e}")
                self.stats.errors.append(f"Page {page_id}: {str(e)}")
                continue

        logger.info(f"Processed {len(entries)} entries successfully")
        return entries

    async def _process_page(self, page: Dict[str, Any]) -> Optional[SuppressionEntry]:
        """
        Process a single Notion page into a SuppressionEntry.

        Args:
            page: Notion page object

        Returns:
            SuppressionEntry or None if page should be skipped
        """
        page_id = page["id"]
        props = page.get("properties", {})

        # Extract fields
        status = self._extract_select(props.get(self.notion.PROP_STATUS, {}))
        company_name = self._extract_title(props.get(self.notion.PROP_COMPANY_NAME, {}))
        canonical_key = self._extract_text(props.get(self.notion.PROP_CANONICAL_KEY, {}))
        website = props.get(self.notion.PROP_WEBSITE, {}).get("url", "")

        # Build canonical key if missing
        if not canonical_key and website:
            # Try to build from website
            domain = normalize_domain(website)
            if domain:
                canonical_key = f"domain:{domain}"
                logger.debug(f"Built canonical key from website: {canonical_key}")

        # If still no canonical key, try from company name (weak fallback)
        if not canonical_key and company_name:
            # Use name_loc fallback (not strong, but better than nothing)
            from utils.canonical_keys import _slug
            name_slug = _slug(company_name)
            if name_slug:
                canonical_key = f"name_loc:{name_slug}"
                logger.debug(f"Built weak canonical key from name: {canonical_key}")

        # Skip if we still can't build a canonical key
        if not canonical_key:
            logger.warning(
                f"Page {page_id} has no canonical key and cannot build one "
                f"(company: {company_name or 'unknown'})"
            )
            self.stats.entries_without_canonical_key += 1
            return None

        # Track key quality
        self.stats.entries_with_canonical_key += 1
        if is_strong_key(canonical_key):
            self.stats.entries_with_strong_key += 1
        else:
            self.stats.entries_with_weak_key += 1

        # Build expiration
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=self.ttl_days)

        # Create entry
        entry = SuppressionEntry(
            canonical_key=canonical_key,
            notion_page_id=page_id,
            status=status or "Unknown",
            company_name=company_name,
            cached_at=now,
            expires_at=expires_at,
            metadata={
                "website": website,
                "synced_from": "suppression_sync_job",
            }
        )

        return entry

    # Helper methods for extracting Notion properties

    @staticmethod
    def _extract_text(prop: Dict) -> Optional[str]:
        """Extract text from Notion rich_text property."""
        rich_text = prop.get("rich_text", [])
        if rich_text and len(rich_text) > 0:
            return rich_text[0].get("text", {}).get("content", "")
        return None

    @staticmethod
    def _extract_title(prop: Dict) -> str:
        """Extract text from Notion title property."""
        title = prop.get("title", [])
        if title and len(title) > 0:
            return title[0].get("text", {}).get("content", "")
        return ""

    @staticmethod
    def _extract_select(prop: Dict) -> Optional[str]:
        """Extract value from Notion select property."""
        select = prop.get("select")
        if select:
            return select.get("name")
        return None


# =============================================================================
# SCHEDULED SYNC
# =============================================================================

async def run_scheduled_sync(
    interval_seconds: int,
    notion_connector: NotionConnector,
    signal_store: SignalStore,
    ttl_days: int = 7,
) -> None:
    """
    Run suppression sync on a schedule.

    Args:
        interval_seconds: How often to sync (in seconds)
        notion_connector: Notion connector instance
        signal_store: Signal store instance
        ttl_days: Cache TTL in days
    """
    logger.info(
        f"Starting scheduled suppression sync "
        f"(interval: {interval_seconds}s, TTL: {ttl_days} days)"
    )

    while True:
        try:
            sync = SuppressionSync(notion_connector, signal_store, ttl_days)
            await sync.sync(dry_run=False)

        except Exception as e:
            logger.exception(f"Error in scheduled sync: {e}")

        # Wait for next run
        logger.info(f"Next sync in {interval_seconds} seconds...")
        await asyncio.sleep(interval_seconds)


# =============================================================================
# CLI
# =============================================================================

async def main():
    """CLI entry point for suppression sync."""
    parser = argparse.ArgumentParser(
        description="Sync Notion CRM entries to local suppression cache"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="signals.db",
        help="Path to SQLite database (default: signals.db)",
    )
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=7,
        help="Cache TTL in days (default: 7)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="Run on interval (seconds). If not set, runs once and exits.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and process but don't update cache",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Load environment variables
    from dotenv import load_dotenv
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

    store = SignalStore(
        db_path=args.db_path,
        suppression_ttl_days=args.ttl_days,
    )
    await store.initialize()

    try:
        if args.interval:
            # Run on schedule
            await run_scheduled_sync(
                interval_seconds=args.interval,
                notion_connector=notion,
                signal_store=store,
                ttl_days=args.ttl_days,
            )
        else:
            # Run once
            sync = SuppressionSync(notion, store, args.ttl_days)
            stats = await sync.sync(dry_run=args.dry_run)

            # Exit with error code if sync had errors
            if stats.errors:
                logger.error(f"Sync completed with {len(stats.errors)} errors")
                sys.exit(1)
            else:
                logger.info("Sync completed successfully")
                sys.exit(0)

    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
