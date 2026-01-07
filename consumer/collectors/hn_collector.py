"""
Hacker News Collector

Collects "Show HN" posts via Algolia API with consumer keyword filtering.

Endpoint: hn.algolia.com/api/v1/search_by_date
Rate limit: 10,000 requests/hour (no auth needed)

Consumer keywords trigger collection:
- Food, beverage, meal, recipe
- Fitness, wellness, health
- Travel, booking, hospitality
- Shopping, marketplace, delivery
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from .base import ConsumerCollector, Signal

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

HN_ALGOLIA_BASE_URL = "https://hn.algolia.com/api/v1"

# Consumer-related keywords for filtering Show HN posts
CONSUMER_KEYWORDS = [
    # CPG / Food & Bev
    "food", "beverage", "meal", "recipe", "cooking", "kitchen",
    "snack", "drink", "grocery", "organic", "vegan", "plant-based",

    # Health & Wellness
    "fitness", "workout", "exercise", "wellness", "meditation",
    "mental health", "sleep", "skincare", "beauty", "supplements",

    # Travel & Hospitality
    "travel", "booking", "hotel", "flight", "trip", "vacation",
    "restaurant", "cafe", "hospitality", "experiences",

    # Consumer Apps & Marketplaces
    "app for", "mobile app", "consumer", "lifestyle", "social",
    "dating", "entertainment", "shopping", "marketplace", "delivery",
    "subscription", "membership", "d2c", "dtc",
]

# Negative keywords to filter out non-consumer posts
NEGATIVE_KEYWORDS = [
    "api", "sdk", "devtool", "developer", "infrastructure",
    "kubernetes", "docker", "terraform", "enterprise", "b2b",
    "blockchain", "crypto", "nft", "web3",
]


# =============================================================================
# HN COLLECTOR
# =============================================================================

class HNCollector(ConsumerCollector):
    """
    Hacker News collector using Algolia API.

    Collects "Show HN" posts and filters for consumer relevance.

    Usage:
        async with consumer_store("db.sqlite") as store:
            collector = HNCollector(store)
            result = await collector.run()
    """

    name = "hn"

    def __init__(
        self,
        store=None,
        hours_lookback: int = 24,
        max_results: int = 100,
    ):
        """
        Initialize HN collector.

        Args:
            store: ConsumerStore instance
            hours_lookback: How many hours back to search
            max_results: Max results per query
        """
        super().__init__(store)
        self.hours_lookback = hours_lookback
        self.max_results = max_results
        self._session: Optional[aiohttp.ClientSession] = None

    async def collect(self) -> List[Signal]:
        """
        Collect Show HN posts from Algolia.

        Returns:
            List of Signal objects
        """
        signals = []

        async with aiohttp.ClientSession() as session:
            self._session = session

            # Search for "Show HN" posts
            posts = await self._search_show_hn()

            for post in posts:
                # Filter for consumer relevance
                if self._is_consumer_relevant(post):
                    signal = self._post_to_signal(post)
                    signals.append(signal)

            logger.info(f"HN: Found {len(posts)} Show HN posts, {len(signals)} consumer-relevant")

        return signals

    async def _search_show_hn(self) -> List[Dict[str, Any]]:
        """Search for Show HN posts via Algolia."""
        # Calculate time range
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=self.hours_lookback)
        since_ts = int(since.timestamp())

        # Build query - search for "Show HN" in title
        params = {
            "query": "Show HN",
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts}",
            "hitsPerPage": self.max_results,
        }

        try:
            url = f"{HN_ALGOLIA_BASE_URL}/search_by_date"
            async with self._session.get(url, params=params) as response:
                self.track_api_call()

                if response.status != 200:
                    logger.error(f"HN API error: {response.status}")
                    return []

                data = await response.json()
                return data.get("hits", [])

        except Exception as e:
            logger.error(f"HN API request failed: {e}")
            return []

    def _is_consumer_relevant(self, post: Dict[str, Any]) -> bool:
        """
        Check if post is consumer-relevant.

        Uses keyword matching on title.
        """
        title = post.get("title", "").lower()
        url = post.get("url", "").lower() if post.get("url") else ""
        combined = f"{title} {url}"

        # Check for negative keywords first
        for keyword in NEGATIVE_KEYWORDS:
            if keyword in combined:
                return False

        # Check for consumer keywords
        for keyword in CONSUMER_KEYWORDS:
            if keyword in combined:
                return True

        return False

    def _post_to_signal(self, post: Dict[str, Any]) -> Signal:
        """Convert HN post to Signal."""
        object_id = post.get("objectID", "")
        title = post.get("title", "")
        url = post.get("url", "")
        author = post.get("author", "")
        points = post.get("points", 0)
        created_at = post.get("created_at_i", 0)

        # Extract company name from title (simple heuristic)
        company_name = None
        if title.startswith("Show HN:"):
            # Remove "Show HN:" prefix
            rest = title[8:].strip()
            # Take first part before common separators
            for sep in [" - ", " – ", " — ", ": ", ", "]:
                if sep in rest:
                    company_name = rest.split(sep)[0].strip()
                    break
            if not company_name:
                company_name = rest[:50]

        # Build metadata
        raw_metadata = {
            "objectID": object_id,
            "author": author,
            "points": points,
            "num_comments": post.get("num_comments", 0),
            "created_at_i": created_at,
        }

        return Signal(
            source_api="hn",
            source_id=object_id,
            signal_type="show_hn",
            title=title,
            url=url or f"https://news.ycombinator.com/item?id={object_id}",
            source_context=f"Show HN post by {author} with {points} points",
            raw_metadata=raw_metadata,
            extracted_company_name=company_name,
            detected_at=datetime.fromtimestamp(created_at, tz=timezone.utc) if created_at else datetime.now(timezone.utc),
        )


# =============================================================================
# CONVENIENCE
# =============================================================================

async def collect_hn_signals(
    store=None,
    hours_lookback: int = 24,
) -> List[Signal]:
    """
    Convenience function to collect HN signals.

    Args:
        store: Optional ConsumerStore
        hours_lookback: Hours to look back

    Returns:
        List of Signal objects
    """
    collector = HNCollector(store, hours_lookback=hours_lookback)
    result = await collector.run()
    return []  # Signals are saved to store
