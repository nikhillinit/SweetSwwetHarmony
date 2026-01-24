"""
OpenCorporates Collector for Discovery Engine

Looks up company incorporation data from OpenCorporates API.
Provides corroboration for signals from other sources.

OpenCorporates aggregates data from 140+ corporate registries worldwide,
including Delaware Division of Corporations, California SOS, UK Companies House.

API docs: https://api.opencorporates.com/documentation/API-Reference

Focus areas for Press On Ventures:
- US Delaware (us_de) - Most US startups incorporate here
- US California (us_ca) - Tech company headquarters
- UK (gb) - UK startups

Usage:
    collector = OpenCorporatesCollector(api_key="your_key")
    results = await collector.search_company("Acme Corp")
    signals = await collector.collect_for_company("Acme Corp")
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from collectors.base import BaseCollector
from collectors.provenance import create_provenance, hash_response
from collectors.source_types import SOURCE_TYPE

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# API configuration
API_BASE_URL = "https://api.opencorporates.com/v0.4"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RESULTS_PER_PAGE = 30

# Rate limiting (free tier: 200/month, 50/day)
RATE_LIMIT_DELAY = 1.0  # seconds between requests

# Supported jurisdictions for thesis fit
SUPPORTED_JURISDICTIONS = {
    # United States
    "us_de": "Delaware",
    "us_ca": "California",
    "us_ny": "New York",
    "us_tx": "Texas",
    "us_fl": "Florida",
    "us_wa": "Washington",
    "us_ma": "Massachusetts",
    "us_co": "Colorado",
    # United Kingdom
    "gb": "United Kingdom",
    "gb_sct": "Scotland",
}

# US jurisdictions for filtering
US_JURISDICTIONS = [j for j in SUPPORTED_JURISDICTIONS if j.startswith("us_")]
UK_JURISDICTIONS = [j for j in SUPPORTED_JURISDICTIONS if j.startswith("gb")]

# Status values indicating active company
ACTIVE_STATUSES = {
    "active",
    "good standing",
    "in good standing",
    "current",
    "registered",
}

# Status values indicating dissolved/inactive
DISSOLVED_STATUSES = {
    "dissolved",
    "inactive",
    "cancelled",
    "revoked",
    "forfeited",
    "withdrawn",
    "merged",
    "converted",
}

# Confidence scores by jurisdiction (Delaware = higher validation)
JURISDICTION_CONFIDENCE = {
    "us_de": 0.75,  # Delaware - gold standard for US startups
    "us_ca": 0.70,  # California - common for tech
    "gb": 0.70,     # UK Companies House
    "default": 0.60,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

def parse_incorporation_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse incorporation date from API response."""
    if not date_str:
        return None

    try:
        # Try full date format (YYYY-MM-DD)
        if len(date_str) == 10 and "-" in date_str:
            return datetime.strptime(date_str, "%Y-%m-%d")
        # Try year only
        if len(date_str) == 4 and date_str.isdigit():
            return datetime(int(date_str), 1, 1)
        return None
    except (ValueError, TypeError):
        return None


def build_canonical_key(jurisdiction: str, company_number: str) -> str:
    """Build canonical key from jurisdiction and company number."""
    return f"corp:{jurisdiction.lower()}:{company_number}"


