"""
News API Collector - Discover startups via news mentions and funding announcements.

when_to_use: When looking for consumer startups with recent press coverage,
  funding announcements, or product launches in authoritative news sources.

API: GNews API (https://gnews.io)
Cost: Free tier (100 requests/day)
Signal Strength: MEDIUM-HIGH (0.5-0.8)

News signals indicate:
1. Funding announcements (strongest signal)
2. Product launches
3. Market visibility and PR activity
4. Industry trend alignment

Aligned with Press On Ventures Consumer Thesis:
- CPG (food, beverage, beauty)
- Health Tech (fitness, wellness, mental health)
- Travel & Hospitality
- Consumer Marketplaces

Usage:
    # Mode 1: Search for consumer-relevant news
    collector = NewsAPICollector(api_key="your_key")
    result = await collector.run(dry_run=True)

    # Mode 2: Custom keywords
    collector = NewsAPICollector(api_key="your_key", keywords=["fintech", "banking"])
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# GNews API endpoint
GNEWS_API_URL = "https://gnews.io/api/v4/search"

# =============================================================================
# THESIS-ALIGNED CONSUMER KEYWORDS
# =============================================================================

# Consumer CPG: Food, beverage, snacks, beauty, personal care
CONSUMER_CPG_KEYWORDS = [
    "meal delivery startup",
    "food tech startup",
    "beverage startup",
    "beauty startup",
    "skincare startup",
    "CPG startup",
    "D2C brand",
    "consumer brand funding",
]

# Consumer Health Tech: Fitness, wellness, mental health
CONSUMER_HEALTH_TECH_KEYWORDS = [
    "fitness app startup",
    "wellness startup",
    "mental health startup",
    "digital health startup",
    "telehealth startup",
    "health tech funding",
    "meditation app",
    "fitness startup",
]

# Travel & Hospitality
TRAVEL_HOSPITALITY_KEYWORDS = [
    "travel startup",
    "hospitality startup",
    "restaurant tech",
    "hotel tech startup",
    "travel booking startup",
]

# Consumer Marketplaces
CONSUMER_MARKETPLACE_KEYWORDS = [
    "consumer marketplace",
    "e-commerce startup",
    "delivery startup",
    "on-demand startup",
    "sharing economy startup",
]

# Combined default keywords
CONSUMER_KEYWORDS = (
    CONSUMER_CPG_KEYWORDS +
    CONSUMER_HEALTH_TECH_KEYWORDS +
    TRAVEL_HOSPITALITY_KEYWORDS +
    CONSUMER_MARKETPLACE_KEYWORDS
)

# Signal confidence levels
SIGNAL_CONFIDENCE = {
    "high": 0.75,
    "medium": 0.55,
    "low": 0.40,
}

# Authoritative news sources (higher confidence)
AUTHORITATIVE_SOURCES = [
    "techcrunch",
    "venturebeat",
    "forbes",
    "bloomberg",
    "reuters",
    "wall street journal",
    "wsj",
    "business insider",
    "cnbc",
    "the information",
    "axios",
    "crunchbase news",
    "pitchbook",
]

# Funding-related keywords
FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "series a", "series b", "seed round",
    "seed funding", "pre-seed", "investment", "investors", "valuation",
    "backed by", "led by", "round of",
]

# Product launch keywords
LAUNCH_KEYWORDS = [
    "launches", "launched", "announces", "announced", "unveils", "unveiled",
    "introduces", "introduced", "debuts", "new product", "release",
]


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class NewsArticle:
    """A news article from GNews API."""

    title: str
    description: str
    url: str
    source: str
    published_at: datetime
    image_url: Optional[str] = None
    content: Optional[str] = None

    @property
    def domain(self) -> str:
        """Extract domain from article URL."""
        if not self.url:
            return ""
        try:
            parsed = urlparse(self.url)
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""

    @property
    def age_days(self) -> int:
        """Age of article in days."""
        delta = datetime.now(timezone.utc) - self.published_at
        return max(0, delta.days)

    @property
    def is_funding_news(self) -> bool:
        """Check if this is funding-related news."""
        text = f"{self.title} {self.description}".lower()
        return any(kw in text for kw in FUNDING_KEYWORDS)

    @property
    def is_product_launch(self) -> bool:
        """Check if this is a product launch announcement."""
        text = f"{self.title} {self.description}".lower()
        return any(kw in text for kw in LAUNCH_KEYWORDS)

    def extract_company_name(self) -> Optional[str]:
        """
        Extract company name from article title.

        Patterns:
        - "CompanyName raises $X..."
        - "CompanyName announces..."
        - "CompanyName launches..."
        """
        patterns = [
            r"^([A-Z][a-zA-Z0-9]+)\s+raises",
            r"^([A-Z][a-zA-Z0-9]+)\s+announces",
            r"^([A-Z][a-zA-Z0-9]+)\s+launches",
            r"^([A-Z][a-zA-Z0-9]+)\s+unveils",
            r"^([A-Z][a-zA-Z0-9]+)\s+secures",
            r"^([A-Z][a-zA-Z0-9]+)\s+closes",
        ]

        for pattern in patterns:
            match = re.match(pattern, self.title)
            if match:
                company = match.group(1)
                # Filter common words
                if company.lower() not in ["the", "a", "an", "this", "new"]:
                    return company

        return None


# =============================================================================
# NEWS API COLLECTOR
# =============================================================================

class NewsAPICollector(BaseCollector):
    """
    Collector for news articles via GNews API.

    Fetches consumer-relevant news articles and converts them to signals
    for the discovery pipeline.
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        api_key: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        max_results: int = 100,
        lookback_days: int = 7,
        language: str = "en",
        country: str = "us",
    ):
        """
        Initialize the News API collector.

        Args:
            store: Optional SignalStore for persistence
            api_key: GNews API key (or set GNEWS_API_KEY env var)
            keywords: Custom keywords to search (default: thesis-aligned)
            max_results: Maximum results per keyword (default: 100)
            lookback_days: How far back to search (default: 7 days)
            language: Article language (default: en)
            country: Article country (default: us)
        """
        super().__init__(
            store=store,
            collector_name="news_api",
            retry_config=RetryConfig(max_retries=3, backoff_base=2.0),
            api_name="gnews",
        )

        # API key from param or environment
        self._api_key = api_key or os.getenv("GNEWS_API_KEY")
        self._api_key_available = bool(self._api_key)

        if not self._api_key_available:
            logger.warning(
                "No GNews API key provided. Set GNEWS_API_KEY environment variable "
                "or pass api_key parameter. Collector will return empty results."
            )

        # Search configuration
        self.keywords = keywords or CONSUMER_KEYWORDS
        self.max_results = max_results
        self.lookback_days = lookback_days
        self.language = language
        self.country = country

        # Track processed URLs to avoid duplicates
        self._processed_urls: set[str] = set()

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from GNews API.

        Returns:
            List of Signal objects from news articles
        """
        if not self._api_key_available:
            logger.info("No API key available, returning empty results")
            return []

        signals = []
        articles_found = 0

        # Search for each keyword group
        for keyword in self.keywords[:10]:  # Limit to conserve API quota
            try:
                articles = await self._search_news(keyword)
                articles_found += len(articles)

                for article in articles:
                    # Skip if already processed
                    if article.url in self._processed_urls:
                        continue
                    self._processed_urls.add(article.url)

                    # Check consumer relevance
                    if not self._is_consumer_relevant(article):
                        continue

                    # Convert to signal
                    signal = self._article_to_signal(article)
                    signals.append(signal)

            except Exception as e:
                logger.warning(f"Error searching for '{keyword}': {e}")
                continue

        logger.info(f"Found {articles_found} articles, {len(signals)} relevant signals")
        return signals

    async def _search_news(self, query: str) -> List[NewsArticle]:
        """
        Search GNews API for articles matching query.

        Args:
            query: Search query string

        Returns:
            List of NewsArticle objects
        """
        # Calculate date range
        from_date = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "q": query,
            "token": self._api_key,
            "lang": self.language,
            "country": self.country,
            "max": min(self.max_results, 100),  # API max is 100
            "from": from_date,
        }

        async def fetch():
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(GNEWS_API_URL, params=params)
                response.raise_for_status()
                return response.json()

        data = await self._fetch_with_retry(fetch)

        articles = []
        for item in data.get("articles", []):
            try:
                # Parse published date
                published_str = item.get("publishedAt", "")
                if published_str:
                    published_at = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                else:
                    published_at = datetime.now(timezone.utc)

                article = NewsArticle(
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    url=item.get("url", ""),
                    source=item.get("source", {}).get("name", "Unknown"),
                    published_at=published_at,
                    image_url=item.get("image"),
                    content=item.get("content"),
                )
                articles.append(article)
            except Exception as e:
                logger.warning(f"Error parsing article: {e}")
                continue

        return articles

    def _is_consumer_relevant(self, article: NewsArticle) -> bool:
        """
        Check if article is relevant to consumer thesis.

        Args:
            article: NewsArticle to check

        Returns:
            True if article is consumer-relevant
        """
        text = f"{article.title} {article.description}".lower()

        # Check for consumer keywords
        consumer_terms = [
            # CPG
            "food", "beverage", "meal", "snack", "beauty", "skincare",
            "cosmetics", "cpg", "d2c", "dtc", "consumer brand",
            # Health Tech
            "fitness", "wellness", "mental health", "meditation", "sleep",
            "health app", "digital health", "telehealth", "wearable",
            # Travel
            "travel", "hospitality", "hotel", "restaurant", "booking",
            # Marketplace
            "marketplace", "e-commerce", "delivery", "on-demand",
        ]

        return any(term in text for term in consumer_terms)

    def _classify_signal_type(self, article: NewsArticle) -> str:
        """
        Classify the signal type based on article content.

        Args:
            article: NewsArticle to classify

        Returns:
            Signal type string
        """
        if article.is_funding_news:
            return "funding_announcement"
        elif article.is_product_launch:
            return "product_launch"
        else:
            return "news_mention"

    def _calculate_confidence(self, article: NewsArticle) -> float:
        """
        Calculate confidence score for article.

        Factors:
        - Authoritative source (+0.15)
        - Funding news (+0.10)
        - Product launch (+0.05)
        - Freshness (decay over time)

        Args:
            article: NewsArticle to score

        Returns:
            Confidence score (0.0 to 0.95)
        """
        base = SIGNAL_CONFIDENCE["low"]

        # Authoritative source boost
        if self._is_authoritative_source(article.source):
            base += 0.15

        # Funding news boost
        if article.is_funding_news:
            base += 0.10

        # Product launch boost
        if article.is_product_launch:
            base += 0.05

        # Freshness boost (newer = better)
        if article.age_days <= 1:
            base += 0.10
        elif article.age_days <= 3:
            base += 0.05
        elif article.age_days > 7:
            base -= 0.05  # Slight penalty for old news

        return min(max(base, 0.0), 0.95)

    def _is_authoritative_source(self, source: str) -> bool:
        """
        Check if source is an authoritative news outlet.

        Args:
            source: Source name

        Returns:
            True if authoritative
        """
        source_lower = source.lower()
        return any(auth in source_lower for auth in AUTHORITATIVE_SOURCES)

    def _extract_company_name(self, article: NewsArticle) -> Optional[str]:
        """
        Extract company name from article.

        Args:
            article: NewsArticle to extract from

        Returns:
            Company name or None
        """
        return article.extract_company_name()

    def _article_to_signal(self, article: NewsArticle) -> Signal:
        """
        Convert NewsArticle to Signal object.

        Args:
            article: NewsArticle to convert

        Returns:
            Signal object
        """
        signal_type = self._classify_signal_type(article)
        confidence = self._calculate_confidence(article)
        company_name = self._extract_company_name(article)

        # Build canonical key candidates
        canonical_keys = []
        if company_name:
            canonical_keys.append(f"name:{company_name.lower()}")
        if article.domain and article.domain not in ["techcrunch.com", "venturebeat.com", "forbes.com"]:
            # Don't use news site domains as canonical keys
            canonical_keys.append(f"domain:{article.domain}")

        # Create unique signal ID
        import hashlib
        url_hash = hashlib.md5(article.url.encode()).hexdigest()[:12]
        signal_id = f"news_{url_hash}"

        return Signal(
            id=signal_id,
            signal_type=signal_type,
            confidence=confidence,
            source_api="news_api",
            source_url=article.url,
            detected_at=article.published_at,
            raw_data={
                "title": article.title,
                "description": article.description,
                "url": article.url,
                "source": article.source,
                "published_at": article.published_at.isoformat(),
                "company_name": company_name,
                "is_funding_news": article.is_funding_news,
                "is_product_launch": article.is_product_launch,
                "canonical_key_candidates": canonical_keys,
            },
        )


# =============================================================================
# MOCK COLLECTOR FOR TESTING
# =============================================================================

class MockNewsAPICollector(NewsAPICollector):
    """
    Mock News API collector for testing without API credentials.

    Returns sample consumer-relevant news articles.
    """

    def __init__(self, store: Optional[SignalStore] = None):
        super().__init__(store=store, api_key="mock_key")
        self._api_key_available = True  # Pretend we have a key

    async def _collect_signals(self) -> List[Signal]:
        """Return mock signals for testing."""
        mock_articles = [
            NewsArticle(
                title="HealthyMeals raises $10M Series A for meal delivery",
                description="Consumer food startup expands nationwide with fresh funding round led by Sequoia.",
                url="https://techcrunch.com/2024/01/15/healthymeals-series-a",
                source="TechCrunch",
                published_at=datetime.now(timezone.utc) - timedelta(hours=12),
            ),
            NewsArticle(
                title="FitTrack launches new fitness wearable with AI coaching",
                description="Digital health company unveils next-gen fitness tracker with personalized workout recommendations.",
                url="https://venturebeat.com/2024/01/14/fittrack-launch",
                source="VentureBeat",
                published_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
            NewsArticle(
                title="TravelNow announces hotel booking platform expansion",
                description="Travel startup expands to 50 new markets with hospitality tech platform.",
                url="https://forbes.com/2024/01/13/travelnow-expansion",
                source="Forbes",
                published_at=datetime.now(timezone.utc) - timedelta(days=2),
            ),
            NewsArticle(
                title="BeautyBox secures $5M seed funding for D2C skincare",
                description="Consumer beauty brand raises seed round for direct-to-consumer expansion.",
                url="https://businessinsider.com/2024/01/12/beautybox-seed",
                source="Business Insider",
                published_at=datetime.now(timezone.utc) - timedelta(days=3),
            ),
        ]

        signals = []
        for article in mock_articles:
            if self._is_consumer_relevant(article):
                signal = self._article_to_signal(article)
                signals.append(signal)

        return signals


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

if __name__ == "__main__":
    import asyncio

    async def main():
        # Use mock collector for demo
        collector = MockNewsAPICollector()
        result = await collector.run(dry_run=True)

        print("=" * 50)
        print("NEWS API COLLECTOR RESULTS")
        print("=" * 50)
        print(f"Status: {result.status.value}")
        print(f"Signals found: {result.signals_found}")
        print(f"New signals: {result.signals_new}")

    asyncio.run(main())
