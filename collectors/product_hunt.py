"""
Product Hunt Collector - Discover newly launched startups.

when_to_use: When looking for early-stage startups that have recently launched.
  Product Hunt launches indicate a company is publicly marketing and has
  a product ready for users.

API: Product Hunt GraphQL API
Cost: FREE (with API key)
Signal Strength: MEDIUM (0.5-0.7)

Product Hunt signals indicate:
1. The company has a launchable product
2. The company is actively marketing
3. There's some level of user interest (upvotes)

Usage:
    collector = ProductHuntCollector(api_key="...")
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import with_retry, RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.rate_limiter import get_rate_limiter
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# Product Hunt GraphQL API
PRODUCT_HUNT_API = "https://api.producthunt.com/v2/api/graphql"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ProductHuntLaunch:
    """A Product Hunt launch signal."""
    product_id: str
    name: str
    tagline: str
    description: str
    url: str
    website: str
    votes_count: int
    comments_count: int
    launched_at: datetime
    topics: List[str] = field(default_factory=list)
    makers: List[Dict[str, str]] = field(default_factory=list)
    thumbnail_url: str = ""

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength based on launch metrics.

        Scoring:
        - Base: 0.5 (Product Hunt launch = real product)
        - Boost for votes: up to +0.15
        - Boost for comments: up to +0.1
        - Freshness bonus: +0.05 if within 7 days
        """
        base = 0.5

        # Vote boost
        if self.votes_count >= 500:
            base += 0.15
        elif self.votes_count >= 200:
            base += 0.1
        elif self.votes_count >= 50:
            base += 0.05

        # Comment boost
        if self.comments_count >= 50:
            base += 0.1
        elif self.comments_count >= 20:
            base += 0.05

        # Freshness bonus
        age_days = (datetime.now(timezone.utc) - self.launched_at).days
        if age_days <= 7:
            base += 0.05

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal."""
        confidence = self.calculate_signal_score()

        # Extract domain from website
        domain = ""
        if self.website:
            from urllib.parse import urlparse
            parsed = urlparse(self.website)
            domain = parsed.netloc.lower().replace("www.", "")

        # Create unique signal ID
        signal_id = f"ph_{self.product_id}"
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        return Signal(
            id=f"product_hunt_launch_{signal_hash}",
            signal_type="product_hunt_launch",
            confidence=confidence,
            source_api="product_hunt",
            source_url=self.url,
            source_response_hash=hashlib.sha256(
                f"{self.product_id}:{self.votes_count}".encode()
            ).hexdigest()[:16],
            detected_at=self.launched_at,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["product_hunt"],
            raw_data={
                "canonical_key": f"domain:{domain}" if domain else f"product_hunt:{self.product_id}",
                "company_name": self.name,
                "company_domain": domain,
                "product_hunt_id": self.product_id,
                "tagline": self.tagline,
                "description": self.description[:500] if self.description else "",
                "votes_count": self.votes_count,
                "comments_count": self.comments_count,
                "topics": self.topics[:5],
                "makers": self.makers[:3],
                "website": self.website,
            }
        )


# =============================================================================
# COLLECTOR
# =============================================================================

class ProductHuntCollector(BaseCollector):
    """
    Collect launches from Product Hunt.

    Discovers newly launched products that might be early-stage startups.

    Usage:
        collector = ProductHuntCollector(
            api_key="...",
            store=signal_store,
        )
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        store: Optional[SignalStore] = None,
        lookback_days: int = 7,
        min_votes: int = 10,
    ):
        """
        Args:
            api_key: Product Hunt API key (or use PH_API_KEY env var)
            store: SignalStore for persistence
            lookback_days: How far back to search for launches
            min_votes: Minimum votes to include a launch
        """
        super().__init__(store=store, collector_name="product_hunt")
        self.api_key = api_key or os.environ.get("PH_API_KEY", "")
        self.lookback_days = lookback_days
        self.min_votes = min_votes
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    async def _collect_signals(self) -> List[Signal]:
        """Collect Product Hunt launches as signals."""
        if not self.api_key:
            logger.warning("No Product Hunt API key configured - skipping")
            return []

        launches = await self._fetch_launches()

        signals = []
        for launch in launches:
            # Save raw data and detect changes
            if self.asset_store:
                is_new, changes = await self._save_asset_with_change_detection(
                    source_type=self.SOURCE_TYPE,
                    external_id=launch.id,
                    raw_data=launch.to_dict(),
                )

                # Skip unchanged launches
                if not is_new and not changes:
                    logger.debug(f"Skipping unchanged Product Hunt launch: {launch.id}")
                    continue

            signals.append(launch.to_signal())

        return signals

    async def _fetch_launches(self) -> List[ProductHuntLaunch]:
        """Fetch recent launches from Product Hunt API."""
        launches: List[ProductHuntLaunch] = []

        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.lookback_days)

        # GraphQL query for posts
        query = """
        query GetPosts($first: Int!, $after: String, $postedAfter: DateTime) {
            posts(
                first: $first,
                after: $after,
                postedAfter: $postedAfter,
                order: VOTES
            ) {
                edges {
                    cursor
                    node {
                        id
                        name
                        tagline
                        description
                        url
                        website
                        votesCount
                        commentsCount
                        createdAt
                        topics {
                            edges {
                                node {
                                    name
                                }
                            }
                        }
                        makers {
                            id
                            name
                            headline
                        }
                        thumbnail {
                            url
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        cursor = None
        page = 0
        max_pages = 5  # Limit pages to avoid excessive API calls

        while page < max_pages:
            variables = {
                "first": 50,
                "after": cursor,
                "postedAfter": start_date.isoformat(),
            }

            try:
                # Use rate limiter before making request
                await self.rate_limiter.acquire()

                # Wrap HTTP request with retry logic
                async def fetch_product_hunt():
                    response = await self.client.post(
                        PRODUCT_HUNT_API,
                        headers=headers,
                        json={"query": query, "variables": variables},
                    )

                    if response.status_code != 200:
                        logger.error(f"Product Hunt API error: {response.status_code}")
                        response.raise_for_status()

                    return response.json()

                data = await with_retry(fetch_product_hunt, self.retry_config)

                if "errors" in data:
                    logger.error(f"Product Hunt GraphQL errors: {data['errors']}")
                    break

                posts = data.get("data", {}).get("posts", {})
                edges = posts.get("edges", [])

                if not edges:
                    break

                for edge in edges:
                    node = edge.get("node", {})

                    # Skip low-vote launches
                    votes = node.get("votesCount", 0)
                    if votes < self.min_votes:
                        continue

                    # Parse topics
                    topics = []
                    topic_edges = node.get("topics", {}).get("edges", [])
                    for te in topic_edges:
                        topic_name = te.get("node", {}).get("name")
                        if topic_name:
                            topics.append(topic_name)

                    # Parse makers
                    makers = []
                    for maker in node.get("makers", []):
                        makers.append({
                            "id": maker.get("id", ""),
                            "name": maker.get("name", ""),
                            "headline": maker.get("headline", ""),
                        })

                    # Parse launch date
                    created_at_str = node.get("createdAt", "")
                    try:
                        launched_at = datetime.fromisoformat(
                            created_at_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        launched_at = datetime.now(timezone.utc)

                    launch = ProductHuntLaunch(
                        product_id=node.get("id", ""),
                        name=node.get("name", ""),
                        tagline=node.get("tagline", ""),
                        description=node.get("description", ""),
                        url=node.get("url", ""),
                        website=node.get("website", ""),
                        votes_count=votes,
                        comments_count=node.get("commentsCount", 0),
                        launched_at=launched_at,
                        topics=topics,
                        makers=makers,
                        thumbnail_url=node.get("thumbnail", {}).get("url", ""),
                    )
                    launches.append(launch)

                # Check for more pages
                page_info = posts.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break

                cursor = page_info.get("endCursor")
                page += 1

                # Rate limit courtesy
                await asyncio.sleep(0.5)

            except httpx.HTTPError as e:
                logger.error(f"Product Hunt HTTP error: {e}")
                break
            except Exception as e:
                logger.exception(f"Product Hunt fetch error: {e}")
                break

        logger.info(f"Fetched {len(launches)} Product Hunt launches")
        return launches


# =============================================================================
# CLI
# =============================================================================

async def main():
    """CLI for testing Product Hunt collector."""
    import argparse

    parser = argparse.ArgumentParser(description="Product Hunt Collector")
    parser.add_argument("--api-key", help="Product Hunt API key")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    parser.add_argument("--min-votes", type=int, default=10, help="Minimum votes")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    api_key = args.api_key or os.environ.get("PH_API_KEY")
    if not api_key:
        print("ERROR: Product Hunt API key required")
        print("Set PH_API_KEY environment variable or use --api-key")
        return

    collector = ProductHuntCollector(
        api_key=api_key,
        lookback_days=args.days,
        min_votes=args.min_votes,
    )

    result = await collector.run(dry_run=True)

    print("\n" + "=" * 60)
    print("PRODUCT HUNT COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
