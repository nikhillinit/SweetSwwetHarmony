"""
Reddit Collector (Links Only)

Collects startup/entrepreneur posts from relevant subreddits.

CRITICAL: Store links only, NO body/comments content.
This is a compliance requirement - we process-and-discard body text.

Subreddits:
- r/startups
- r/entrepreneur
- r/smallbusiness
- r/SideProject

Uses Reddit's public JSON API (no auth required for basic access).
Rate limit: 60 requests/minute for unauthenticated.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from .base import ConsumerCollector, Signal

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

REDDIT_BASE_URL = "https://www.reddit.com"

# Subreddits to monitor
TARGET_SUBREDDITS = [
    "startups",
    "entrepreneur",
    "smallbusiness",
    "SideProject",
]

# Consumer keywords for filtering
CONSUMER_KEYWORDS = [
    # CPG / Food & Bev
    "food", "beverage", "meal", "snack", "drink", "grocery",
    "organic", "vegan", "plant-based", "recipe",

    # Health & Wellness
    "fitness", "workout", "wellness", "meditation", "sleep",
    "skincare", "beauty", "supplements", "health app",

    # Travel & Hospitality
    "travel", "booking", "hotel", "restaurant", "hospitality",

    # Consumer Apps
    "app for", "mobile app", "consumer app", "lifestyle",
    "dating", "social", "shopping", "marketplace", "delivery",
    "subscription", "d2c", "dtc", "e-commerce",
]

# Keywords indicating self-promotion / launch posts (higher signal)
LAUNCH_INDICATORS = [
    "launched", "launching", "just launched",
    "built", "i built", "we built",
    "created", "i created", "we created",
    "introducing", "announcing",
    "show", "check out", "feedback",
]


# =============================================================================
# REDDIT COLLECTOR
# =============================================================================

class RedditCollector(ConsumerCollector):
    """
    Reddit collector for consumer startup signals.

    IMPORTANT: Stores links only, processes-and-discards body content.
    This is a compliance requirement.

    Usage:
        async with consumer_store("db.sqlite") as store:
            collector = RedditCollector(store)
            result = await collector.run()
    """

    name = "reddit"

    def __init__(
        self,
        store=None,
        subreddits: Optional[List[str]] = None,
        posts_per_subreddit: int = 25,
    ):
        """
        Initialize Reddit collector.

        Args:
            store: ConsumerStore instance
            subreddits: List of subreddits to monitor
            posts_per_subreddit: Max posts to fetch per subreddit
        """
        super().__init__(store)
        self.subreddits = subreddits or TARGET_SUBREDDITS
        self.posts_per_subreddit = posts_per_subreddit
        self._session: Optional[aiohttp.ClientSession] = None

    async def collect(self) -> List[Signal]:
        """
        Collect signals from Reddit.

        Returns:
            List of Signal objects (links only, no body content)
        """
        signals = []

        headers = {
            "User-Agent": "ConsumerDiscoveryBot/1.0 (educational purposes)"
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            self._session = session

            for subreddit in self.subreddits:
                try:
                    sub_signals = await self._collect_from_subreddit(subreddit)
                    signals.extend(sub_signals)

                    # Rate limiting: wait between subreddits
                    await asyncio.sleep(1.0)

                except Exception as e:
                    logger.error(f"Reddit r/{subreddit} collection failed: {e}")

            logger.info(f"Reddit: Collected {len(signals)} consumer signals")

        return signals

    async def _collect_from_subreddit(self, subreddit: str) -> List[Signal]:
        """Collect from a single subreddit."""
        signals = []

        url = f"{REDDIT_BASE_URL}/r/{subreddit}/new.json"
        params = {"limit": self.posts_per_subreddit}

        try:
            async with self._session.get(url, params=params) as response:
                self.track_api_call()

                if response.status == 429:
                    logger.warning("Reddit rate limited, backing off...")
                    await asyncio.sleep(60)
                    return []

                if response.status != 200:
                    logger.error(f"Reddit API error: {response.status}")
                    return []

                data = await response.json()
                posts = data.get("data", {}).get("children", [])

                for post_wrapper in posts:
                    post = post_wrapper.get("data", {})

                    # Filter for consumer relevance
                    # IMPORTANT: We check selftext for filtering but DO NOT store it
                    if self._is_consumer_relevant(post):
                        signal = self._post_to_signal(subreddit, post)
                        signals.append(signal)

                logger.debug(f"r/{subreddit}: {len(posts)} posts, {len(signals)} consumer-relevant")

        except Exception as e:
            logger.error(f"Reddit fetch failed for r/{subreddit}: {e}")

        return signals

    def _is_consumer_relevant(self, post: Dict[str, Any]) -> bool:
        """
        Check if post is consumer-relevant.

        Uses title and selftext for filtering, but selftext is NOT stored.
        """
        title = post.get("title", "").lower()
        selftext = post.get("selftext", "").lower()  # Used for filtering only
        combined = f"{title} {selftext}"

        # Check for consumer keywords
        has_consumer_keyword = any(kw in combined for kw in CONSUMER_KEYWORDS)

        # Check for launch indicators (higher signal)
        is_launch_post = any(kw in combined for kw in LAUNCH_INDICATORS)

        # Prioritize launch posts with consumer keywords
        return has_consumer_keyword and is_launch_post

    def _post_to_signal(self, subreddit: str, post: Dict[str, Any]) -> Signal:
        """
        Convert Reddit post to Signal.

        CRITICAL: We store links only. No selftext/body content.
        """
        post_id = post.get("id", "")
        title = post.get("title", "")
        url = post.get("url", "")
        author = post.get("author", "[deleted]")
        score = post.get("score", 0)
        created_utc = post.get("created_utc", 0)
        permalink = post.get("permalink", "")

        # Use external URL if it's a link post, otherwise Reddit permalink
        if url and not url.startswith(f"{REDDIT_BASE_URL}"):
            signal_url = url
        else:
            signal_url = f"{REDDIT_BASE_URL}{permalink}"

        # Extract company name from title (simple heuristic)
        company_name = self._extract_company_name(title)

        # Build minimal context (NO body text)
        context = f"Posted in r/{subreddit} by u/{author} ({score} upvotes)"

        return Signal(
            source_api="reddit",
            source_id=post_id,
            signal_type="mention",
            title=title[:200],  # Truncate long titles
            url=signal_url,
            source_context=context,  # NO body content
            raw_metadata={
                "subreddit": subreddit,
                "author": author,
                "score": score,
                "num_comments": post.get("num_comments", 0),
                "created_utc": created_utc,
                "is_self": post.get("is_self", False),
                # IMPORTANT: Do NOT include selftext
            },
            extracted_company_name=company_name,
            detected_at=datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else datetime.now(timezone.utc),
        )

    def _extract_company_name(self, title: str) -> Optional[str]:
        """
        Extract company/product name from post title.

        Common patterns:
        - "I built [Product Name] - ..."
        - "Just launched [Product Name]..."
        - "Introducing [Product Name]"
        """
        # Pattern: "I/We built/created [Name]"
        match = re.search(r"(?:i|we)\s+(?:built|created|made|launched)\s+([A-Z][a-zA-Z0-9\s]+)", title, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Clean up - take first few words
            words = name.split()[:3]
            return " ".join(words)

        # Pattern: "Introducing [Name]"
        match = re.search(r"introducing\s+([A-Z][a-zA-Z0-9\s]+)", title, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            words = name.split()[:3]
            return " ".join(words)

        return None
