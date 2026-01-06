"""
Suppression Sync - Sync Notion CRM to local suppression cache.

Fetches all companies from Notion and caches their canonical keys locally.
This prevents duplicate discovery of companies already in the CRM.

Usage:
    from workflows.suppression_sync import SuppressionSync
    from connectors.notion_connector_v2 import NotionConnector
    from storage.signal_store import SignalStore

    sync = SuppressionSync(
        notion_connector=connector,
        signal_store=store,
        ttl_days=7,
    )

    stats = await sync.sync(dry_run=False)
    print(f"Synced {stats.synced_count} entries")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SyncStats:
    """Statistics from a suppression sync run."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Counts
    pages_fetched: int = 0
    entries_synced: int = 0
    entries_skipped: int = 0
    entries_expired_cleared: int = 0
    errors: List[str] = field(default_factory=list)

    # Dry run flag
    dry_run: bool = True

    @property
    def duration_seconds(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "pages_fetched": self.pages_fetched,
            "entries_synced": self.entries_synced,
            "entries_skipped": self.entries_skipped,
            "entries_expired_cleared": self.entries_expired_cleared,
            "errors": self.errors[:10],  # Limit error list
            "error_count": len(self.errors),
            "dry_run": self.dry_run,
        }


class SuppressionSync:
    """
    Sync Notion CRM to local suppression cache.

    Workflow:
    1. Fetch all pages from Notion database
    2. Extract canonical keys from each page
    3. Add to local suppression cache with TTL
    4. Clear expired entries
    """

    def __init__(
        self,
        notion_connector: Any,  # NotionConnector - using Any to avoid circular import
        signal_store: Any,  # SignalStore - using Any to avoid circular import
        ttl_days: int = 7,
    ):
        """
        Args:
            notion_connector: NotionConnector instance
            signal_store: SignalStore instance
            ttl_days: Time-to-live for cache entries in days
        """
        self.notion = notion_connector
        self.store = signal_store
        self.ttl_days = ttl_days

    async def sync(self, dry_run: bool = True) -> SyncStats:
        """
        Run the suppression sync.

        Args:
            dry_run: If True, don't write to cache

        Returns:
            SyncStats with operation details
        """
        stats = SyncStats(dry_run=dry_run)

        logger.info(f"Starting suppression sync (dry_run={dry_run})")

        try:
            # Step 1: Fetch pages from Notion
            logger.info("Fetching pages from Notion...")
            pages = await self._fetch_all_pages()
            stats.pages_fetched = len(pages)
            logger.info(f"Fetched {len(pages)} pages from Notion")

            # Step 2: Process each page
            for page in pages:
                try:
                    await self._process_page(page, stats, dry_run)
                except Exception as e:
                    error_msg = f"Error processing page {page.get('id', 'unknown')}: {e}"
                    logger.warning(error_msg)
                    stats.errors.append(error_msg)

            # Step 3: Clear expired entries
            if not dry_run:
                cleared = await self.store.clear_expired_suppressions()
                stats.entries_expired_cleared = cleared
                logger.info(f"Cleared {cleared} expired entries")

            stats.completed_at = datetime.now(timezone.utc)

            logger.info(
                f"Suppression sync complete: "
                f"{stats.entries_synced} synced, "
                f"{stats.entries_skipped} skipped, "
                f"{len(stats.errors)} errors"
            )

        except Exception as e:
            logger.exception("Suppression sync failed")
            stats.errors.append(f"Sync failed: {e}")
            stats.completed_at = datetime.now(timezone.utc)

        return stats

    async def _fetch_all_pages(self) -> List[Dict[str, Any]]:
        """
        Fetch all pages from Notion database.

        Uses pagination to get all entries.
        """
        all_pages: List[Dict[str, Any]] = []

        try:
            # Use the notion connector's query method
            # This should handle pagination internally
            pages = await self.notion.query_database()
            all_pages.extend(pages)
        except Exception as e:
            logger.error(f"Error fetching pages: {e}")
            raise

        return all_pages

    async def _process_page(
        self,
        page: Dict[str, Any],
        stats: SyncStats,
        dry_run: bool
    ) -> None:
        """
        Process a single Notion page and add to suppression cache.
        """
        page_id = page.get("id", "")
        properties = page.get("properties", {})

        # Extract canonical key
        canonical_key = self._extract_canonical_key(properties)
        if not canonical_key:
            stats.entries_skipped += 1
            return

        # Extract status
        status = self._extract_status(properties)

        # Extract company name
        company_name = self._extract_company_name(properties)

        # Add to cache
        if not dry_run:
            await self.store.add_suppression(
                canonical_key=canonical_key,
                notion_page_id=page_id,
                notion_status=status,
                company_name=company_name,
                ttl_days=self.ttl_days,
            )

        stats.entries_synced += 1

    def _extract_canonical_key(self, properties: Dict[str, Any]) -> Optional[str]:
        """
        Extract canonical key from Notion properties.

        Priority:
        1. Canonical Key property (if exists)
        2. Build from Website/Domain
        """
        # Try explicit Canonical Key property
        canonical_prop = properties.get("Canonical Key", {})
        if canonical_prop.get("type") == "rich_text":
            texts = canonical_prop.get("rich_text", [])
            if texts:
                return texts[0].get("plain_text", "").strip()

        # Try building from website
        website_prop = properties.get("Website", {})
        if website_prop.get("type") == "url":
            url = website_prop.get("url", "")
            if url:
                # Extract domain from URL
                from urllib.parse import urlparse
                parsed = urlparse(url)
                domain = parsed.netloc.lower().replace("www.", "")
                if domain:
                    return f"domain:{domain}"

        return None

    def _extract_status(self, properties: Dict[str, Any]) -> str:
        """Extract status from Notion properties."""
        status_prop = properties.get("Status", {})

        if status_prop.get("type") == "status":
            status_obj = status_prop.get("status", {})
            return status_obj.get("name", "Unknown")
        elif status_prop.get("type") == "select":
            select_obj = status_prop.get("select", {})
            return select_obj.get("name", "Unknown") if select_obj else "Unknown"

        return "Unknown"

    def _extract_company_name(self, properties: Dict[str, Any]) -> Optional[str]:
        """Extract company name from Notion properties."""
        # Try "Name" or "Company" property
        for prop_name in ["Name", "Company", "Company Name"]:
            prop = properties.get(prop_name, {})

            if prop.get("type") == "title":
                titles = prop.get("title", [])
                if titles:
                    return titles[0].get("plain_text", "").strip()
            elif prop.get("type") == "rich_text":
                texts = prop.get("rich_text", [])
                if texts:
                    return texts[0].get("plain_text", "").strip()

        return None
