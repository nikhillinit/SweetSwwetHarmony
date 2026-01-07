"""
UK Companies House Collector for Discovery Engine

Collects signals from UK Companies House API for recent incorporations.
New incorporations signal early-stage startups in target sectors.

Focus areas for Press On Ventures:
- Healthtech (SIC codes: 86xxx - health activities, 72xxx - R&D)
- Cleantech (SIC codes: 35xxx - electricity, 38xxx - waste management)
- AI/Software (SIC codes: 62xxx - computer programming, 63xxx - information services)

Companies House API:
- Base: https://api.company-information.service.gov.uk
- Auth: Basic auth (API key as username, empty password)
- Search: GET /advanced-search/companies
- Rate limit: 600 requests per 5 minutes
- Docs: https://developer-specs.company-information.service.gov.uk/

Key features:
- Filters by incorporation date (recent companies)
- Filters by SIC codes (thesis-fit sectors)
- Extracts full company profile including directors, filing history
- Builds canonical keys using companies_house_number
- Returns signals compatible with verification_gate_v2
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

import httpx

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import with_retry, RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.rate_limiter import get_rate_limiter
from verification.verification_gate_v2 import Signal, VerificationStatus
from utils.canonical_keys import (
    build_canonical_key,
    build_canonical_key_candidates,
    normalize_domain,
    normalize_companies_house_number,
)

logger = logging.getLogger(__name__)


# =============================================================================
# INDUSTRY CLASSIFICATIONS - THESIS FIT
# =============================================================================

# SIC 2007 codes for healthtech (UK standard)
HEALTHTECH_SIC_CODES = {
    # Healthcare activities
    "86101", "86102", "86210", "86220", "86230", "86900",
    # Medical/dental practice
    "86210", "86220", "86230",
    # Residential health facilities
    "87100", "87200", "87300", "87900",
    # Human health activities
    "86900",
    # Pharmaceutical manufacturing
    "21100", "21200",
    # Medical and dental instruments
    "32501", "32502",
    # Research and development - biotechnology
    "72110", "72190", "72200",
}

# SIC 2007 codes for cleantech
CLEANTECH_SIC_CODES = {
    # Electricity generation
    "35110", "35120", "35130", "35140",
    # Electric power distribution
    "35220", "35230",
    # Waste collection, treatment, disposal
    "38110", "38120", "38210", "38220", "38310", "38320",
    # Remediation and waste management services
    "39000",
    # Manufacture of electric motors, generators
    "27110", "27120",
    # Energy-related R&D
    "72190", "72200",
}

# SIC 2007 codes for AI/Software
AI_INFRASTRUCTURE_SIC_CODES = {
    # Computer programming
    "62011", "62012", "62020", "62030", "62090",
    # Computer consultancy
    "62020",
    # Information service activities
    "63110", "63120", "63910", "63990",
    # Data processing, hosting
    "63110", "63120",
    # Web portals
    "63120",
    # Research and development - natural sciences/engineering
    "72110", "72190", "72200",
}

# Combine all target SIC codes
TARGET_SIC_CODES = HEALTHTECH_SIC_CODES | CLEANTECH_SIC_CODES | AI_INFRASTRUCTURE_SIC_CODES

# Map SIC codes to industry groups
# Note: Some codes (like 72110 - biotech R&D) appear in multiple categories
# We prioritize in order: healthtech, cleantech, ai_infrastructure
SIC_TO_INDUSTRY = {}
# Build in priority order (later ones will overwrite earlier ones)
for code in AI_INFRASTRUCTURE_SIC_CODES:
    SIC_TO_INDUSTRY[code] = "ai_infrastructure"
for code in CLEANTECH_SIC_CODES:
    SIC_TO_INDUSTRY[code] = "cleantech"
for code in HEALTHTECH_SIC_CODES:
    SIC_TO_INDUSTRY[code] = "healthtech"


# =============================================================================
# CONFIGURATION
# =============================================================================

# Companies House API endpoints
COMPANIES_HOUSE_BASE_URL = "https://api.company-information.service.gov.uk"
COMPANIES_HOUSE_SEARCH_URL = f"{COMPANIES_HOUSE_BASE_URL}/advanced-search/companies"
COMPANIES_HOUSE_COMPANY_URL = f"{COMPANIES_HOUSE_BASE_URL}/company"

# Rate limiting (600 requests per 5 minutes = 2 requests per second)
REQUEST_DELAY_SECONDS = 0.6  # Conservative rate limiting
MAX_RETRIES = 3

# Default search parameters
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_MAX_COMPANIES = 100


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CompanyProfile:
    """UK Companies House company profile"""

    # Core identifiers
    company_number: str
    company_name: str
    company_status: str  # active, dissolved, liquidation, etc.

    # Dates
    incorporation_date: Optional[datetime] = None

    # Classification
    company_type: Optional[str] = None  # ltd, plc, llp, etc.
    sic_codes: List[str] = field(default_factory=list)
    industry_group: Optional[str] = None  # healthtech, cleantech, ai_infrastructure

    # Location
    registered_office_address: Dict[str, str] = field(default_factory=dict)
    jurisdiction: str = "england-wales"  # england-wales, scotland, northern-ireland

    # External references (for canonical key building)
    website: Optional[str] = None
    external_refs: Dict[str, str] = field(default_factory=dict)

    # People (for verification)
    officers: List[Dict[str, Any]] = field(default_factory=list)

    # Metadata
    company_url: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def age_days(self) -> int:
        """Days since incorporation"""
        if not self.incorporation_date:
            return 0
        return (datetime.now(timezone.utc) - self.incorporation_date).days

    @property
    def is_recent(self) -> bool:
        """Incorporated within last 90 days"""
        return self.age_days <= 90

    @property
    def is_target_sector(self) -> bool:
        """Matches Press On Ventures thesis (any SIC code match)"""
        return self.industry_group is not None

    @property
    def is_active(self) -> bool:
        """Company is active (not dissolved)"""
        return self.company_status.lower() in ("active", "live")

    @property
    def stage_estimate(self) -> str:
        """Estimate funding stage from age"""
        if self.age_days < 180:
            return "Pre-Seed"
        elif self.age_days < 365:
            return "Seed"
        else:
            return "Seed +"

    def to_signal(self) -> Signal:
        """Convert company profile to a Signal for verification gate"""

        # Build canonical key candidates from available identifiers
        canonical_key_candidates = build_canonical_key_candidates(
            domain_or_website=self.website or "",
            companies_house_number=self.company_number,
            crunchbase_uuid="",
            pitchbook_uuid="",
            github_org="",
            github_repo="",
            fallback_company_name=self.company_name,
            fallback_region=self.jurisdiction or "",
        )

        # Use first candidate as primary canonical key
        canonical_key = canonical_key_candidates[0] if canonical_key_candidates else f"companies_house_{self.company_number}"

        # Generate stable hash for provenance tracking
        response_hash = hashlib.sha256(
            f"{self.company_number}:{self.incorporation_date}".encode()
        ).hexdigest()[:16]

        # Confidence based on:
        # - Active status (dissolved = 0 confidence)
        # - Industry match (target sector = higher confidence)
        # - Recency (fresher = higher confidence)
        # - Data completeness (website, officers = higher confidence)

        if not self.is_active:
            # Dissolved companies should be filtered out, but just in case
            # Return 0 confidence immediately
            confidence = 0.0
            base_confidence = 0.0
        else:
            base_confidence = 0.6  # Incorporation is authoritative data

            # Boost for target sectors
            if self.is_target_sector:
                base_confidence += 0.2

            # Boost for very recent incorporations
            if self.age_days <= 30:
                base_confidence += 0.15
            elif self.age_days <= 90:
                base_confidence += 0.1

            # Boost for data completeness
            if self.website:
                base_confidence += 0.05
            if len(self.officers) >= 2:
                base_confidence += 0.05

            confidence = min(max(base_confidence, 0.0), 1.0)

        # Build address string for display
        address_parts = [
            self.registered_office_address.get("locality", ""),
            self.registered_office_address.get("region", ""),
            self.registered_office_address.get("postal_code", ""),
        ]
        address_str = ", ".join([p for p in address_parts if p])

        return Signal(
            id=f"companies_house_{self.company_number}",
            signal_type="incorporation",
            confidence=confidence,
            source_api="companies_house",
            source_url=self.company_url,
            source_response_hash=response_hash,
            detected_at=self.incorporation_date or self.retrieved_at,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["companies_house"],
            raw_data={
                "company_number": self.company_number,
                "company_name": self.company_name,
                "company_status": self.company_status,
                "company_type": self.company_type,
                "incorporation_date": self.incorporation_date.isoformat() if self.incorporation_date else None,
                "age_days": self.age_days,
                "sic_codes": self.sic_codes,
                "industry_group": self.industry_group,
                "jurisdiction": self.jurisdiction,
                "registered_address": address_str,
                "website": self.website,
                "officers_count": len(self.officers),
                "stage_estimate": self.stage_estimate,
                "canonical_key": canonical_key,
                "canonical_key_candidates": canonical_key_candidates,
            }
        )


# =============================================================================
# COMPANIES HOUSE COLLECTOR
# =============================================================================

class CompaniesHouseCollector(BaseCollector):
    """
    Collects recent UK company incorporations from Companies House API.

    Filters by:
    - Incorporation date (recent companies)
    - SIC codes (thesis-fit sectors)
    - Company status (active only)

    Usage:
        collector = CompaniesHouseCollector(store=signal_store, api_key=os.environ["COMPANIES_HOUSE_API_KEY"])
        result = await collector.run(dry_run=False)
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        api_key: Optional[str] = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        max_companies: int = DEFAULT_MAX_COMPANIES,
        target_sectors_only: bool = True,
    ):
        """
        Args:
            store: Optional SignalStore instance for persistence
            api_key: Companies House API key (or use COMPANIES_HOUSE_API_KEY env var)
            lookback_days: How many days back to search for incorporations
            max_companies: Maximum number of companies to process
            target_sectors_only: Only return companies in target sectors
        """
        super().__init__(store=store, collector_name="companies_house")

        self.api_key = api_key or os.environ.get("COMPANIES_HOUSE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Companies House API key required. "
                "Set COMPANIES_HOUSE_API_KEY env var or pass api_key parameter."
            )

        self.lookback_days = lookback_days
        self.max_companies = max_companies
        self.target_sectors_only = target_sectors_only

        self._client: Optional[httpx.AsyncClient] = None
        self._processed_company_numbers: Set[str] = set()

    async def __aenter__(self):
        """Async context manager entry"""
        # Companies House uses Basic Auth: API key as username, empty password
        auth_string = f"{self.api_key}:"
        auth_bytes = auth_string.encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")

        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Accept": "application/json",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self._client:
            await self._client.aclose()

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from Companies House incorporations.

        Returns:
            List of Signal objects
        """
        # Fetch recent incorporations
        companies = await self._fetch_recent_incorporations()

        logger.info(f"Found {len(companies)} recent incorporations")

        # Filter for target sectors if requested
        if self.target_sectors_only:
            companies = [c for c in companies if c.is_target_sector]
            logger.info(f"Filtered to {len(companies)} companies in target sectors")

        # Filter for active companies only
        companies = [c for c in companies if c.is_active]
        logger.info(f"Filtered to {len(companies)} active companies")

        # Convert to signals
        signals = [c.to_signal() for c in companies]

        return signals

    async def _fetch_recent_incorporations(self) -> List[CompanyProfile]:
        """
        Fetch recent incorporations from Companies House API.

        Uses the advanced search API to find companies by incorporation date.
        """
        companies: List[CompanyProfile] = []

        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.lookback_days)

        # Format dates for API (YYYY-MM-DD)
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

        # Search by incorporation date and company status
        # Note: Companies House API doesn't support SIC code filtering in search,
        # so we filter by SIC codes after fetching company profiles

        logger.info(
            f"Searching for incorporations between {start_date_str} and {end_date_str}"
        )

        # Paginate through results
        items_per_page = 50
        start_index = 0
        total_fetched = 0

        while total_fetched < self.max_companies:
            try:
                # Build search query
                # Note: Advanced search is limited, so we use basic search with incorporated_from/to
                params = {
                    "incorporated_from": start_date_str,
                    "incorporated_to": end_date_str,
                    "company_status": "active",
                    "size": items_per_page,
                    "start_index": start_index,
                }

                # Make request
                response = await self._make_request("GET", "/search/companies", params=params)

                if response.status_code != 200:
                    logger.warning(f"Search request failed: {response.status_code} - {response.text}")
                    break

                data = response.json()
                items = data.get("items", [])

                if not items:
                    logger.info("No more results")
                    break

                logger.info(f"Fetched {len(items)} companies (page {start_index // items_per_page + 1})")

                # Process each company
                for item in items:
                    if total_fetched >= self.max_companies:
                        break

                    company_number = item.get("company_number")
                    if not company_number or company_number in self._processed_company_numbers:
                        continue

                    # Fetch full company profile
                    try:
                        profile = await self._fetch_company_profile(company_number)
                        if profile:
                            companies.append(profile)
                            self._processed_company_numbers.add(company_number)
                            total_fetched += 1

                        # Rate limiting
                        await asyncio.sleep(REQUEST_DELAY_SECONDS)

                    except Exception as e:
                        logger.warning(f"Error fetching company {company_number}: {e}")
                        continue

                # Move to next page
                start_index += items_per_page

                # Check if we've reached the end
                total_results = data.get("total_results", 0)
                if start_index >= total_results:
                    logger.info(f"Reached end of results (total: {total_results})")
                    break

            except Exception as e:
                logger.error(f"Error in pagination loop: {e}")
                break

        logger.info(f"Fetched {len(companies)} company profiles")
        return companies

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _fetch_company_profile(self, company_number: str) -> Optional[CompanyProfile]:
        """
        Fetch full company profile from Companies House API.

        Args:
            company_number: UK Companies House number

        Returns:
            CompanyProfile or None if fetch failed
        """
        try:
            # Normalize company number
            company_number = normalize_companies_house_number(company_number)

            # Fetch company profile
            response = await self._make_request("GET", f"/company/{company_number}")

            if response.status_code == 404:
                logger.warning(f"Company {company_number} not found")
                return None

            response.raise_for_status()
            data = response.json()

            # Parse company data
            profile = self._parse_company_data(data)

            # Enrich with officers data (for verification)
            try:
                officers = await self._fetch_company_officers(company_number)
                profile.officers = officers
            except Exception as e:
                logger.debug(f"Could not fetch officers for {company_number}: {e}")

            return profile

        except Exception as e:
            logger.warning(f"Error fetching company profile {company_number}: {e}")
            return None

    def _parse_company_data(self, data: Dict[str, Any]) -> CompanyProfile:
        """
        Parse Companies House API response into CompanyProfile.

        Args:
            data: JSON response from /company/{number} endpoint

        Returns:
            CompanyProfile
        """
        company_number = data.get("company_number", "")
        company_name = data.get("company_name", "")
        company_status = data.get("company_status", "")
        company_type = data.get("type", "")

        # Parse incorporation date
        incorporation_date = None
        date_str = data.get("date_of_creation")
        if date_str:
            try:
                # Parse and ensure timezone-aware
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                # If naive, assume UTC
                if dt.tzinfo is None:
                    incorporation_date = dt.replace(tzinfo=timezone.utc)
                else:
                    incorporation_date = dt
            except Exception as e:
                logger.debug(f"Could not parse date {date_str}: {e}")

        # Extract SIC codes
        sic_codes = []
        if "sic_codes" in data:
            sic_codes = data["sic_codes"]

        # Determine industry group from SIC codes
        industry_group = None
        for code in sic_codes:
            if code in SIC_TO_INDUSTRY:
                industry_group = SIC_TO_INDUSTRY[code]
                break  # Use first match

        # Extract registered address
        registered_address = {}
        if "registered_office_address" in data:
            addr = data["registered_office_address"]
            registered_address = {
                "address_line_1": addr.get("address_line_1", ""),
                "address_line_2": addr.get("address_line_2", ""),
                "locality": addr.get("locality", ""),
                "region": addr.get("region", ""),
                "postal_code": addr.get("postal_code", ""),
                "country": addr.get("country", ""),
            }

        # Extract jurisdiction
        jurisdiction = data.get("jurisdiction", "england-wales")

        # Build company URL
        company_url = f"{COMPANIES_HOUSE_BASE_URL}/company/{company_number}"

        # Try to extract website (not always available in API)
        website = None
        # Companies House API doesn't include website in basic profile
        # Would need to fetch filing history or use external enrichment

        return CompanyProfile(
            company_number=company_number,
            company_name=company_name,
            company_status=company_status,
            incorporation_date=incorporation_date,
            company_type=company_type,
            sic_codes=sic_codes,
            industry_group=industry_group,
            registered_office_address=registered_address,
            jurisdiction=jurisdiction,
            website=website,
            company_url=company_url,
            raw_data=data,
        )

    async def _fetch_company_officers(self, company_number: str) -> List[Dict[str, Any]]:
        """
        Fetch company officers (directors, secretaries) for verification.

        Args:
            company_number: UK Companies House number

        Returns:
            List of officer records
        """
        try:
            response = await self._make_request("GET", f"/company/{company_number}/officers")

            if response.status_code != 200:
                return []

            data = response.json()
            items = data.get("items", [])

            # Extract relevant officer info
            officers = []
            for item in items:
                officer = {
                    "name": item.get("name", ""),
                    "officer_role": item.get("officer_role", ""),
                    "appointed_on": item.get("appointed_on"),
                    "nationality": item.get("nationality"),
                    "occupation": item.get("occupation"),
                }
                officers.append(officer)

            return officers

        except Exception as e:
            logger.debug(f"Could not fetch officers: {e}")
            return []

    async def _make_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None
    ) -> httpx.Response:
        """
        Make authenticated request to Companies House API with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., /company/12345678)
            params: Query parameters

        Returns:
            httpx.Response
        """
        # Build full URL
        if path.startswith("/"):
            url = f"{COMPANIES_HOUSE_BASE_URL}{path}"
        else:
            url = f"{COMPANIES_HOUSE_BASE_URL}/{path}"

        # Use rate limiter before making request
        await self.rate_limiter.acquire()

        # Wrap HTTP request with retry logic
        async def make_http_request():
            response = await self._client.request(
                method=method,
                url=url,
                params=params,
            )
            response.raise_for_status()
            return response

        return await with_retry(make_http_request, self.retry_config)


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

async def example_usage():
    """Example demonstrating the Companies House collector"""

    # Ensure API key is set
    api_key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not api_key:
        print("ERROR: COMPANIES_HOUSE_API_KEY environment variable not set")
        print("Get your API key from: https://developer.company-information.service.gov.uk/")
        return

    collector = CompaniesHouseCollector(
        api_key=api_key,
        lookback_days=90,
        max_companies=50,
        target_sectors_only=True,
    )

    result = await collector.run(dry_run=True)

    print("=" * 50)
    print("COMPANIES HOUSE COLLECTOR RESULT")
    print("=" * 50)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")
    print(f"Suppressed: {result.signals_suppressed}")
    print(f"Dry run: {result.dry_run}")

    if result.error_message:
        print(f"Error: {result.error_message}")


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run example
    asyncio.run(example_usage())
