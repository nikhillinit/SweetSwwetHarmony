"""
Hacker News Collector - Discover startup traction via HN mentions.

when_to_use: When looking for startups with community traction on Hacker News.
  HN mentions indicate developer/tech community interest and can signal
  product launches (Show HN), viral content, or founder visibility.

API: Algolia HN Search API (no authentication required)
Cost: FREE
Signal Strength: MEDIUM (0.5-0.7)

HN signals indicate:
1. Product launch activity (Show HN)
2. Community traction and engagement
3. Developer/tech community interest
4. Founder marketing activity

Usage:
    # Mode 1: Find Show HN launches (default)
    collector = HackerNewsCollector()
    result = await collector.run(dry_run=True)

    # Mode 2: Enrich existing domains with HN mentions
    collector = HackerNewsCollector(search_domains=["mystartup.com", "another.io"])
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.provenance import create_provenance, hash_response
from collectors.retry_strategy import with_retry, RetryConfig
from collectors.source_types import SOURCE_TYPE
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.company_name_extractor import normalize_company_name
from utils.hn_title import strip_hn_prefix, extract_name_from_hn_body, HN_SEP_RE
from utils.rate_limiter import get_rate_limiter
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# Algolia-powered Hacker News Search API
HN_ALGOLIA_API = "https://hn.algolia.com/api/v1/search"

# _SHOW_HN_SEP_RE moved to utils.hn_title as HN_SEP_RE

# Common subdomains that are NOT company names
_SUBDOMAIN_PREFIXES = frozenset({
    "app", "blog", "docs", "api", "m", "my", "dev", "staging",
    "beta", "web", "portal", "dashboard", "help", "support",
})

_MAX_NAME_WORDS = 4
_SENTENCE_MARKERS = frozenset({
    "beats", "helps", "makes", "shows", "gets", "lets", "turns",
    "finds", "gives", "runs", "uses", "brings", "takes", "builds",
    "creates", "launches", "announces", "introduces", "simplifies",
    "is", "are", "was", "that", "which", "into",
    "from", "with", "your", "their", "every", "about",
})


def _looks_like_name(text: str) -> bool:
    """Last-resort heuristic for posts with no domain."""
    if not text or not text.strip():
        return False
    words = text.strip().split()
    if len(words) > _MAX_NAME_WORDS:
        return False
    if {w.lower() for w in words} & _SENTENCE_MARKERS:
        return False
    return True


def _domain_to_company(domain: str) -> str:
    """Convert domain to company name, handling subdomains."""
    if not domain:
        return ""
    parts = domain.split(".")
    # Skip common subdomain prefixes to find the real label
    # e.g., app.startup.com → "startup", blog.acme.ai → "acme"
    for i, part in enumerate(parts[:-1]):  # skip TLD
        if part.lower() not in _SUBDOMAIN_PREFIXES:
            return part.title()
    # All parts are subdomains or TLDs — use first part
    return parts[0].title()


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class HackerNewsPost:
    """A Hacker News post signal."""

    object_id: str
    title: str
    url: str
    author: str
    points: int
    num_comments: int
    created_at: datetime
    story_text: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def is_show_hn(self) -> bool:
        """Check if this is a Show HN post."""
        return "show_hn" in self.tags or self.title.lower().startswith("show hn")

    @property
    def is_ask_hn(self) -> bool:
        """Check if this is an Ask HN post."""
        return "ask_hn" in self.tags or self.title.lower().startswith("ask hn")

    @property
    def domain(self) -> str:
        """Extract domain from URL."""
        if not self.url:
            return ""
        try:
            parsed = urlparse(self.url)
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength based on HN metrics.

        Scoring:
        - Base: 0.5 (HN mention = some visibility)
        - Boost for points: up to +0.15
        - Boost for comments: up to +0.1
        - Show HN bonus: +0.05 (indicates product launch)
        - Freshness bonus: +0.05 if within 7 days
        """
        base = 0.5

        # Points boost
        if self.points >= 500:
            base += 0.15
        elif self.points >= 200:
            base += 0.1
        elif self.points >= 50:
            base += 0.05

        # Comments boost
        if self.num_comments >= 100:
            base += 0.1
        elif self.num_comments >= 50:
            base += 0.07
        elif self.num_comments >= 20:
            base += 0.05

        # Show HN bonus (indicates product launch)
        if self.is_show_hn:
            base += 0.05

        # Freshness bonus
        age_days = (datetime.now(timezone.utc) - self.created_at).days
        if age_days <= 7:
            base += 0.05

        return min(base, 1.0)

    def to_signal(self, retrieved_at: Optional[datetime] = None) -> Signal:
        """Convert to verification gate Signal."""
        confidence = self.calculate_signal_score()

        # Create canonical key from domain or HN ID
        if self.domain:
            canonical_key = f"domain:{self.domain}"
        else:
            canonical_key = f"hacker_news:{self.object_id}"

        # Create unique signal ID
        signal_id = f"hn_{self.object_id}"
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        # Use provided retrieved_at or default to now
        if retrieved_at is None:
            retrieved_at = datetime.now(timezone.utc)

        # Build raw data for hashing
        raw_data_for_hash = {
            "object_id": self.object_id,
            "points": self.points,
            "num_comments": self.num_comments,
        }

        # Create provenance for audit trail
        source_url = f"https://news.ycombinator.com/item?id={self.object_id}"
        provenance = create_provenance(
            source_url=source_url,
            response_data=raw_data_for_hash,
            endpoint="/api/v1/search",
            query_params={"objectID": self.object_id},
            retrieved_at=retrieved_at,
        )

        return Signal(
            id=f"hacker_news_mention_{signal_hash}",
            signal_type="hacker_news_mention",
            confidence=confidence,
            source_api="hacker_news",
            source_url=source_url,
            source_response_hash=hash_response(raw_data_for_hash),
            detected_at=self.created_at,
            retrieved_at=retrieved_at,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["hacker_news"],
            raw_data={
                "canonical_key": canonical_key,
                "company_name": self._extract_company_name(),
                "company_domain": self.domain,
                "hacker_news_id": self.object_id,
                "title": self.title,
                "points": self.points,
                "num_comments": self.num_comments,
                "author": self.author,
                "is_show_hn": self.is_show_hn,
                "story_text": self.story_text[:500] if self.story_text else "",
                "url": self.url,
                **provenance,  # Add provenance block
            },
        )

    def _extract_company_name(self) -> str:
        """Try to extract company name from title or domain."""
        cleaned, prefix_type = strip_hn_prefix(self.title)

        if prefix_type in ("show", "launch", "demo"):
            # Tier 1: separator-based extraction from cleaned body
            name = extract_name_from_hn_body(cleaned)
            if name:
                normalized = normalize_company_name(name[:50])
                if normalized:
                    return normalized.title()

            # Tier 2: domain fallback (subdomain-aware)
            if self.domain:
                return _domain_to_company(self.domain)

            # Tier 3: last-resort heuristic for no-URL posts
            if _looks_like_name(cleaned):
                normalized = normalize_company_name(cleaned[:50])
                if normalized:
                    return normalized.title()

        # Non-HN-prefix → domain only
        if self.domain:
            return _domain_to_company(self.domain)
        return ""


