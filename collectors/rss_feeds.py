"""
RSS Feed Collector - Discover startups via RSS feeds from news sources.

when_to_use: When looking for consumer startups with recent press coverage
  from TechCrunch, PR Newswire, Product Hunt, and health tech sources.

API: RSS/Atom feeds (no authentication required)
Cost: FREE
Signal Strength: MEDIUM (0.4-0.7)

RSS signals indicate:
1. Press coverage and PR activity
2. Product launches (Product Hunt)
3. Funding announcements
4. Industry visibility

Aligned with Press On Ventures Consumer Thesis:
- CPG (food, beverage, beauty)
- Health Tech (fitness, wellness, mental health)
- Travel & Hospitality
- Consumer Marketplaces

Usage:
    # Mode 1: Default feeds (thesis-aligned)
    collector = RSSFeedCollector()
    result = await collector.run(dry_run=True)

    # Mode 2: Custom feeds
    collector = RSSFeedCollector(feeds=["https://example.com/feed.xml"])
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.canonical_keys import build_canonical_key_candidates, NEWS_PUBLISHER_DOMAINS
from utils.company_name_extractor import (
    extract_company_info,
    extract_via_regex,
    is_blocked_domain,
    warmup_ner,
    ExtractionResult,
)
from utils.hn_title import HN_SEP_RE
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# =============================================================================
# DEFAULT FEEDS (THESIS-ALIGNED)
# =============================================================================

# Feed categories for filtering
FEED_CATEGORIES = {
    "startup": [
        "https://techcrunch.com/category/startups/feed/",
        "https://www.producthunt.com/feed",
        "https://news.crunchbase.com/feed/",
    ],
    "health_tech": [
        "https://www.mobihealthnews.com/feed",
        "https://www.fiercehealthcare.com/rss/xml",
    ],
    "cpg": [
        "https://www.foodbusinessnews.net/rss",
        "https://www.beveragedaily.com/rss/news",
    ],
    "press_release": [
        "https://www.prnewswire.com/rss/consumer-products-retail-latest-news/consumer-products-retail-latest-news-list.rss",
        "https://www.globenewswire.com/RssFeed/subjectcode/12-Consumer%20Products/feedTitle/GlobeNewswire%20-%20Consumer%20Products",
    ],
    "general": [
        "https://venturebeat.com/category/business/feed/",
    ],
}

# All default feeds combined
DEFAULT_FEEDS = []
for category_feeds in FEED_CATEGORIES.values():
    DEFAULT_FEEDS.extend(category_feeds)

# Press release sources (for signal classification)
PRESS_RELEASE_SOURCES = [
    "prnewswire",
    "globenewswire",
    "businesswire",
    "pr newswire",
    "globe newswire",
]

# Authoritative news sources
AUTHORITATIVE_SOURCES = [
    "techcrunch",
    "venturebeat",
    "crunchbase",
    "product hunt",
    "producthunt",
]

# Funding keywords
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

# Consumer keywords for relevance filtering
CONSUMER_KEYWORDS = [
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


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class RSSArticle:
    """An article from an RSS feed."""

    title: str
    description: str
    url: str
    source_feed: str
    published_at: datetime
    author: Optional[str] = None
    categories: List[str] = field(default_factory=list)
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

    @property
    def is_press_release(self) -> bool:
        """Check if this is from a press release source."""
        source_lower = self.source_feed.lower()
        url_lower = self.url.lower()
        return any(
            pr in source_lower or pr in url_lower
            for pr in PRESS_RELEASE_SOURCES
        )

    def extract_company_name(self) -> Optional[str]:
        """
        Extract company name from article title.

        Delegates to shared extract_via_regex() for identical behavior.
        Kept as compatibility wrapper so existing tests pass unchanged.
        """
        return extract_via_regex(self.title)


# =============================================================================
# PRESS-RELEASE TITLE-SEGMENT HEURISTIC
# =============================================================================

# Extend HN_SEP_RE with colon for press releases (separate regex to avoid HN regression)
_RSS_TITLE_SEP_RE = re.compile(
    HN_SEP_RE.pattern + r"|\s*:\s*"
)

_PR_BOILERPLATE = frozenset({
    "breaking", "exclusive", "update", "alert", "report",
    "press release", "funding update", "pr newswire",
    "business wire", "globe newswire", "news release",
})

_FIRST_TOKEN_STOP = frozenset({
    "the", "a", "an", "new", "how", "why", "what", "when",
    "breaking", "exclusive", "update", "alert",
})

_QUARTER_TICKER_RE = re.compile(r"^(Q[1-4]\b|FY\d|[A-Z]{1,5}:\s)")


def _extract_press_release_prefix(title: str) -> Optional[str]:
    """Extract company name from press-release title prefix before delimiter."""
    if not title or not title.strip():
        return None

    m = _RSS_TITLE_SEP_RE.search(title)
    if not m:
        return None

    segment = title[:m.start()].strip().strip("\"'")

    # Length guardrails
    if len(segment) < 2 or len(segment) > 35:
        return None

    tokens = segment.split()
    if not (1 <= len(tokens) <= 4):
        return None

    # Reject if first token is stopword
    if tokens[0].lower() in _FIRST_TOKEN_STOP:
        return None

    # Reject PR boilerplate
    if segment.lower() in _PR_BOILERPLATE:
        return None

    # Reject quarter/ticker patterns (pre-split: "Q4 2026", "FY2026", "AAPL:")
    if _QUARTER_TICKER_RE.match(segment):
        return None

    # Reject likely stock tickers: single all-uppercase alphabetic token, 1-5 chars
    if len(tokens) == 1 and segment.isalpha() and segment.isupper() and len(segment) <= 5:
        return None

    # Must contain at least one letter
    if not any(c.isalpha() for c in segment):
        return None

    return segment


# =============================================================================
# TITLE PRE-NORMALIZATION
# =============================================================================


def _normalize_title(raw: str) -> str:
    """Unescape HTML entities, strip trademark symbols, normalize whitespace."""
    t = html_lib.unescape(raw)          # &amp; → &, &#39; → '
    t = re.sub(r'[®™©℠]', '', t)       # strip to empty string (not space)
    t = t.replace('\u2018', "'").replace('\u2019', "'")  # smart quotes → ASCII
    t = re.sub(r'\s+', ' ', t).strip()  # collapse whitespace
    return t


# =============================================================================
# RSS FEED COLLECTOR
# =============================================================================

class RSSFeedCollector(BaseCollector):
    """
    Collector for RSS feeds from news sources and press releases.

    Fetches consumer-relevant articles from configured RSS feeds
    and converts them to signals for the discovery pipeline.
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        feeds: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        max_articles_per_feed: int = 20,
        lookback_days: int = 7,
        http: Optional[Any] = None,
        asset_store: Optional[Any] = None,
    ):
        """
        Initialize the RSS feed collector.

        Args:
            store: Optional SignalStore for persistence
            feeds: Custom list of RSS feed URLs
            categories: Feed categories to include (startup, health_tech, cpg, etc.)
            max_articles_per_feed: Maximum articles per feed (default: 20)
            lookback_days: How far back to include articles (default: 7 days)
            http: Optional shared CollectorHttpClient for connection pooling
            asset_store: Optional SourceAssetStore for change detection
        """
        super().__init__(
            store=store,
            collector_name="rss_feeds",
            retry_config=RetryConfig(max_retries=2, backoff_base=1.0),
            api_name="rss",
            http=http,
            asset_store=asset_store,
        )

        # Determine feeds to use
        if feeds:
            self.feeds = feeds
        elif categories:
            self.feeds = []
            for cat in categories:
                if cat in FEED_CATEGORIES:
                    self.feeds.extend(FEED_CATEGORIES[cat])
        else:
            self.feeds = DEFAULT_FEEDS

        self.max_articles_per_feed = max_articles_per_feed
        self.lookback_days = lookback_days

        # Track processed URLs to avoid duplicates
        self._processed_urls: set[str] = set()

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from RSS feeds.

        Returns:
            List of Signal objects from feed articles
        """
        # Pre-load NER model if mode requires it
        from utils.company_name_extractor import _get_extraction_mode
        if _get_extraction_mode() == "ner_active":
            warmup_ner()

        signals = []
        articles_found = 0

        for feed_url in self.feeds:
            try:
                articles = await self._parse_feed(feed_url)
                articles_found += len(articles)

                for article in articles:
                    # Skip if already processed
                    if article.url in self._processed_urls:
                        continue
                    self._processed_urls.add(article.url)

                    # Skip old articles
                    if article.age_days > self.lookback_days:
                        continue

                    # Check consumer relevance
                    if not self._is_consumer_relevant(article):
                        continue

                    # Convert to signal
                    signal = self._article_to_signal(article)
                    signals.append(signal)

            except Exception as e:
                logger.warning(f"Error parsing feed {feed_url}: {e}")
                continue

        logger.info(f"Parsed {len(self.feeds)} feeds, found {articles_found} articles, {len(signals)} relevant signals")

        # --- DNS probe enrichment pass (candidate-only) ---
        dns_enabled = os.environ.get("DNS_PROBE_ENABLED", "false").lower() in ("true", "1", "yes")
        dns_cap = int(os.environ.get("DNS_PROBE_CAP", "50"))
        dns_concurrency = int(os.environ.get("DNS_PROBE_CONCURRENCY", "8"))

        if not dns_enabled:
            for sig in signals:
                sig.raw_data["dns_probe_attempted"] = False
                sig.raw_data["dns_probe_domain"] = None
                sig.raw_data["dns_probe_status"] = "skipped_disabled"
        else:
            # 1. Collect unique company names from signals
            name_to_signals: dict[str, list[Signal]] = {}
            for sig in signals:
                cname = sig.raw_data.get("company_name")
                if cname:
                    name_to_signals.setdefault(cname, []).append(sig)
                else:
                    sig.raw_data["dns_probe_attempted"] = False
                    sig.raw_data["dns_probe_domain"] = None
                    sig.raw_data["dns_probe_status"] = "skipped_no_company"

            # 2. Probe unique names with bounded concurrency
            from utils.dns_probe import dns_probe_company

            sem = asyncio.Semaphore(dns_concurrency)
            names_to_probe = list(name_to_signals.keys())[:dns_cap]
            capped_names = set(name_to_signals.keys()) - set(names_to_probe)

            async def _probe_with_sem(name):
                async with sem:
                    return name, await dns_probe_company(name)

            results = await asyncio.gather(*[_probe_with_sem(n) for n in names_to_probe])
            probe_map = dict(results)

            # 3. Patch raw_data on matching signals
            for cname, sigs in name_to_signals.items():
                if cname in capped_names:
                    for sig in sigs:
                        sig.raw_data["dns_probe_attempted"] = False
                        sig.raw_data["dns_probe_domain"] = None
                        sig.raw_data["dns_probe_status"] = "skipped_cap"
                else:
                    domain = probe_map.get(cname)
                    for sig in sigs:
                        sig.raw_data["dns_probe_attempted"] = True
                        sig.raw_data["dns_probe_domain"] = domain
                        sig.raw_data["dns_probe_status"] = "hit" if domain else "miss"

        # --- DNS promotion pass (key upgrade) ---
        from utils.dns_promotion import is_dns_promote_enabled, get_dns_confidence_penalty

        if is_dns_promote_enabled():
            penalty = get_dns_confidence_penalty()
            for sig in signals:
                if (
                    sig.raw_data.get("dns_probe_status") == "hit"
                    and sig.raw_data.get("dns_probe_domain")
                ):
                    domain = sig.raw_data["dns_probe_domain"]
                    domain_key = f"domain:{domain}"
                    candidates = sig.raw_data.get("canonical_key_candidates", [])
                    if domain_key not in candidates:
                        sig.raw_data["canonical_key_candidates"] = candidates + [domain_key]
                    sig.confidence -= penalty
                    sig.raw_data["dns_promoted"] = True
                    sig.raw_data["dns_promoted_domain"] = domain
                    sig.raw_data["dns_confidence_penalty"] = penalty

        return signals

    async def _fetch_feed(self, url: str) -> str:
        """
        Fetch RSS feed content.

        Args:
            url: Feed URL

        Returns:
            Feed XML content
        """
        async def fetch():
            if self.http:
                response = await self.http._client.get(url, follow_redirects=True)
                response.raise_for_status()
                return response.text
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                return response.text

        return await self._fetch_with_retry(fetch)

    async def _parse_feed(self, feed_url: str) -> List[RSSArticle]:
        """
        Parse an RSS feed and extract articles.

        Args:
            feed_url: URL of the RSS feed

        Returns:
            List of RSSArticle objects
        """
        content = await self._fetch_feed(feed_url)

        # Determine feed name from URL
        feed_name = self._get_feed_name(feed_url)

        articles = []
        try:
            root = ET.fromstring(content)

            # Handle both RSS and Atom formats
            if root.tag == "rss":
                items = root.findall(".//item")
            elif root.tag == "{http://www.w3.org/2005/Atom}feed":
                items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            else:
                # Try generic item search
                items = root.findall(".//item")

            for item in items[:self.max_articles_per_feed]:
                try:
                    article = self._parse_item(item, feed_name, root.tag)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.debug(f"Error parsing item: {e}")
                    continue

        except ET.ParseError as e:
            logger.warning(f"XML parse error for {feed_url}: {e}")

        return articles

    def _parse_item(self, item: ET.Element, feed_name: str, feed_type: str) -> Optional[RSSArticle]:
        """
        Parse a single RSS item into an RSSArticle.

        Args:
            item: XML element for the item
            feed_name: Name of the source feed
            feed_type: Type of feed (rss or atom)

        Returns:
            RSSArticle or None if parsing fails
        """
        if feed_type == "{http://www.w3.org/2005/Atom}feed":
            # Atom format
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            title = _normalize_title(item.findtext("atom:title", "", ns))
            link_elem = item.find("atom:link", ns)
            url = link_elem.get("href", "") if link_elem is not None else ""
            description = item.findtext("atom:summary", "", ns) or item.findtext("atom:content", "", ns)
            pub_date = item.findtext("atom:published", "", ns) or item.findtext("atom:updated", "", ns)
            author = item.findtext("atom:author/atom:name", "", ns)
        else:
            # RSS format
            title = _normalize_title(item.findtext("title", ""))
            url = item.findtext("link", "")
            description = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "")
            author = item.findtext("author", "") or item.findtext("dc:creator", "")

            # Get categories
            categories = [cat.text for cat in item.findall("category") if cat.text]

        if not title or not url:
            return None

        # Parse publication date
        published_at = self._parse_date(pub_date)

        return RSSArticle(
            title=title.strip(),
            description=self._clean_html(description or ""),
            url=url.strip(),
            source_feed=feed_name,
            published_at=published_at,
            author=author.strip() if author else None,
            categories=categories if 'categories' in dir() else [],
        )

    def _parse_date(self, date_str: str) -> datetime:
        """
        Parse date string from RSS feed.

        Args:
            date_str: Date string in various formats

        Returns:
            datetime object (defaults to now if parsing fails)
        """
        if not date_str:
            return datetime.now(timezone.utc)

        try:
            # Try RFC 2822 format (common in RSS)
            return parsedate_to_datetime(date_str)
        except Exception:
            pass

        try:
            # Try ISO format (common in Atom)
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            pass

        # Default to now
        return datetime.now(timezone.utc)

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean)
        return clean.strip()

    def _get_feed_name(self, url: str) -> str:
        """Extract a readable name from feed URL."""
        domain = urlparse(url).netloc.lower().replace("www.", "")

        # Map known domains to names
        name_map = {
            "techcrunch.com": "TechCrunch Startups",
            "producthunt.com": "Product Hunt",
            "news.crunchbase.com": "Crunchbase News",
            "venturebeat.com": "VentureBeat",
            "mobihealthnews.com": "MobiHealthNews",
            "prnewswire.com": "PR Newswire",
            "globenewswire.com": "GlobeNewswire",
            "foodbusinessnews.net": "Food Business News",
        }

        return name_map.get(domain, domain)

    def _is_consumer_relevant(self, article: RSSArticle) -> bool:
        """
        Check if article is relevant to consumer thesis.

        Args:
            article: RSSArticle to check

        Returns:
            True if article is consumer-relevant
        """
        text = f"{article.title} {article.description}".lower()
        return any(term in text for term in CONSUMER_KEYWORDS)

    def _classify_signal_type(self, article: RSSArticle) -> str:
        """
        Classify the signal type based on article content.

        Args:
            article: RSSArticle to classify

        Returns:
            Signal type string
        """
        if article.is_press_release:
            return "press_release"
        elif article.is_funding_news:
            return "funding_announcement"
        elif article.is_product_launch:
            return "product_launch"
        else:
            return "news_mention"

    def _calculate_confidence(self, article: RSSArticle) -> float:
        """
        Calculate confidence score for article.

        Args:
            article: RSSArticle to score

        Returns:
            Confidence score (0.0 to 0.95)
        """
        base = 0.40

        # Authoritative source boost
        source_lower = article.source_feed.lower()
        if any(auth in source_lower for auth in AUTHORITATIVE_SOURCES):
            base += 0.15

        # Press release - moderate confidence
        if article.is_press_release:
            base += 0.10

        # Funding news boost
        if article.is_funding_news:
            base += 0.10

        # Product launch boost
        if article.is_product_launch:
            base += 0.05

        # Freshness boost
        if article.age_days <= 1:
            base += 0.05
        elif article.age_days > 7:
            base -= 0.05

        return min(max(base, 0.0), 0.95)

    def _article_to_signal(self, article: RSSArticle) -> Signal:
        """
        Convert RSSArticle to Signal object.

        Uses shared extract_company_info() for enhanced extraction.
        In baseline mode, behavior is identical to pre-refactor.

        Args:
            article: RSSArticle to convert

        Returns:
            Signal object
        """
        signal_type = self._classify_signal_type(article)
        confidence = self._calculate_confidence(article)

        # Full extraction pipeline (mode-gated)
        # Combine description + content for richer domain extraction in url_promote mode
        desc_text = " ".join(
            t for t in [article.description or "", article.content or ""] if t
        ).strip()
        extraction = extract_company_info(
            title=article.title,
            description=desc_text,
            url=article.url,
            allow_lone_domain=True,
        )
        company_name = extraction.company_name

        # Build canonical key: prefer promoted_domain, then article domain, then name
        # Use suffix-aware blocklist check (catches subdomains like m.reuters.com)
        domain_for_key = ""
        if extraction.promoted_domain and not is_blocked_domain(extraction.promoted_domain):
            domain_for_key = extraction.promoted_domain
        elif article.domain and not is_blocked_domain(article.domain):
            domain_for_key = article.domain

        canonical_keys = build_canonical_key_candidates(
            domain_or_website=domain_for_key,
            fallback_company_name=company_name or "",
        )

        # Segmented extraction: split on strong delimiters, re-run start-anchored extractor
        if not company_name:
            for segment in re.split(r'[?:—|•]\s*', article.title):
                segment = segment.strip()
                if segment and segment[0].isupper():
                    seg_result = extract_via_regex(segment)
                    if seg_result:
                        company_name = seg_result
                        canonical_keys = build_canonical_key_candidates(
                            domain_or_website=domain_for_key,
                            fallback_company_name=company_name,
                        )
                        break

        # Fallback: press-release title-segment heuristic
        if not canonical_keys and not company_name and article.is_press_release:
            segment_name = _extract_press_release_prefix(article.title)
            if segment_name:
                company_name = segment_name
                canonical_keys = build_canonical_key_candidates(
                    fallback_company_name=segment_name,
                )

        # Create unique signal ID
        import hashlib
        url_hash = hashlib.md5(article.url.encode()).hexdigest()[:12]
        signal_id = f"rss_{url_hash}"

        return Signal(
            id=signal_id,
            signal_type=signal_type,
            confidence=confidence,
            source_api="rss_feeds",
            source_url=article.url,
            detected_at=article.published_at,
            raw_data={
                "title": article.title,
                "description": article.description,
                "url": article.url,
                "source_feed": article.source_feed,
                "published_at": article.published_at.isoformat(),
                "author": article.author,
                "company_name": company_name,
                "company_name_method": extraction.company_name_method,
                "candidate_domains": extraction.candidate_domains,
                "promoted_domain": extraction.promoted_domain,
                "is_funding_news": article.is_funding_news,
                "is_product_launch": article.is_product_launch,
                "is_press_release": article.is_press_release,
                "canonical_key_candidates": canonical_keys,
            },
        )


# =============================================================================
# MOCK COLLECTOR FOR TESTING
# =============================================================================

class MockRSSFeedCollector(RSSFeedCollector):
    """
    Mock RSS feed collector for testing without network calls.

    Returns sample consumer-relevant articles.
    """

    def __init__(self, store: Optional[SignalStore] = None):
        super().__init__(store=store)

    async def _collect_signals(self) -> List[Signal]:
        """Return mock signals for testing."""
        mock_articles = [
            RSSArticle(
                title="WellnessApp raises $8M Series A for fitness tracking",
                description="Consumer health startup expands with AI-powered workout recommendations.",
                url="https://techcrunch.com/2024/01/15/wellnessapp-series-a",
                source_feed="TechCrunch Startups",
                published_at=datetime.now(timezone.utc) - timedelta(hours=6),
            ),
            RSSArticle(
                title="MealBox launches nationwide meal delivery service",
                description="D2C food startup announces expansion to all 50 states.",
                url="https://prnewswire.com/2024/01/14/mealbox-launch",
                source_feed="PR Newswire",
                published_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
            RSSArticle(
                title="TravelEase announces hotel booking platform update",
                description="Travel tech company releases new mobile app features.",
                url="https://venturebeat.com/2024/01/13/travelease-update",
                source_feed="VentureBeat",
                published_at=datetime.now(timezone.utc) - timedelta(days=2),
            ),
            RSSArticle(
                title="SkinGlow secures $3M seed funding for beauty tech",
                description="Consumer skincare brand raises seed round for D2C expansion.",
                url="https://news.crunchbase.com/2024/01/12/skinglow-seed",
                source_feed="Crunchbase News",
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
        collector = MockRSSFeedCollector()
        result = await collector.run(dry_run=True)

        print("=" * 50)
        print("RSS FEED COLLECTOR RESULTS")
        print("=" * 50)
        print(f"Status: {result.status.value}")
        print(f"Signals found: {result.signals_found}")
        print(f"New signals: {result.signals_new}")

    asyncio.run(main())
