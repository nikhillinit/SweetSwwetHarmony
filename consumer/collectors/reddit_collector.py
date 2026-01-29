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

Features:
- Consumer keyword filtering
- Launch post detection
- Sentiment analysis (title only - compliance safe)
- Integration with community sentiment storage
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import aiohttp

from .base import ConsumerCollector, Signal

if TYPE_CHECKING:
    from utils.community_sentiment import CommunitySentimentAnalyzer, SentimentResult

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

    Features:
    - Consumer keyword filtering (CPG, Health Tech, Travel, Marketplaces)
    - Launch post detection (self-promotion indicators)
    - Sentiment analysis on TITLES ONLY (compliance-safe)
    - Integration with community sentiment storage

    Usage:
        async with consumer_store("db.sqlite") as store:
            collector = RedditCollector(store, enable_sentiment=True)
            result = await collector.run()
    """

    name = "reddit"

    def __init__(
        self,
        store=None,
        subreddits: Optional[List[str]] = None,
        posts_per_subreddit: int = 25,
        enable_sentiment: bool = True,
    ):
        """
        Initialize Reddit collector.

        Args:
            store: ConsumerStore instance
            subreddits: List of subreddits to monitor
            posts_per_subreddit: Max posts to fetch per subreddit
            enable_sentiment: Whether to analyze sentiment (titles only)
        """
        super().__init__(store)
        self.subreddits = subreddits or TARGET_SUBREDDITS
        self.posts_per_subreddit = posts_per_subreddit
        self.enable_sentiment = enable_sentiment
        self._session: Optional[aiohttp.ClientSession] = None
        self._sentiment_analyzer: Optional["CommunitySentimentAnalyzer"] = None

        # Lazy-load sentiment analyzer
        if self.enable_sentiment:
            self._init_sentiment_analyzer()

    def _init_sentiment_analyzer(self) -> None:
        """Initialize sentiment analyzer (lazy load to avoid import overhead)."""
        try:
            from utils.community_sentiment import CommunitySentimentAnalyzer, SentimentConfig

            # Use heuristic only for speed (no Ollama dependency)
            config = SentimentConfig(use_ollama_if_available=False)
            self._sentiment_analyzer = CommunitySentimentAnalyzer(config)
            logger.debug("Reddit collector: Sentiment analysis enabled")
        except ImportError:
            logger.warning("Sentiment analyzer not available, skipping sentiment analysis")
            self.enable_sentiment = False

    def _analyze_title_sentiment(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Analyze sentiment of post title.

        COMPLIANCE: Only analyzes titles, NOT body content.

        Args:
            title: Post title text

        Returns:
            Dict with sentiment data or None if analysis disabled
        """
        if not self.enable_sentiment or not self._sentiment_analyzer:
            return None

        try:
            result = self._sentiment_analyzer.analyze_sync(title)
            return {
                "sentiment_score": result.score,
                "sentiment_label": result.label,
                "sentiment_method": result.method,
                "sentiment_keywords": result.keywords_found,
            }
        except Exception as e:
            logger.debug(f"Sentiment analysis failed: {e}")
            return None

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
        Sentiment analysis is performed on TITLE ONLY (compliance-safe).
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

        # Analyze sentiment (TITLE ONLY - compliance requirement)
        sentiment_data = self._analyze_title_sentiment(title)

        # Build minimal context (NO body text)
        context = f"Posted in r/{subreddit} by u/{author} ({score} upvotes)"

        # Build metadata dict
        raw_metadata: Dict[str, Any] = {
            "subreddit": subreddit,
            "author": author,
            "score": score,
            "num_comments": post.get("num_comments", 0),
            "created_utc": created_utc,
            "is_self": post.get("is_self", False),
            # IMPORTANT: Do NOT include selftext
        }

        # Add sentiment data if available
        if sentiment_data:
            raw_metadata["sentiment_score"] = sentiment_data["sentiment_score"]
            raw_metadata["sentiment_label"] = sentiment_data["sentiment_label"]
            raw_metadata["sentiment_method"] = sentiment_data["sentiment_method"]
            raw_metadata["sentiment_keywords"] = sentiment_data["sentiment_keywords"]

        return Signal(
            source_api="reddit",
            source_id=post_id,
            signal_type="mention",
            title=title[:200],  # Truncate long titles
            url=signal_url,
            source_context=context,  # NO body content
            raw_metadata=raw_metadata,
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

    def get_sentiment_summary(self, signals: List[Signal]) -> Dict[str, Any]:
        """
        Get aggregated sentiment summary from collected signals.

        Args:
            signals: List of Signal objects with sentiment data

        Returns:
            Dict with sentiment summary statistics
        """
        if not signals:
            return {
                "total": 0,
                "with_sentiment": 0,
                "positive": 0,
                "negative": 0,
                "neutral": 0,
                "avg_score": 0.0,
            }

        with_sentiment = 0
        positive = 0
        negative = 0
        neutral = 0
        total_score = 0.0

        for signal in signals:
            if signal.raw_metadata and "sentiment_label" in signal.raw_metadata:
                with_sentiment += 1
                label = signal.raw_metadata["sentiment_label"]
                score = signal.raw_metadata.get("sentiment_score", 0.0)

                if label == "positive":
                    positive += 1
                elif label == "negative":
                    negative += 1
                else:
                    neutral += 1

                total_score += score

        avg_score = total_score / with_sentiment if with_sentiment > 0 else 0.0

        return {
            "total": len(signals),
            "with_sentiment": with_sentiment,
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "avg_score": round(avg_score, 3),
        }