# =============================================================================
# COLLECTOR
# =============================================================================


class HackerNewsCollector(BaseCollector):
    """
    Collect Hacker News mentions as traction signals.

    Two modes of operation:
    1. Show HN mode (default): Find recent Show HN launches
    2. Domain enrichment mode: Search for mentions of specific domains

    Usage:
        # Mode 1: Show HN discovery
        collector = HackerNewsCollector()
        result = await collector.run(dry_run=True)

        # Mode 2: Domain enrichment
        collector = HackerNewsCollector(
            search_domains=["acme.com", "startup.io"],
            store=signal_store,
        )
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        lookback_days: int = 7,
        min_points: int = 10,
        search_domains: Optional[List[str]] = None,
    ):
        """
        Args:
            store: SignalStore for persistence
            lookback_days: How far back to search for posts
            min_points: Minimum points to include a post
            search_domains: List of domains to search for (None = Show HN mode)
        """
        super().__init__(
            store=store,
            collector_name="hacker_news",
            api_name="hacker_news",
        )
        self.lookback_days = lookback_days
        self.min_points = min_points
        self.search_domains = search_domains
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    async def _collect_signals(self) -> List[Signal]:
        """Collect Hacker News posts as signals."""
        posts = await self._fetch_posts()

        signals = []
        for post in posts:
            # Save raw data and detect changes
            if self.asset_store:
                is_new, changes = await self._save_asset_with_change_detection(
                    source_type=self.SOURCE_TYPE,
                    external_id=str(post.object_id),
                    raw_data=post.to_dict() if hasattr(post, 'to_dict') else vars(post),
                )

                # Skip unchanged posts
                if not is_new and not changes:
                    logger.debug(f"Skipping unchanged HN post: {post.object_id}")
                    continue

            signals.append(post.to_signal())

        return signals

    async def _fetch_posts(self) -> List[HackerNewsPost]:
        """
        Fetch posts from Hacker News Algolia API.

        In Show HN mode: searches for tag:show_hn
        In domain mode: searches for each domain
        """
        posts: List[HackerNewsPost] = []

        # Calculate timestamp for lookback
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        cutoff_timestamp = int(cutoff_date.timestamp())

        if self.search_domains:
            # Domain enrichment mode: search each domain
            for domain in self.search_domains:
                domain_posts = await self._search_domain(domain, cutoff_timestamp)
                posts.extend(domain_posts)
                # Rate limit courtesy between domains
                await asyncio.sleep(0.2)
        else:
            # Show HN mode: find recent Show HN posts
            posts = await self._search_show_hn(cutoff_timestamp)

        # Filter by minimum points
        filtered_posts = [p for p in posts if p.points >= self.min_points]

        logger.info(
            f"Fetched {len(filtered_posts)} HN posts "
            f"(filtered from {len(posts)} by min_points={self.min_points})"
        )

        return filtered_posts

    async def _search_show_hn(self, cutoff_timestamp: int) -> List[HackerNewsPost]:
        """Search for Show HN posts."""
        posts: List[HackerNewsPost] = []
        page = 0
        max_pages = 5

        while page < max_pages:
            params = {
                "tags": "show_hn",
                "numericFilters": f"created_at_i>{cutoff_timestamp}",
                "hitsPerPage": 50,
                "page": page,
            }

            try:
                # Use rate limiter before making request
                await self.rate_limiter.acquire()

                # Wrap HTTP request with retry logic
                async def fetch_hn():
                    response = await self.client.get(HN_ALGOLIA_API, params=params)

                    if response.status_code != 200:
                        logger.error(f"HN API error: {response.status_code}")
                        response.raise_for_status()

                    return response.json()

                data = await with_retry(fetch_hn, self.retry_config)
                hits = data.get("hits", [])

                if not hits:
                    break

                for hit in hits:
                    post = self._parse_hit(hit)
                    if post:
                        posts.append(post)

                # Check for more pages
                if page >= data.get("nbPages", 1) - 1:
                    break

                page += 1
                await asyncio.sleep(0.2)  # Rate limit courtesy

            except httpx.HTTPError as e:
                logger.error(f"HN HTTP error: {e}")
                break
            except Exception as e:
                logger.exception(f"HN fetch error: {e}")
                break

        return posts

    async def _search_domain(
        self, domain: str, cutoff_timestamp: int
    ) -> List[HackerNewsPost]:
        """Search for posts mentioning a specific domain."""
        posts: List[HackerNewsPost] = []

        params = {
            "query": domain,
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff_timestamp}",
            "hitsPerPage": 50,
        }

        try:
            # Use rate limiter before making request
            await self.rate_limiter.acquire()

            # Wrap HTTP request with retry logic
            async def fetch_domain_posts():
                response = await self.client.get(HN_ALGOLIA_API, params=params)

                if response.status_code != 200:
                    logger.error(f"HN API error for {domain}: {response.status_code}")
                    response.raise_for_status()

                return response.json()

            data = await with_retry(fetch_domain_posts, self.retry_config)

            for hit in data.get("hits", []):
                # Verify the URL actually contains the domain
                hit_url = hit.get("url", "")
                if domain.lower() in hit_url.lower():
                    post = self._parse_hit(hit)
                    if post:
                        posts.append(post)

        except httpx.HTTPError as e:
            logger.error(f"HN HTTP error for {domain}: {e}")
        except Exception as e:
            logger.exception(f"HN fetch error for {domain}: {e}")

        return posts

    def _parse_hit(self, hit: Dict[str, Any]) -> Optional[HackerNewsPost]:
        """Parse an Algolia hit into a HackerNewsPost."""
        try:
            # Parse timestamp
            created_at_i = hit.get("created_at_i")
            if created_at_i:
                created_at = datetime.fromtimestamp(created_at_i, tz=timezone.utc)
            else:
                # Try ISO format
                created_at_str = hit.get("created_at", "")
                try:
                    created_at = datetime.fromisoformat(
                        created_at_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    created_at = datetime.now(timezone.utc)

            # Extract tags
            tags = hit.get("_tags", [])

            return HackerNewsPost(
                object_id=str(hit.get("objectID", "")),
                title=hit.get("title", ""),
                url=hit.get("url", ""),
                author=hit.get("author", ""),
                points=hit.get("points", 0) or 0,
                num_comments=hit.get("num_comments", 0) or 0,
                created_at=created_at,
                story_text=hit.get("story_text", "") or "",
                tags=tags,
            )
        except Exception as e:
            logger.warning(f"Failed to parse HN hit: {e}")
            return None


# =============================================================================
# CLI
# =============================================================================


async def main():
    """CLI for testing Hacker News collector."""
    import argparse

    parser = argparse.ArgumentParser(description="Hacker News Collector")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    parser.add_argument("--min-points", type=int, default=10, help="Minimum points")
    parser.add_argument(
        "--domains",
        nargs="+",
        help="Domains to search (omit for Show HN mode)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    collector = HackerNewsCollector(
        lookback_days=args.days,
        min_points=args.min_points,
        search_domains=args.domains,
    )

    async with collector:
        result = await collector.run(dry_run=True)

    print("\n" + "=" * 60)
    print("HACKER NEWS COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Mode: {'Domain search' if args.domains else 'Show HN discovery'}")
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
