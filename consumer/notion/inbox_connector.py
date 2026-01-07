"""
Notion Inbox Connector

API client for interacting with Notion Discovery Inbox database.
Rate limited to 2 req/sec (Notion limit is 3/sec).

Database Properties:
- Name (Title): Company/signal name
- Status (Select): New, Reviewing, Approved, Rejected
- Rejection Reason (Select): not_consumer, wrong_category, too_early, too_late, insufficient_info, other
- Notes (Text): Free-form reviewer notes
- Source (Select): hn, reddit, bevnet, nosh, uspto_tm
- URL (URL): Link to original
- Thesis Score (Number): 0.0-1.0 from LLM classifier
- Category (Select): consumer_cpg, consumer_health_tech, travel, marketplace, other
- Signal ID (Number): Internal reference
- Created (Date): When added
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# RATE LIMITING
# =============================================================================

class RateLimiter:
    """Simple rate limiter for API calls."""

    def __init__(self, calls_per_second: float = 2.0):
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until rate limit allows another call."""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self.last_call = time.time()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class NotionPage:
    """Notion page data."""
    id: str
    status: str
    name: str
    source: Optional[str] = None
    url: Optional[str] = None
    thesis_score: Optional[float] = None
    category: Optional[str] = None
    signal_id: Optional[int] = None
    rejection_reason: Optional[str] = None
    notes: Optional[str] = None
    created_time: Optional[datetime] = None
    last_edited_time: Optional[datetime] = None


# =============================================================================
# NOTION INBOX CONNECTOR
# =============================================================================

