"""
ClinicalTrials.gov API Client for Digital Health Intelligence.

Provides async methods to search and fetch clinical trial data from
ClinicalTrials.gov's public API v2.

API Details:
- Base URL: https://clinicaltrials.gov/api/v2/studies
- Free, no API key required
- Rate limit: 3 requests/second
- Returns JSON with study details

Usage:
    client = ClinicalTrialsClient()

    # Search by sponsor (company name)
    trials = await client.search_by_sponsor("Pfizer", max_results=10)

    # Search by condition
    trials = await client.search_by_condition("diabetes", max_results=10)

    # Get specific study by NCT ID
    trial = await client.get_study("NCT12345678")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

import httpx

from storage.health_enrichment import ClinicalTrial

logger = logging.getLogger(__name__)

# ClinicalTrials.gov API v2 base URL
CLINICAL_TRIALS_API_BASE = "https://clinicaltrials.gov/api/v2/studies"


class ClinicalTrialsClient:
    """
    Async client for ClinicalTrials.gov API.

    Provides methods to search clinical trials by sponsor or condition,
    and fetch individual study details. Implements rate limiting for
    API compliance.

    Attributes:
        rate_limit: Maximum requests per second (default: 3.0)
    """

    def __init__(self, rate_limit: float = 3.0):
        """
        Initialize the ClinicalTrials.gov client.

        Args:
            rate_limit: Maximum requests per second (default: 3.0)
        """
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

    async def search_by_sponsor(
        self, sponsor_name: str, max_results: int = 10
    ) -> List[ClinicalTrial]:
        """
        Search clinical trials by sponsor/company name.

        Args:
            sponsor_name: Name of the sponsoring company or organization.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of ClinicalTrial objects matching the sponsor name.
        """
        params = {
            "query.spons": sponsor_name,
            "pageSize": min(max_results, 100),
            "format": "json",
        }

        return await self._search(params)

    async def search_by_condition(
        self, condition: str, max_results: int = 10
    ) -> List[ClinicalTrial]:
        """
        Search clinical trials by medical condition.

        Args:
            condition: Medical condition or disease name to search for.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of ClinicalTrial objects matching the condition.
        """
        params = {
            "query.cond": condition,
            "pageSize": min(max_results, 100),
            "format": "json",
        }

        return await self._search(params)

    async def get_study(self, nct_id: str) -> Optional[ClinicalTrial]:
        """
        Get a specific clinical trial by its NCT ID.

        Args:
            nct_id: The NCT identifier (e.g., "NCT12345678").

        Returns:
            ClinicalTrial object if found, None otherwise.
        """
        url = f"{CLINICAL_TRIALS_API_BASE}/{nct_id}"

        try:
            await self._wait_for_rate_limit()

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params={"format": "json"})
                response.raise_for_status()
                data = response.json()

                return self._parse_study(data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Study not found: {nct_id}")
            else:
                logger.error(f"HTTP error fetching study {nct_id}: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching study {nct_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching study {nct_id}: {e}")
            return None

    async def _search(self, params: dict) -> List[ClinicalTrial]:
        """
        Execute a search query against the ClinicalTrials.gov API.

        Args:
            params: Query parameters for the API request.

        Returns:
            List of ClinicalTrial objects from search results.
        """
        try:
            await self._wait_for_rate_limit()

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(CLINICAL_TRIALS_API_BASE, params=params)
                response.raise_for_status()
                data = response.json()

                studies = data.get("studies", [])
                trials = []

                for study in studies:
                    try:
                        trial = self._parse_study(study)
                        if trial:
                            trials.append(trial)
                    except Exception as e:
                        logger.warning(f"Failed to parse study: {e}")
                        continue

                logger.info(f"Found {len(trials)} clinical trials")
                return trials

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error searching clinical trials: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error searching clinical trials: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error searching clinical trials: {e}")
            return []

    def _parse_study(self, study_data: dict) -> Optional[ClinicalTrial]:
        """
        Parse a study from the API response into a ClinicalTrial dataclass.

        Args:
            study_data: Raw study data from the API response.

        Returns:
            ClinicalTrial object, or None if required fields are missing.
        """
        try:
            protocol = study_data.get("protocolSection", {})

            # Identification module
            identification = protocol.get("identificationModule", {})
            nct_id = identification.get("nctId", "")
            title = identification.get("officialTitle") or identification.get("briefTitle", "")

            if not nct_id:
                logger.warning("Study missing NCT ID, skipping")
                return None

            # Status module
            status_module = protocol.get("statusModule", {})
            status = status_module.get("overallStatus")

            # Parse start date
            start_date = None
            start_date_struct = status_module.get("startDateStruct", {})
            if start_date_struct:
                start_date = self._parse_date(start_date_struct.get("date"))

            # Parse completion date
            completion_date = None
            completion_date_struct = status_module.get("completionDateStruct", {})
            if completion_date_struct:
                completion_date = self._parse_date(completion_date_struct.get("date"))

            # Design module
            design = protocol.get("designModule", {})
            phases_list = design.get("phases", [])
            phase = self._normalize_phase(phases_list[0]) if phases_list else None

            # Enrollment
            enrollment_info = design.get("enrollmentInfo", {})
            enrollment = enrollment_info.get("count")

            # Sponsor/collaborators module
            sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
            lead_sponsor = sponsor_module.get("leadSponsor", {})
            sponsor_name = lead_sponsor.get("name", "")

            # Conditions module
            conditions_module = protocol.get("conditionsModule", {})
            conditions = conditions_module.get("conditions", [])

            return ClinicalTrial(
                entity_id="",  # Will be set when saving to storage
                nct_id=nct_id,
                title=title,
                phase=phase,
                status=status,
                enrollment=enrollment,
                conditions=conditions,
                start_date=start_date,
                completion_date=completion_date,
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.error(f"Error parsing study data: {e}")
            return None

    def _normalize_phase(self, phase_str: str) -> str:
        """
        Normalize phase string from API format.

        Converts API format (e.g., "PHASE2") to display format (e.g., "Phase 2").

        Args:
            phase_str: Phase string from API (e.g., "PHASE1", "PHASE2", "PHASE3").

        Returns:
            Normalized phase string (e.g., "Phase 1", "Phase 2", "Phase 3").
        """
        if not phase_str:
            return ""

        phase_mapping = {
            "EARLY_PHASE1": "Early Phase 1",
            "PHASE1": "Phase 1",
            "PHASE2": "Phase 2",
            "PHASE3": "Phase 3",
            "PHASE4": "Phase 4",
            "NA": "Not Applicable",
        }

        return phase_mapping.get(phase_str.upper(), phase_str)

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Parse a date string from the API response.

        Handles various date formats from the API.

        Args:
            date_str: Date string from API (e.g., "2023-01-15", "January 2023").

        Returns:
            datetime.date object, or None if parsing fails.
        """
        if not date_str:
            return None

        # Try common formats
        formats = [
            "%Y-%m-%d",
            "%Y-%m",
            "%B %Y",
            "%B %d, %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        logger.debug(f"Unable to parse date: {date_str}")
        return None
