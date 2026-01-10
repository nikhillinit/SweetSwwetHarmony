"""
Crunchbase Collector - Discover newly funded startups and company data.

when_to_use: When looking for recently funded startups, company financials,
  or comprehensive startup data for early-stage companies.

API: Crunchbase API
  - https://data.crunchbase.com/docs
  - Requires CRUNCHBASE_API_KEY environment variable
Cost: PAID (starts at $99/month for basic access)
Signal Strength: HIGH (0.6-0.9)

Crunchbase signals indicate:
1. Recent funding rounds (strong signal for active startups)
2. Company traction metrics (employee count, funding total)
3. Founder backgrounds
4. Industry categorization

Usage:
    collector = CrunchbaseCollector(api_key="...")
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig, with_retry
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.rate_limiter import AsyncRateLimiter
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# Crunchbase API endpoints
CRUNCHBASE_BASE = "https://api.crunchbase.com/api/v4"
SEARCH_ORGS = f"{CRUNCHBASE_BASE}/searches/organizations"
ENTITY_ORG = f"{CRUNCHBASE_BASE}/entities/organizations"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FundingRound:
    """A funding round from Crunchbase."""
    announced_on: Optional[datetime] = None
    funding_type: str = ""  # seed, series_a, etc.
    money_raised_usd: Optional[int] = None
    investor_count: int = 0
    lead_investors: List[str] = field(default_factory=list)


@dataclass
class CrunchbaseCompany:
    """A company from Crunchbase."""
    uuid: str
    name: str
    permalink: str
    short_description: str = ""
    website_url: str = ""
    founded_on: Optional[datetime] = None
    num_employees_enum: str = ""  # e.g., "c_11_50"
    total_funding_usd: Optional[int] = None
    last_funding_at: Optional[datetime] = None
    last_funding_type: str = ""
    categories: List[str] = field(default_factory=list)
    location_identifiers: List[str] = field(default_factory=list)
    funding_rounds: List[FundingRound] = field(default_factory=list)

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength for a company.

        Scoring:
        - Base: 0.5 (company in Crunchbase = legitimate startup)
        - Recent funding (<6 months): +0.2
        - Pre-seed/Seed funding: +0.1 (our target stage)
        - Series A funding: +0.05
        - Small team (1-50): +0.1
        - Consumer category: +0.1
        """
        base = 0.5

        # Recent funding boost (strong signal)
        if self.last_funding_at:
            days_since_funding = (datetime.now(timezone.utc) - self.last_funding_at).days
            if days_since_funding <= 30:
                base += 0.25
            elif days_since_funding <= 90:
                base += 0.2
            elif days_since_funding <= 180:
                base += 0.1

        # Stage-appropriate funding boost
        early_stages = {"pre_seed", "seed", "angel", "convertible_note", "grant"}
        if self.last_funding_type.lower() in early_stages:
            base += 0.1
        elif self.last_funding_type.lower() == "series_a":
            base += 0.05

        # Team size boost (prefer early-stage)
        if self.num_employees_enum in ["c_1_10", "c_11_50"]:
            base += 0.1
        elif self.num_employees_enum == "c_51_100":
            base += 0.05

        # Consumer category boost
        consumer_categories = {
            "food and beverage", "consumer goods", "health and wellness",
            "fitness", "travel and tourism", "hospitality", "restaurants",
            "beauty", "personal care", "consumer electronics", "e-commerce",
            "marketplace", "consumer", "consumer services"
        }
        if any(cat.lower() in consumer_categories for cat in self.categories):
            base += 0.1

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal."""
        confidence = self.calculate_signal_score()

        # Extract domain from website
        domain = ""
        if self.website_url:
            parsed = urlparse(self.website_url)
            domain = parsed.netloc.lower().replace("www.", "")

        # Create canonical key
        if domain:
            canonical_key = f"domain:{domain}"
        else:
            canonical_key = f"crunchbase:{self.permalink}"

        # Create unique signal ID
        signal_hash = hashlib.sha256(self.uuid.encode()).hexdigest()[:12]

        # Determine signal type based on what triggered it
        if self.last_funding_at and (datetime.now(timezone.utc) - self.last_funding_at).days <= 180:
            signal_type = "crunchbase_funding"
        else:
            signal_type = "crunchbase_company"

        return Signal(
            id=f"crunchbase_{signal_hash}",
            signal_type=signal_type,
            confidence=confidence,
            source_api="crunchbase",
            source_url=f"https://www.crunchbase.com/organization/{self.permalink}",
            source_response_hash=hashlib.sha256(
                f"{self.uuid}:{self.total_funding_usd or 0}".encode()
            ).hexdigest()[:16],
            detected_at=self.last_funding_at or datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["crunchbase"],
            raw_data={
                "canonical_key": canonical_key,
                "company_name": self.name,
                "company_domain": domain,
                "crunchbase_uuid": self.uuid,
                "crunchbase_permalink": self.permalink,
                "description": self.short_description[:500] if self.short_description else "",
                "founded_on": self.founded_on.isoformat() if self.founded_on else None,
                "num_employees": self.num_employees_enum,
                "total_funding_usd": self.total_funding_usd,
                "last_funding_at": self.last_funding_at.isoformat() if self.last_funding_at else None,
                "last_funding_type": self.last_funding_type,
                "categories": self.categories[:5],
                "location": self.location_identifiers[:2],
                "website": self.website_url,
                "why_now": self._build_why_now(),
            }
        )

    def _build_why_now(self) -> str:
        """Generate a 'why now' narrative."""
        parts = []

        if self.last_funding_at:
            days_ago = (datetime.now(timezone.utc) - self.last_funding_at).days
            funding_str = self.last_funding_type.replace("_", " ").title()

            if self.total_funding_usd:
                amount = self.total_funding_usd
                if amount >= 1_000_000:
                    amount_str = f"${amount / 1_000_000:.1f}M"
                else:
                    amount_str = f"${amount / 1_000:.0f}K"
                parts.append(f"Raised {amount_str} ({funding_str}, {days_ago}d ago)")
            else:
                parts.append(f"Recent {funding_str} funding ({days_ago}d ago)")

        if self.founded_on:
            age = datetime.now(timezone.utc).year - self.founded_on.year
            if age <= 2:
                parts.append(f"Founded {self.founded_on.year}")

        if self.num_employees_enum in ["c_1_10", "c_11_50"]:
            size_map = {"c_1_10": "1-10", "c_11_50": "11-50"}
            parts.append(f"Early-stage team ({size_map.get(self.num_employees_enum, '')})")

        return "; ".join(parts) if parts else "Active in Crunchbase"


# =============================================================================
# COLLECTOR
# =============================================================================

class CrunchbaseCollector(BaseCollector):
    """
    Collector for Crunchbase company and funding data.

    This collector can:
    1. Search for recently funded companies
    2. Filter by funding stage (pre-seed, seed, series A)
    3. Filter by category (consumer, food, health, etc.)
    4. Look up specific companies by domain or name

    Requires CRUNCHBASE_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        store: Optional[SignalStore] = None,
        retry_config: Optional[RetryConfig] = None,
        lookback_days: int = 30,
        funding_stages: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        max_results: int = 100,
    ):
        """
        Initialize Crunchbase collector.

        Args:
            api_key: Crunchbase API key (or set CRUNCHBASE_API_KEY env var)
            store: Optional SignalStore for persistence
            retry_config: Retry configuration
            lookback_days: How far back to search for funding (default: 30)
            funding_stages: Filter by stages (e.g., ["seed", "series_a"])
            categories: Filter by categories (e.g., ["consumer goods"])
            locations: Filter by locations (e.g., ["United States", "United Kingdom"])
            max_results: Maximum results to fetch (default: 100)
        """
        super().__init__(
            store=store,
            collector_name="crunchbase",
            retry_config=retry_config,
            api_name="crunchbase",
        )

        self.api_key = api_key or os.getenv("CRUNCHBASE_API_KEY")
        self.lookback_days = lookback_days
        self.funding_stages = funding_stages or ["pre_seed", "seed", "series_a"]
        self.categories = categories
        self.locations = locations or ["United States", "United Kingdom"]
        self.max_results = max_results

        # Rate limit: Crunchbase basic tier allows 200 req/min
        self._rate_limiter = AsyncRateLimiter(rate=3, period=1)
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Set up HTTP client."""
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect Crunchbase signals for recently funded companies.

        Returns:
            List of Signal objects from Crunchbase data
        """
        if not self.api_key:
            logger.warning(
                "CRUNCHBASE_API_KEY not set. Crunchbase collector requires API key. "
                "Get one at https://data.crunchbase.com/"
            )
            return []

        signals: List[Signal] = []

        try:
            companies = await self._search_recently_funded()
            for company in companies:
                # Save raw data and detect changes
                if self.asset_store:
                    is_new, changes = await self._save_asset_with_change_detection(
                        source_type=self.SOURCE_TYPE,
                        external_id=company.id or company.permalink,
                        raw_data=company.to_dict() if hasattr(company, 'to_dict') else vars(company),
                    )

                    # Skip unchanged companies
                    if not is_new and not changes:
                        logger.debug(f"Skipping unchanged Crunchbase company: {company.id}")
                        continue

                signals.append(company.to_signal())

            logger.info(f"Collected {len(signals)} Crunchbase signals")

        except Exception as e:
            logger.error(f"Error collecting from Crunchbase: {e}")

        return signals

    async def _search_recently_funded(self) -> List[CrunchbaseCompany]:
        """
        Search for recently funded companies matching our criteria.

        Returns:
            List of CrunchbaseCompany objects
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        # Calculate date range
        after_date = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

        # Build query
        query = {
            "field_ids": [
                "identifier",
                "short_description",
                "website_url",
                "founded_on",
                "num_employees_enum",
                "funding_total",
                "last_funding_at",
                "last_funding_type",
                "categories",
                "location_identifiers",
            ],
            "query": [
                {
                    "type": "predicate",
                    "field_id": "last_funding_at",
                    "operator_id": "gte",
                    "values": [after_date],
                },
                {
                    "type": "predicate",
                    "field_id": "last_funding_type",
                    "operator_id": "includes",
                    "values": self.funding_stages,
                },
            ],
            "order": [
                {
                    "field_id": "last_funding_at",
                    "sort": "desc",
                }
            ],
            "limit": self.max_results,
        }

        # Add location filter if specified
        if self.locations:
            query["query"].append({
                "type": "predicate",
                "field_id": "location_identifiers",
                "operator_id": "includes",
                "values": self.locations,
            })

        # Add category filter if specified
        if self.categories:
            query["query"].append({
                "type": "predicate",
                "field_id": "categories",
                "operator_id": "includes",
                "values": self.categories,
            })

        headers = {
            "X-cb-user-key": self.api_key,
            "Content-Type": "application/json",
        }

        async def do_request():
            await self._rate_limiter.acquire()
            response = await self._client.post(
                SEARCH_ORGS,
                headers=headers,
                json=query,
            )
            response.raise_for_status()
            return response.json()

        try:
            data = await with_retry(do_request, self.retry_config)
        except httpx.HTTPStatusError as e:
            logger.error(f"Crunchbase API error: {e.response.status_code}")
            if e.response.status_code == 401:
                logger.error("Invalid API key")
            raise

        companies = []
        for entity in data.get("entities", []):
            company = self._parse_company(entity)
            if company:
                companies.append(company)

        return companies

    def _parse_company(self, entity: Dict[str, Any]) -> Optional[CrunchbaseCompany]:
        """Parse Crunchbase entity into CrunchbaseCompany."""
        try:
            props = entity.get("properties", {})
            identifier = entity.get("identifier", {})

            # Parse dates
            founded_on = None
            if props.get("founded_on"):
                try:
                    founded_on = datetime.fromisoformat(props["founded_on"].replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            last_funding_at = None
            if props.get("last_funding_at"):
                try:
                    last_funding_at = datetime.fromisoformat(props["last_funding_at"].replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            # Parse funding total
            funding_total = None
            if props.get("funding_total", {}).get("value_usd"):
                funding_total = int(props["funding_total"]["value_usd"])

            # Parse categories
            categories = []
            for cat in props.get("categories", []) or []:
                if isinstance(cat, dict):
                    categories.append(cat.get("value", ""))
                elif isinstance(cat, str):
                    categories.append(cat)

            # Parse locations
            locations = []
            for loc in props.get("location_identifiers", []) or []:
                if isinstance(loc, dict):
                    locations.append(loc.get("value", ""))
                elif isinstance(loc, str):
                    locations.append(loc)

            return CrunchbaseCompany(
                uuid=identifier.get("uuid", ""),
                name=identifier.get("value", "Unknown"),
                permalink=identifier.get("permalink", ""),
                short_description=props.get("short_description", ""),
                website_url=props.get("website_url", ""),
                founded_on=founded_on,
                num_employees_enum=props.get("num_employees_enum", ""),
                total_funding_usd=funding_total,
                last_funding_at=last_funding_at,
                last_funding_type=props.get("last_funding_type", ""),
                categories=categories,
                location_identifiers=locations,
            )
        except Exception as e:
            logger.warning(f"Error parsing company: {e}")
            return None


# =============================================================================
# CLI
# =============================================================================

async def main():
    """Test the Crunchbase collector."""
    collector = CrunchbaseCollector(
        lookback_days=30,
        funding_stages=["seed", "pre_seed"],
        max_results=10,
    )

    async with collector:
        result = await collector.run(dry_run=True)

    print(f"\nResult: {result}")
    print(f"Signals found: {result.signals_found}")
    print(f"Status: {result.status}")

    if result.error_message:
        print(f"Error: {result.error_message}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