@dataclass
class CompanyRecord:
    """Parsed company record from OpenCorporates."""

    name: str
    company_number: str
    jurisdiction: str
    incorporation_date: Optional[str] = None
    company_type: Optional[str] = None
    status: Optional[str] = None
    registered_address: Optional[str] = None
    registry_url: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Get display-friendly company name."""
        if self.name:
            return self.name.title()
        return self.name

    @property
    def is_active(self) -> bool:
        """Check if company is active."""
        if not self.status:
            return True  # Assume active if unknown
        return self.status.lower() in ACTIVE_STATUSES

    @property
    def is_dissolved(self) -> bool:
        """Check if company is dissolved."""
        if not self.status:
            return False
        return self.status.lower() in DISSOLVED_STATUSES

    @property
    def canonical_key(self) -> str:
        """Get canonical key for deduplication."""
        return build_canonical_key(self.jurisdiction, self.company_number)

    @property
    def incorporation_datetime(self) -> Optional[datetime]:
        """Get incorporation date as datetime."""
        return parse_incorporation_date(self.incorporation_date)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "CompanyRecord":
        """Create record from API response."""
        company = data.get("company", data)

        return cls(
            name=company.get("name", ""),
            company_number=company.get("company_number", ""),
            jurisdiction=company.get("jurisdiction_code", ""),
            incorporation_date=company.get("incorporation_date"),
            company_type=company.get("company_type"),
            status=company.get("current_status"),
            registered_address=company.get("registered_address_in_full"),
            registry_url=company.get("registry_url"),
            raw_data=company,
        )

    def to_signal_data(self) -> Dict[str, Any]:
        """Convert to signal raw_data format."""
        return {
            "company_name": self.name,
            "display_name": self.display_name,
            "company_number": self.company_number,
            "jurisdiction": self.jurisdiction,
            "jurisdiction_name": SUPPORTED_JURISDICTIONS.get(
                self.jurisdiction, self.jurisdiction
            ),
            "incorporation_date": self.incorporation_date,
            "company_type": self.company_type,
            "status": self.status,
            "is_active": self.is_active,
            "is_dissolved": self.is_dissolved,
            "registered_address": self.registered_address,
            "registry_url": self.registry_url,
            "source": "opencorporates",
        }


# =============================================================================
# COLLECTOR
# =============================================================================

class OpenCorporatesCollector(BaseCollector):
    """
    Collector for OpenCorporates company incorporation data.

    Usage:
        collector = OpenCorporatesCollector(api_key="your_key")

        # Search for company
        results = await collector.search_company("Acme Corp")

        # Collect signals for a company name
        signals = await collector.collect_for_company("Acme Corp")

        # Batch lookup multiple companies
        results = await collector.batch_lookup(["Acme", "Beta", "Gamma"])
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        store=None,
        dry_run: bool = False,
        jurisdictions: Optional[List[str]] = None,
    ):
        """
        Initialize collector.

        Args:
            api_key: OpenCorporates API key (or OPENCORPORATES_API_KEY env var)
            store: SignalStore instance for persistence
            dry_run: If True, don't persist signals
            jurisdictions: List of jurisdiction codes to search (default: US + UK)
        """
        super().__init__(
            collector_name="opencorporates",
            store=store,
        )
        self._dry_run = dry_run

        self._api_key = api_key or os.getenv("OPENCORPORATES_API_KEY")
        if not self._api_key:
            logger.warning("No OpenCorporates API key - searches will be limited")

        self._jurisdictions = jurisdictions or list(SUPPORTED_JURISDICTIONS.keys())
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_time: float = 0

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "DiscoveryEngine/1.0"},
            )
        return self._client

    async def _rate_limit(self):
        """Apply rate limiting between requests."""
        import time

        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make API request with rate limiting."""
        await self._rate_limit()

        client = await self._get_client()

        # Build URL
        url = f"{API_BASE_URL}{endpoint}"

        # Add API key to params
        params = params or {}
        if self._api_key:
            params["api_token"] = self._api_key

        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error("OpenCorporates rate limit exceeded")
            elif e.response.status_code == 404:
                logger.debug(f"Company not found: {endpoint}")
            else:
                logger.error(f"OpenCorporates API error: {e}")
            raise
        except Exception as e:
            logger.error(f"OpenCorporates request failed: {e}")
            raise

    async def search_company(
        self,
        name: str,
        jurisdictions: Optional[List[str]] = None,
        active_only: bool = False,
    ) -> List[CompanyRecord]:
        """
        Search for company by name.

        Args:
            name: Company name to search
            jurisdictions: Limit to these jurisdictions (default: all supported)
            active_only: Only return active companies

        Returns:
            List of matching CompanyRecord objects
        """
        jurisdictions = jurisdictions or self._jurisdictions

        try:
            params = {
                "q": name,
                "per_page": DEFAULT_RESULTS_PER_PAGE,
            }

            # Add jurisdiction filter
            if jurisdictions:
                params["jurisdiction_code"] = "|".join(jurisdictions)

            # Add status filter
            if active_only:
                params["current_status"] = "Active"

            response = await self._make_request("/companies/search", params)

            # Parse results
            companies = response.get("results", {}).get("companies", [])
            records = [CompanyRecord.from_api_response(c) for c in companies]

            logger.info(f"Found {len(records)} companies matching '{name}'")
            return records

        except Exception as e:
            logger.error(f"Search failed for '{name}': {e}")
            return []

    async def get_company_details(
        self,
        jurisdiction: str,
        company_number: str,
    ) -> Optional[CompanyRecord]:
        """
        Get detailed company information.

        Args:
            jurisdiction: Jurisdiction code (e.g., 'us_de')
            company_number: Company registration number

        Returns:
            CompanyRecord with full details, or None if not found
        """
        try:
            endpoint = f"/companies/{jurisdiction}/{company_number}"
            response = await self._make_request(endpoint)

            if "company" in response:
                return CompanyRecord.from_api_response(response)
            return None

        except Exception as e:
            logger.debug(f"Company not found: {jurisdiction}/{company_number}")
            return None

    async def collect_for_company(
        self,
        name: str,
        jurisdictions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Collect incorporation signals for a company name.

        Args:
            name: Company name to look up
            jurisdictions: Limit to these jurisdictions

        Returns:
            List of signal dicts ready for storage
        """
        records = await self.search_company(name, jurisdictions)
        retrieved_at = datetime.now(timezone.utc)

        signals = []
        for record in records:
            # Calculate confidence based on jurisdiction
            confidence = JURISDICTION_CONFIDENCE.get(
                record.jurisdiction,
                JURISDICTION_CONFIDENCE["default"],
            )

            # Reduce confidence for dissolved companies
            if record.is_dissolved:
                confidence *= 0.5

            # Build source URL
            source_url = record.registry_url or f"{API_BASE_URL}/companies/{record.jurisdiction}/{record.company_number}"

            # Create provenance for audit trail
            provenance = create_provenance(
                source_url=source_url,
                response_data=record.raw_data,
                endpoint=f"/companies/{record.jurisdiction}/{record.company_number}",
                query_params={"q": name},
                retrieved_at=retrieved_at,
            )

            # Merge provenance into signal data
            signal_data = record.to_signal_data()
            signal_data.update(provenance)

            signal = {
                "signal_type": "incorporation",
                "source_api": "opencorporates",
                "source_url": source_url,
                "source_response_hash": hash_response(record.raw_data),
                "canonical_key": record.canonical_key,
                "company_name": record.display_name,
                "confidence": confidence,
                "raw_data": signal_data,
                "detected_at": record.incorporation_datetime or retrieved_at,
                "retrieved_at": retrieved_at,
            }

            signals.append(signal)

        return signals

    async def batch_lookup(
        self,
        company_names: List[str],
        jurisdictions: Optional[List[str]] = None,
    ) -> Dict[str, List[CompanyRecord]]:
        """
        Look up multiple companies.

        Args:
            company_names: List of company names to search
            jurisdictions: Limit to these jurisdictions

        Returns:
            Dict mapping company name to list of matching records
        """
        results = {}

        for name in company_names:
            records = await self.search_company(name, jurisdictions)
            results[name] = records

        return results

    async def _collect_signals(self) -> List[Dict[str, Any]]:
        """
        Collect signals (required by BaseCollector abstract method).

        For OpenCorporates, we don't do bulk collection - instead use
        collect_for_company() or batch_lookup() for targeted lookups.
        """
        logger.info("OpenCorporates collector requires company names - use collect_for_company()")
        return []

    async def collect(self) -> List[Dict[str, Any]]:
        """
        Collect signals (public interface).

        For OpenCorporates, we don't do bulk collection - instead use
        collect_for_company() or batch_lookup() for targeted lookups.
        """
        return await self._collect_signals()

    async def lookup_and_corroborate(
        self,
        company_name: str,
        existing_canonical_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Look up company and return corroboration signal if found.

        Used by pipeline to corroborate signals from other sources.

        Args:
            company_name: Company name to look up
            existing_canonical_key: Canonical key from existing signal

        Returns:
            Signal dict if found, None otherwise
        """
        signals = await self.collect_for_company(company_name)

        if not signals:
            return None

        # Return best match (highest confidence, active)
        active_signals = [s for s in signals if s["raw_data"].get("is_active", True)]
        if active_signals:
            return max(active_signals, key=lambda s: s["confidence"])

        # Return first match if no active ones
        return signals[0] if signals else None

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
