"""
OpenFDA API Client for Digital Health Intelligence.

Provides async methods to search and fetch FDA 510(k) clearance data from
the OpenFDA API.

API Details:
- Base URL: https://api.fda.gov/device/510k.json
- Free, no API key required (but key increases rate limit)
- Rate limit: 240 requests/minute without key (4 requests/second)
- Returns JSON with device clearance details

Usage:
    client = OpenFDAClient()

    # Search by applicant/company name
    clearances = await client.search_510k_by_applicant("Medtronic", max_results=10)

    # Search by device name
    clearances = await client.search_510k_by_device("cardiac monitor", max_results=10)

    # Get specific 510k by application number
    clearance = await client.get_510k("K123456")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import List, Optional

import httpx

from storage.health_enrichment import FDAClearance

logger = logging.getLogger(__name__)

# OpenFDA 510k API base URL
OPENFDA_510K_API_BASE = "https://api.fda.gov/device/510k.json"


class OpenFDAClient:
    """
    Async client for OpenFDA 510(k) API.

    Provides methods to search FDA device clearances by applicant or device name,
    and fetch individual 510(k) details. Implements rate limiting for
    API compliance.

    Attributes:
        api_key: Optional API key for higher rate limits.
        rate_limit: Maximum requests per second (default: 4.0).
    """

    def __init__(self, api_key: Optional[str] = None, rate_limit: float = 4.0):
        """
        Initialize the OpenFDA client.

        Args:
            api_key: Optional API key for higher rate limits.
            rate_limit: Maximum requests per second (default: 4.0).
        """
        self.api_key = api_key
        self.rate_limit = rate_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            if self._last_request_time is not None:
                elapsed = asyncio.get_event_loop().time() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def search_510k_by_applicant(
        self, applicant_name: str, max_results: int = 10
    ) -> List[FDAClearance]:
        """
        Search 510(k) clearances by applicant/company name.

        Args:
            applicant_name: Name of the applicant company.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of FDAClearance objects matching the applicant name.
        """
        # OpenFDA uses Lucene query syntax
        search_query = f'applicant:"{applicant_name}"'
        params = {
            "search": search_query,
            "limit": min(max_results, 100),
        }

        return await self._search(params)

    async def search_510k_by_device(
        self, device_name: str, max_results: int = 10
    ) -> List[FDAClearance]:
        """
        Search 510(k) clearances by device name.

        Args:
            device_name: Name or description of the device.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of FDAClearance objects matching the device name.
        """
        # OpenFDA uses Lucene query syntax
        search_query = f'device_name:"{device_name}"'
        params = {
            "search": search_query,
            "limit": min(max_results, 100),
        }

        return await self._search(params)

    async def get_510k(self, application_number: str) -> Optional[FDAClearance]:
        """
        Get a specific 510(k) clearance by its application number.

        Args:
            application_number: The 510(k) number (e.g., "K123456").

        Returns:
            FDAClearance object if found, None otherwise.
        """
        # Ensure K number format
        k_number = application_number.upper()
        search_query = f'k_number:"{k_number}"'
        params = {
            "search": search_query,
            "limit": 1,
        }

        if self.api_key:
            params["api_key"] = self.api_key

        try:
            await self._wait_for_rate_limit()

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(OPENFDA_510K_API_BASE, params=params)
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                if not results:
                    logger.warning(f"510(k) not found: {application_number}")
                    return None

                return self._parse_510k(results[0])

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"510(k) not found: {application_number}")
            else:
                logger.error(f"HTTP error fetching 510(k) {application_number}: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching 510(k) {application_number}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching 510(k) {application_number}: {e}")
            return None

    async def _search(self, params: dict) -> List[FDAClearance]:
        """
        Execute a search query against the OpenFDA API.

        Args:
            params: Query parameters for the API request.

        Returns:
            List of FDAClearance objects from search results.
        """
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            await self._wait_for_rate_limit()

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(OPENFDA_510K_API_BASE, params=params)
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                clearances = []

                for record in results:
                    try:
                        clearance = self._parse_510k(record)
                        if clearance:
                            clearances.append(clearance)
                    except Exception as e:
                        logger.warning(f"Failed to parse 510(k) record: {e}")
                        continue

                logger.info(f"Found {len(clearances)} FDA 510(k) clearances")
                return clearances

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error searching 510(k) clearances: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error searching 510(k) clearances: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error searching 510(k) clearances: {e}")
            return []

    def _parse_510k(self, record: dict) -> Optional[FDAClearance]:
        """
        Parse a 510(k) record from the API response into an FDAClearance dataclass.

        Args:
            record: Raw 510(k) record from the API response.

        Returns:
            FDAClearance object, or None if required fields are missing.
        """
        try:
            k_number = record.get("k_number", "")
            device_name = record.get("device_name", "")

            if not k_number:
                logger.warning("510(k) record missing k_number, skipping")
                return None

            if not device_name:
                logger.warning("510(k) record missing device_name, skipping")
                return None

            # Normalize device class from "1"/"2"/"3" to "I"/"II"/"III"
            device_class = self._normalize_device_class(record.get("device_class"))

            # Parse decision date
            decision_date = self._parse_date(record.get("decision_date"))

            # Decision description
            decision = record.get("decision_description")

            return FDAClearance(
                entity_id="",  # Will be set when saving to storage
                application_number=k_number,
                device_name=device_name,
                device_class=device_class,
                clearance_type="510k",
                decision=decision,
                decision_date=decision_date,
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.error(f"Error parsing 510(k) record: {e}")
            return None

    def _normalize_device_class(self, device_class: Optional[str]) -> Optional[str]:
        """
        Normalize device class from numeric to Roman numeral format.

        Args:
            device_class: Device class string from API ("1", "2", or "3").

        Returns:
            Normalized device class ("I", "II", or "III"), or None if not provided.
        """
        if not device_class:
            return None

        class_mapping = {
            "1": "I",
            "2": "II",
            "3": "III",
        }

        return class_mapping.get(device_class, device_class)

    def _parse_date(self, date_str: Optional[str]) -> Optional[date]:
        """
        Parse a date string from the API response.

        Args:
            date_str: Date string from API (e.g., "2024-01-15").

        Returns:
            datetime.date object, or None if parsing fails.
        """
        if not date_str:
            return None

        # OpenFDA typically uses YYYY-MM-DD format
        formats = [
            "%Y-%m-%d",
            "%Y%m%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        logger.debug(f"Unable to parse date: {date_str}")
        return None
