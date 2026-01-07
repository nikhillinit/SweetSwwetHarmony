"""
BevNet/NOSH RSS Collector

Collects new product launch announcements from BevNet and NOSH RSS feeds.
These are industry publications covering beverage and natural food products.

Feeds:
- BevNet: bevnet.com/rss (beverage industry)
- NOSH: nosh.com/rss (natural/organic/specialty/healthy food)

No rate limiting required (standard RSS polling).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import aiohttp

from .base import ConsumerCollector, Signal

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

RSS_FEEDS = {
    "bevnet": "https://www.bevnet.com/rss/news.xml",
    "nosh": "https://www.nosh.com/feed/",
}

# Keywords indicating new product launch (vs. industry news)
LAUNCH_KEYWORDS = [
    "launch", "launches", "launching",
    "introduces", "introducing", "introduction",
    "unveil", "unveils", "unveiling",
    "debut", "debuts", "debuting",
    "new product", "new line", "new flavor",
    "announces", "announcing", "announcement",
    "releases", "releasing", "release",
    "rolls out", "rolling out",
]


# =============================================================================
# BEVNET COLLECTOR
# =============================================================================

class BevNetCollector(ConsumerCollector):
    """
    BevNet/NOSH RSS feed collector.

    Collects new product launch announcements from beverage and
    natural food industry publications.

    Usage:
        async with consumer_store("db.sqlite") as store:
            collector = BevNetCollector(store)
            result = await collector.run()
    """

    name = "bevnet"

    def __init__(
        self,
        store=None,
        include_nosh: bool = True,
    ):
        """
        Initialize BevNet collector.

        Args:
            store: ConsumerStore instance
            include_nosh: Also collect from NOSH feed
        """
        super().__init__(store)
        self.include_nosh = include_nosh
        self._session: Optional[aiohttp.ClientSession] = None

    async def collect(self) -> List[Signal]:
        """
        Collect signals from RSS feeds.

        Returns:
            List of Signal objects
        """
        signals = []

        async with aiohttp.ClientSession() as session:
            self._session = session

            # Collect from BevNet
            bevnet_signals = await self._collect_from_feed("bevnet", RSS_FEEDS["bevnet"])
            signals.extend(bevnet_signals)

            # Optionally collect from NOSH
            if self.include_nosh:
                nosh_signals = await self._collect_from_feed("nosh", RSS_FEEDS["nosh"])
                signals.extend(nosh_signals)

            logger.info(f"BevNet/NOSH: Collected {len(signals)} signals")

        return signals

    async def _collect_from_feed(
        self,
        feed_name: str,
        feed_url: str,
    ) -> List[Signal]:
        """Collect signals from a single RSS feed."""
        signals = []

        try:
            async with self._session.get(feed_url) as response:
                self.track_api_call()

                if response.status != 200:
                    logger.error(f"{feed_name} RSS error: {response.status}")
                    return []

                content = await response.text()
                items = self._parse_rss(content)

                for item in items:
                    # Filter for launch announcements
                    if self._is_launch_announcement(item):
                        signal = self._item_to_signal(feed_name, item)
                        signals.append(signal)

                logger.debug(f"{feed_name}: {len(items)} items, {len(signals)} launches")

        except Exception as e:
            logger.error(f"{feed_name} RSS fetch failed: {e}")

        return signals

    def _parse_rss(self, content: str) -> List[Dict[str, Any]]:
        """Parse RSS XML content."""
        items = []

        try:
            root = ET.fromstring(content)

            # Handle both RSS 2.0 and Atom formats
            # RSS 2.0: /rss/channel/item
            for item in root.findall(".//item"):
                items.append(self._parse_rss_item(item))

            # Atom: /feed/entry
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                items.append(self._parse_atom_entry(entry))

        except ET.ParseError as e:
            logger.error(f"RSS parse error: {e}")

        return items

    def _parse_rss_item(self, item: ET.Element) -> Dict[str, Any]:
        """Parse RSS 2.0 item element."""
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        description = item.findtext("description", "")
        pub_date = item.findtext("pubDate", "")
        guid = item.findtext("guid", link)

        return {
            "title": title,
            "link": link,
            "description": self._clean_html(description)[:500],
            "pub_date": pub_date,
            "guid": guid,
        }

    def _parse_atom_entry(self, entry: ET.Element) -> Dict[str, Any]:
        """Parse Atom entry element."""
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        title = entry.findtext("atom:title", "", ns)
        link_elem = entry.find("atom:link", ns)
        link = link_elem.get("href", "") if link_elem is not None else ""
        summary = entry.findtext("atom:summary", "", ns)
        published = entry.findtext("atom:published", "", ns)
        entry_id = entry.findtext("atom:id", link, ns)

        return {
            "title": title,
            "link": link,
            "description": self._clean_html(summary)[:500],
            "pub_date": published,
            "guid": entry_id,
        }

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"\s+", " ", clean)
        return clean.strip()

    def _is_launch_announcement(self, item: Dict[str, Any]) -> bool:
        """Check if item is a product launch announcement."""
        title = item.get("title", "").lower()
        description = item.get("description", "").lower()
        combined = f"{title} {description}"

        for keyword in LAUNCH_KEYWORDS:
            if keyword in combined:
                return True

        return False

    def _item_to_signal(
        self,
        feed_name: str,
        item: Dict[str, Any],
    ) -> Signal:
        """Convert RSS item to Signal."""
        guid = item.get("guid", "")
        title = item.get("title", "")
        link = item.get("link", "")
        description = item.get("description", "")

        # Generate stable source_id from GUID
        if guid:
            source_id = hashlib.sha256(guid.encode()).hexdigest()[:16]
        else:
            source_id = hashlib.sha256(f"{title}|{link}".encode()).hexdigest()[:16]

        # Extract company name (simple heuristic)
        company_name = self._extract_company_name(title)

        return Signal(
            source_api="bevnet_rss",
            source_id=source_id,
            signal_type="product_launch",
            title=title,
            url=link,
            source_context=description[:500],
            raw_metadata={
                "feed": feed_name,
                "guid": guid,
                "pub_date": item.get("pub_date", ""),
            },
            extracted_company_name=company_name,
        )

    def _extract_company_name(self, title: str) -> Optional[str]:
        """
        Extract company name from article title.

        Common patterns:
        - "Company Name Launches New Product"
        - "Company Name Introduces XYZ"
        - "Company Name Unveils..."
        """
        # Try to extract text before launch keyword
        title_lower = title.lower()

        for keyword in LAUNCH_KEYWORDS:
            if keyword in title_lower:
                idx = title_lower.find(keyword)
                if idx > 0:
                    company = title[:idx].strip()
                    # Clean up common suffixes
                    for suffix in [" to", " will", " is", " has", " set to"]:
                        if company.lower().endswith(suffix):
                            company = company[:-len(suffix)].strip()
                    if len(company) > 2:
                        return company

        return None