class NotionInboxConnector:
    """
    Client for Notion Discovery Inbox database.

    Usage:
        connector = NotionInboxConnector()

        # Create a page
        page_id = await connector.create_page(
            name="Acme Foods",
            source="hn",
            url="https://example.com",
            thesis_score=0.85,
            category="consumer_cpg",
            signal_id=123
        )

        # Query pages by status
        pages = await connector.query_by_status("New")

        # Update status
        await connector.update_status(page_id, "Approved")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        rate_limit: float = 2.0,
    ):
        """
        Initialize connector.

        Args:
            api_key: Notion API key (defaults to NOTION_API_KEY env var)
            database_id: Notion database ID (defaults to NOTION_INBOX_DATABASE_ID env var)
            rate_limit: Max requests per second
        """
        self.api_key = api_key or os.environ.get("NOTION_API_KEY")
        self.database_id = database_id or os.environ.get("NOTION_INBOX_DATABASE_ID")

        if not self.api_key:
            raise ValueError("NOTION_API_KEY not set")
        if not self.database_id:
            raise ValueError("NOTION_INBOX_DATABASE_ID not set")

        self.rate_limiter = RateLimiter(rate_limit)
        self._client = None

    @property
    def client(self):
        """Lazy-load Notion client."""
        if self._client is None:
            try:
                from notion_client import Client
                self._client = Client(auth=self.api_key)
            except ImportError:
                raise ImportError("notion-client package required: pip install notion-client")
        return self._client

    async def create_page(
        self,
        name: str,
        source: str,
        signal_id: int,
        url: Optional[str] = None,
        thesis_score: Optional[float] = None,
        category: Optional[str] = None,
        rationale: Optional[str] = None,
        key_signals: Optional[List[str]] = None,
    ) -> str:
        """
        Create a new page in the Inbox database.

        Returns:
            Notion page ID
        """
        await self.rate_limiter.acquire()

        # Build properties
        properties: Dict[str, Any] = {
            "Name": {"title": [{"text": {"content": name[:100]}}]},
            "Status": {"select": {"name": "New"}},
            "Source": {"select": {"name": source}},
            "Signal ID": {"number": signal_id},
        }

        if url:
            properties["URL"] = {"url": url}
        if thesis_score is not None:
            properties["Thesis Score"] = {"number": round(thesis_score, 3)}
        if category:
            properties["Category"] = {"select": {"name": category}}

        # Build page content with rationale
        children = []
        if rationale:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Rationale: {rationale}"}}]
                }
            })
        if key_signals:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Key signals: {', '.join(key_signals)}"}}]
                }
            })

        try:
            response = self.client.pages.create(
                parent={"database_id": self.database_id},
                properties=properties,
                children=children if children else None,
            )
            page_id = response["id"]
            logger.info(f"Created Notion page: {page_id} for {name}")
            return page_id

        except Exception as e:
            logger.error(f"Failed to create Notion page: {e}")
            raise

    async def update_status(
        self,
        page_id: str,
        status: str,
    ) -> None:
        """Update page status."""
        await self.rate_limiter.acquire()

        try:
            self.client.pages.update(
                page_id=page_id,
                properties={"Status": {"select": {"name": status}}}
            )
            logger.debug(f"Updated page {page_id} status to {status}")
        except Exception as e:
            logger.error(f"Failed to update page status: {e}")
            raise

    async def query_by_status(
        self,
        status: str,
        limit: int = 100,
    ) -> List[NotionPage]:
        """
        Query pages by status.

        Args:
            status: Status to filter by (New, Reviewing, Approved, Rejected)
            limit: Max pages to return

        Returns:
            List of NotionPage objects
        """
        await self.rate_limiter.acquire()

        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": "Status",
                    "select": {"equals": status}
                },
                page_size=min(limit, 100),
            )
            return [self._parse_page(page) for page in response.get("results", [])]

        except Exception as e:
            logger.error(f"Failed to query Notion database: {e}")
            raise

    async def query_recently_modified(
        self,
        exclude_status: Optional[str] = "New",
        limit: int = 100,
    ) -> List[NotionPage]:
        """
        Query recently modified pages (for polling decisions).

        Args:
            exclude_status: Status to exclude (typically "New")
            limit: Max pages to return

        Returns:
            List of NotionPage objects sorted by last_edited_time
        """
        await self.rate_limiter.acquire()

        try:
            filter_obj = None
            if exclude_status:
                filter_obj = {
                    "property": "Status",
                    "select": {"does_not_equal": exclude_status}
                }

            response = self.client.databases.query(
                database_id=self.database_id,
                filter=filter_obj,
                sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
                page_size=min(limit, 100),
            )
            return [self._parse_page(page) for page in response.get("results", [])]

        except Exception as e:
            logger.error(f"Failed to query Notion database: {e}")
            raise

    async def get_page(self, page_id: str) -> Optional[NotionPage]:
        """Get a single page by ID."""
        await self.rate_limiter.acquire()

        try:
            response = self.client.pages.retrieve(page_id=page_id)
            return self._parse_page(response)
        except Exception as e:
            logger.error(f"Failed to get page {page_id}: {e}")
            return None

    async def page_exists(self, signal_id: int) -> Optional[str]:
        """
        Check if a page already exists for this signal ID.

        Returns:
            Page ID if exists, None otherwise
        """
        await self.rate_limiter.acquire()

        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": "Signal ID",
                    "number": {"equals": signal_id}
                },
                page_size=1,
            )
            results = response.get("results", [])
            if results:
                return results[0]["id"]
            return None

        except Exception as e:
            logger.error(f"Failed to check page existence: {e}")
            return None

    def _parse_page(self, page: Dict[str, Any]) -> NotionPage:
        """Parse Notion page response into NotionPage object."""
        props = page.get("properties", {})

        # Extract title
        name_prop = props.get("Name", {}).get("title", [])
        name = name_prop[0]["plain_text"] if name_prop else ""

        # Extract select properties
        status = props.get("Status", {}).get("select", {}).get("name", "")
        source = props.get("Source", {}).get("select", {}).get("name")
        category = props.get("Category", {}).get("select", {}).get("name")
        rejection_reason = props.get("Rejection Reason", {}).get("select", {}).get("name")

        # Extract other properties
        url = props.get("URL", {}).get("url")
        thesis_score = props.get("Thesis Score", {}).get("number")
        signal_id = props.get("Signal ID", {}).get("number")

        # Extract notes (rich text)
        notes_prop = props.get("Notes", {}).get("rich_text", [])
        notes = notes_prop[0]["plain_text"] if notes_prop else None

        # Parse timestamps
        created_time = None
        if page.get("created_time"):
            created_time = datetime.fromisoformat(page["created_time"].replace("Z", "+00:00"))

        last_edited_time = None
        if page.get("last_edited_time"):
            last_edited_time = datetime.fromisoformat(page["last_edited_time"].replace("Z", "+00:00"))

        return NotionPage(
            id=page["id"],
            status=status,
            name=name,
            source=source,
            url=url,
            thesis_score=thesis_score,
            category=category,
            signal_id=signal_id,
            rejection_reason=rejection_reason,
            notes=notes,
            created_time=created_time,
            last_edited_time=last_edited_time,
        )
