"""
LinkedIn Collector - Discover founders and companies via LinkedIn data.

when_to_use: When looking for founder activity, company growth signals,
  or professional network indicators for early-stage companies.

API: Proxycurl API (third-party LinkedIn data provider)
  - https://nubela.co/proxycurl/
  - Requires PROXYCURL_API_KEY environment variable
Cost: PAID (~$0.01-0.03 per profile lookup)
Signal Strength: MEDIUM-HIGH (0.5-0.8)

LinkedIn signals indicate:
1. Founder professional background
2. Company headcount growth
3. Recent job postings (hiring = growth)
4. Company announcements/updates

Usage:
    collector = LinkedInCollector(api_key="...")
    result = await collector.run(dry_run=True)

Note: Direct LinkedIn API requires partnership agreement.
      This collector uses Proxycurl as a compliant data source.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

# Proxycurl API endpoints
PROXYCURL_BASE = "https://nubela.co/proxycurl/api/v2"
COMPANY_LOOKUP = f"{PROXYCURL_BASE}/linkedin/company"
PERSON_LOOKUP = f"{PROXYCURL_BASE}/linkedin"
JOB_LISTING = f"{PROXYCURL_BASE}/linkedin/company/job"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class LinkedInCompany:
    """A LinkedIn company profile."""
    linkedin_url: str
    name: str
    description: str = ""
    website: str = ""
    industry: str = ""
    company_size: str = ""  # e.g., "11-50"
    company_size_on_linkedin: int = 0
    founded_year: Optional[int] = None
    specialties: List[str] = field(default_factory=list)
    locations: List[Dict[str, str]] = field(default_factory=list)
    follower_count: int = 0

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength for a company.

        Scoring:
        - Base: 0.5 (company exists on LinkedIn)
        - Small team (11-50): +0.1 (early-stage indicator)
        - Recent founding (<3 years): +0.1
        - Growing followers: +0.05
        - Consumer-relevant industry: +0.1
        """
        base = 0.5

        # Size boost (prefer early-stage)
        if self.company_size in ["1-10", "11-50"]:
            base += 0.1
        elif self.company_size in ["51-200"]:
            base += 0.05

        # Recent founding boost
        if self.founded_year:
            age = datetime.now().year - self.founded_year
            if age <= 2:
                base += 0.15
            elif age <= 3:
                base += 0.1
            elif age <= 5:
                base += 0.05

        # Follower boost (social proof)
        if self.follower_count >= 1000:
            base += 0.05
        elif self.follower_count >= 500:
            base += 0.03

        # Consumer industry boost
        consumer_industries = {
            "food & beverages", "consumer goods", "health, wellness and fitness",
            "hospitality", "restaurants", "leisure, travel & tourism",
            "retail", "consumer services", "sporting goods"
        }
        if self.industry.lower() in consumer_industries:
            base += 0.1

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal."""
        confidence = self.calculate_signal_score()

        # Extract domain from website
        domain = ""
        if self.website:
            parsed = urlparse(self.website)
            domain = parsed.netloc.lower().replace("www.", "")

        # Create canonical key
        if domain:
            canonical_key = f"domain:{domain}"
        else:
            # Use LinkedIn URL as fallback
            linkedin_slug = self.linkedin_url.rstrip("/").split("/")[-1]
            canonical_key = f"linkedin:{linkedin_slug}"

        # Create unique signal ID
        signal_hash = hashlib.sha256(self.linkedin_url.encode()).hexdigest()[:12]

        return Signal(
            id=f"linkedin_company_{signal_hash}",
            signal_type="linkedin_company",
            confidence=confidence,
            source_api="linkedin",
            source_url=self.linkedin_url,
            source_response_hash=hashlib.sha256(
                f"{self.linkedin_url}:{self.follower_count}".encode()
            ).hexdigest()[:16],
            detected_at=datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["linkedin"],
            raw_data={
                "canonical_key": canonical_key,
                "company_name": self.name,
                "company_domain": domain,
                "description": self.description[:500] if self.description else "",
                "industry": self.industry,
                "company_size": self.company_size,
                "employee_count": self.company_size_on_linkedin,
                "founded_year": self.founded_year,
                "specialties": self.specialties[:5],
                "follower_count": self.follower_count,
                "linkedin_url": self.linkedin_url,
                "website": self.website,
                "why_now": self._build_why_now(),
            }
        )

    def _build_why_now(self) -> str:
        """Generate a 'why now' narrative."""
        parts = []

        if self.founded_year:
            age = datetime.now().year - self.founded_year
            if age <= 2:
                parts.append(f"Recently founded ({self.founded_year})")

        if self.company_size in ["1-10", "11-50"]:
            parts.append(f"Early-stage team ({self.company_size} employees)")

        if self.follower_count >= 500:
            parts.append(f"{self.follower_count:,} LinkedIn followers")

        return "; ".join(parts) if parts else "Active LinkedIn presence"


@dataclass
class LinkedInJobPosting:
    """A LinkedIn job posting signal."""
    job_url: str
    company_url: str
    company_name: str
    title: str
    location: str = ""
    posted_at: Optional[datetime] = None

    def to_signal(self, company_domain: str = "") -> Signal:
        """Convert to verification gate Signal."""
        # Job postings indicate growth - medium-high signal
        confidence = 0.65

        # Boost for leadership/founding roles
        leadership_keywords = ["founder", "co-founder", "ceo", "cto", "vp", "head of"]
        if any(kw in self.title.lower() for kw in leadership_keywords):
            confidence += 0.1

        # Create canonical key
        if company_domain:
            canonical_key = f"domain:{company_domain}"
        else:
            linkedin_slug = self.company_url.rstrip("/").split("/")[-1]
            canonical_key = f"linkedin:{linkedin_slug}"

        signal_hash = hashlib.sha256(self.job_url.encode()).hexdigest()[:12]

        return Signal(
            id=f"linkedin_job_{signal_hash}",
            signal_type="linkedin_job_posting",
            confidence=confidence,
            source_api="linkedin",
            source_url=self.job_url,
            source_response_hash=hashlib.sha256(self.job_url.encode()).hexdigest()[:16],
            detected_at=self.posted_at or datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["linkedin"],
            raw_data={
                "canonical_key": canonical_key,
                "company_name": self.company_name,
                "job_title": self.title,
                "location": self.location,
                "company_linkedin_url": self.company_url,
                "why_now": f"Hiring: {self.title}",
            }
        )


# =============================================================================
# COLLECTOR
# =============================================================================

class LinkedInCollector(BaseCollector):
    """
    Collector for LinkedIn company and job data via Proxycurl API.

    This collector can:
    1. Look up companies by domain or LinkedIn URL
    2. Find recent job postings for tracked companies
    3. Enrich existing signals with LinkedIn data

    Requires PROXYCURL_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        store: Optional[SignalStore] = None,
        retry_config: Optional[RetryConfig] = None,
        company_urls: Optional[List[str]] = None,
        company_domains: Optional[List[str]] = None,
    ):
        """
        Initialize LinkedIn collector.

        Args:
            api_key: Proxycurl API key (or set PROXYCURL_API_KEY env var)
            store: Optional SignalStore for persistence
            retry_config: Retry configuration
            company_urls: List of LinkedIn company URLs to look up
            company_domains: List of company domains to find on LinkedIn
        """
        super().__init__(
            store=store,
            collector_name="linkedin",
            retry_config=retry_config,
            api_name="linkedin",
        )

        self.api_key = api_key or os.getenv("PROXYCURL_API_KEY")
        self.company_urls = company_urls or []
        self.company_domains = company_domains or []

        # Rate limit: Proxycurl allows ~10 req/sec on paid plans
        self._rate_limiter = AsyncRateLimiter(rate=5, period=1)
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
        Collect LinkedIn signals for configured companies.

        Returns:
            List of Signal objects from LinkedIn data
        """
        if not self.api_key:
            logger.warning(
                "PROXYCURL_API_KEY not set. LinkedIn collector requires API key. "
                "Get one at https://nubela.co/proxycurl/"
            )
            return []

        signals: List[Signal] = []

        # Look up companies by LinkedIn URL
        for url in self.company_urls:
            try:
                company = await self._fetch_company(url)
                if company:
                    signals.append(company.to_signal())
            except Exception as e:
                logger.error(f"Error fetching company {url}: {e}")

        # Look up companies by domain
        for domain in self.company_domains:
            try:
                company = await self._resolve_company_by_domain(domain)
                if company:
                    signals.append(company.to_signal())
            except Exception as e:
                logger.error(f"Error resolving domain {domain}: {e}")

        logger.info(f"Collected {len(signals)} LinkedIn signals")
        return signals

    async def _fetch_company(self, linkedin_url: str) -> Optional[LinkedInCompany]:
        """
        Fetch company profile from Proxycurl.

        Args:
            linkedin_url: LinkedIn company page URL

        Returns:
            LinkedInCompany or None if not found
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "url": linkedin_url,
            "resolve_numeric_id": "true",
            "categories": "include",
        }

        async def do_request():
            await self._rate_limiter.acquire()
            response = await self._client.get(
                COMPANY_LOOKUP,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            return response.json()

        try:
            data = await with_retry(do_request, self.retry_config)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Company not found: {linkedin_url}")
                return None
            raise

        return self._parse_company(data, linkedin_url)

    async def _resolve_company_by_domain(self, domain: str) -> Optional[LinkedInCompany]:
        """
        Find LinkedIn company page by domain.

        Args:
            domain: Company domain (e.g., "acme.com")

        Returns:
            LinkedInCompany or None if not found
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"company_domain": domain}

        async def do_request():
            await self._rate_limiter.acquire()
            response = await self._client.get(
                f"{PROXYCURL_BASE}/linkedin/company/resolve",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            return response.json()

        try:
            data = await with_retry(do_request, self.retry_config)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"No LinkedIn company found for domain: {domain}")
                return None
            raise

        linkedin_url = data.get("url")
        if linkedin_url:
            return await self._fetch_company(linkedin_url)
        return None

    def _parse_company(self, data: Dict[str, Any], linkedin_url: str) -> LinkedInCompany:
        """Parse Proxycurl response into LinkedInCompany."""
        return LinkedInCompany(
            linkedin_url=linkedin_url,
            name=data.get("name", "Unknown"),
            description=data.get("description", ""),
            website=data.get("website", ""),
            industry=data.get("industry", ""),
            company_size=data.get("company_size", ""),
            company_size_on_linkedin=data.get("company_size_on_linkedin", 0),
            founded_year=data.get("founded_year"),
            specialties=data.get("specialties", []) or [],
            locations=data.get("locations", []) or [],
            follower_count=data.get("follower_count", 0),
        )


# =============================================================================
# CLI
# =============================================================================

async def main():
    """Test the LinkedIn collector."""
    import asyncio

    # Example usage
    collector = LinkedInCollector(
        company_urls=[
            "https://www.linkedin.com/company/anthropic/",
        ],
        company_domains=[
            "openai.com",
        ],
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
