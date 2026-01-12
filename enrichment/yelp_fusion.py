"""
Yelp Fusion API Client for Travel & Hospitality Intelligence.

Provides async methods to search and fetch business data from the Yelp Fusion API.

API Details:
- Base URL: https://api.yelp.com/v3
- Requires API key (Bearer token authentication)
- Rate limit: 5 requests/second (API limit)
- Hourly limit: 50 requests/hour (to avoid free tier abuse detection)
- Search endpoint: /businesses/search
- Business details endpoint: /businesses/{id}

Usage:
    client = YelpFusionClient(api_key="your-api-key")

    # Search for businesses by name and location
    businesses = await client.search_by_name("Joe's Pizza", "New York, NY", max_results=10)

    # Get specific business details
    business = await client.get_business("joes-pizza-new-york")
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

# Yelp Fusion API base URL
YELP_API_BASE = "https://api.yelp.com/v3"


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class YelpBusiness:
    """
    Yelp business record from Yelp Fusion API.

    Represents a business/venue linked to a travel & hospitality entity.
    """

    entity_id: str
    yelp_id: str
    name: str
    rating: float
    review_count: int
    price: Optional[str]
    categories: List[str]
    url: str
    fetched_at: datetime
    id: Optional[int] = None


# =============================================================================
# YELP FUSION CLIENT
# =============================================================================


class YelpFusionClient:
    """
    Async client for Yelp Fusion API.

    Provides methods to search Yelp businesses by name/location and fetch
    individual business details. Implements dual rate limiting for
    API compliance:
    - Per-second: 5 requests/second (Yelp API limit)
    - Hourly: 50 requests/hour (free tier abuse detection avoidance)

    Attributes:
        api_key: Yelp Fusion API key (required).
        rate_limit: Maximum requests per second (default: 5.0).
        hourly_limit: Maximum requests per hour (default: 50).
    """

    def __init__(
        self,
        api_key: str,
        rate_limit: float = 5.0,
        hourly_limit: int = 50,
    ):
        """
        Initialize the Yelp Fusion client.

        Args:
            api_key: Yelp Fusion API key (required for authentication).
            rate_limit: Maximum requests per second (default: 5.0).
            hourly_limit: Maximum requests per hour (default: 50).
        """
        self.api_key = api_key
        self.rate_limit = rate_limit
        self.hourly_limit = hourly_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0
        self._hourly_requests: deque = deque(maxlen=hourly_limit)

    async def _wait_for_rate_limit(self) -> None:
        """
        Wait to comply with rate limiting.

        Implements dual rate limiting:
        1. Per-second limiting using semaphore and minimum interval
        2. Hourly limiting using a deque to track request timestamps
        """
        async with self._semaphore:
            current_time = asyncio.get_event_loop().time()

            # Per-second rate limiting
            if self._last_request_time is not None:
                elapsed = current_time - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)

            # Hourly rate limiting - remove timestamps older than 1 hour
            one_hour_ago = time.time() - 3600
            while self._hourly_requests and self._hourly_requests[0] < one_hour_ago:
                self._hourly_requests.popleft()

            # If we've hit the hourly limit, wait until oldest request expires
            if len(self._hourly_requests) >= self.hourly_limit:
                wait_time = self._hourly_requests[0] - one_hour_ago
                if wait_time > 0:
                    logger.warning(
                        f"Hourly rate limit reached ({self.hourly_limit}/hour). "
                        f"Waiting {wait_time:.1f} seconds."
                    )
                    await asyncio.sleep(wait_time)

            # Record this request
            self._hourly_requests.append(time.time())
            self._last_request_time = asyncio.get_event_loop().time()

    async def _make_request(
        self, endpoint: str, params: Optional[dict] = None
    ) -> dict:
        """
        Make an authenticated API request to Yelp Fusion.

        Args:
            endpoint: API endpoint path (e.g., "/businesses/search").
            params: Optional query parameters.

        Returns:
            JSON response as a dictionary.

        Raises:
            httpx.HTTPStatusError: On HTTP error responses.
            httpx.RequestError: On request failures.
        """
        await self._wait_for_rate_limit()

        url = f"{YELP_API_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()

    async def search_by_name(
        self, name: str, location: str, max_results: int = 10
    ) -> List[YelpBusiness]:
        """
        Search Yelp businesses by name and location.

        Args:
            name: Business name to search for.
            location: Location string (e.g., "San Francisco, CA").
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of YelpBusiness objects matching the search criteria.
        """
        params = {
            "term": name,
            "location": location,
            "limit": min(max_results, 50),  # Yelp API max is 50
        }

        try:
            data = await self._make_request("/businesses/search", params=params)

            businesses = data.get("businesses", [])
            results = []

            for record in businesses:
                try:
                    business = self._parse_business(record)
                    if business:
                        results.append(business)
                except Exception as e:
                    logger.warning(f"Failed to parse Yelp business record: {e}")
                    continue

            logger.info(f"Found {len(results)} Yelp businesses for '{name}' in {location}")
            return results

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error searching Yelp businesses: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error searching Yelp businesses: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error searching Yelp businesses: {e}")
            return []

    async def get_business(self, yelp_id: str) -> Optional[YelpBusiness]:
        """
        Get a specific Yelp business by its Yelp ID.

        Args:
            yelp_id: The Yelp business ID.

        Returns:
            YelpBusiness object if found, None otherwise.
        """
        try:
            data = await self._make_request(f"/businesses/{yelp_id}")
            return self._parse_business(data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Yelp business not found: {yelp_id}")
            else:
                logger.error(f"HTTP error fetching Yelp business {yelp_id}: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching Yelp business {yelp_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching Yelp business {yelp_id}: {e}")
            return None

    def _parse_business(self, record: dict) -> Optional[YelpBusiness]:
        """
        Parse a Yelp business record from the API response into a YelpBusiness dataclass.

        Args:
            record: Raw business record from the API response.

        Returns:
            YelpBusiness object, or None if required fields are missing.
        """
        try:
            yelp_id = record.get("id", "")
            name = record.get("name", "")

            if not yelp_id:
                logger.warning("Yelp business record missing id, skipping")
                return None

            if not name:
                logger.warning("Yelp business record missing name, skipping")
                return None

            # Extract category titles
            categories = [
                cat.get("title", "")
                for cat in record.get("categories", [])
                if cat.get("title")
            ]

            return YelpBusiness(
                entity_id="",  # Will be set when saving to storage
                yelp_id=yelp_id,
                name=name,
                rating=record.get("rating", 0.0),
                review_count=record.get("review_count", 0),
                price=record.get("price"),
                categories=categories,
                url=record.get("url", ""),
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.error(f"Error parsing Yelp business record: {e}")
            return None
