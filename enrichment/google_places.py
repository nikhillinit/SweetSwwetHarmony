"""
Google Places API Client for Travel & Hospitality Intelligence.

Provides async methods to search and fetch place data from the Google Places API.

API Details:
- Base URL: https://maps.googleapis.com/maps/api/place
- Requires API key (query parameter authentication)
- Hourly limit: 50 requests/hour (to stay in free tier)
- Search endpoint: /textsearch/json
- Details endpoint: /details/json

Usage:
    client = GooglePlacesClient(api_key="your-api-key")

    # Search for places by query and location
    places = await client.search_places("Joe's Pizza", location="New York, NY", max_results=10)

    # Get specific place details
    place = await client.get_place_details("ChIJN1t_tDeuEmsRUsoyG83frY4")
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

# Google Places API base URL
GOOGLE_PLACES_API_BASE = "https://maps.googleapis.com/maps/api/place"


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class GooglePlace:
    """
    Google Place record from Google Places API.

    Represents a place/venue linked to a travel & hospitality entity.
    """

    entity_id: str
    place_id: str
    name: str
    rating: float
    user_ratings_total: int
    price_level: Optional[int]  # 0-4
    types: List[str]
    website: Optional[str]
    fetched_at: datetime
    id: Optional[int] = None


# =============================================================================
# GOOGLE PLACES CLIENT
# =============================================================================


class GooglePlacesClient:
    """
    Async client for Google Places API.

    Provides methods to search Google Places by query/location and fetch
    individual place details. Implements hourly rate limiting for
    free tier compliance:
    - Hourly: 50 requests/hour (default, to stay in free tier)

    Attributes:
        api_key: Google Places API key (required).
        hourly_limit: Maximum requests per hour (default: 50).
    """

    def __init__(
        self,
        api_key: str,
        hourly_limit: int = 50,
    ):
        """
        Initialize the Google Places client.

        Args:
            api_key: Google Places API key (required for authentication).
            hourly_limit: Maximum requests per hour (default: 50).
        """
        self.api_key = api_key
        self.hourly_limit = hourly_limit
        self._semaphore = asyncio.Semaphore(1)
        self._hourly_requests: deque = deque(maxlen=hourly_limit)

    async def _wait_for_rate_limit(self) -> None:
        """
        Wait to comply with rate limiting.

        Implements hourly rate limiting using a deque to track request timestamps.
        """
        async with self._semaphore:
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

    async def _make_request(
        self, endpoint: str, params: Optional[dict] = None
    ) -> dict:
        """
        Make an authenticated API request to Google Places.

        Args:
            endpoint: API endpoint path (e.g., "/textsearch/json").
            params: Optional query parameters.

        Returns:
            JSON response as a dictionary.

        Raises:
            httpx.HTTPStatusError: On HTTP error responses.
            httpx.RequestError: On request failures.
        """
        await self._wait_for_rate_limit()

        url = f"{GOOGLE_PLACES_API_BASE}{endpoint}"

        # Add API key to params
        if params is None:
            params = {}
        params["key"] = self.api_key

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def search_places(
        self, query: str, location: Optional[str] = None, max_results: int = 10
    ) -> List[GooglePlace]:
        """
        Search Google Places by query and optional location.

        Args:
            query: Search query string (e.g., business name).
            location: Optional location string (e.g., "San Francisco, CA").
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of GooglePlace objects matching the search criteria.
        """
        params = {
            "query": query,
        }

        if location:
            params["query"] = f"{query} {location}"

        try:
            data = await self._make_request("/textsearch/json", params=params)

            # Check for API-level errors
            status = data.get("status", "")
            if status not in ("OK", "ZERO_RESULTS"):
                logger.error(f"Google Places API error: {status}")
                return []

            results_list = data.get("results", [])
            results = []

            for record in results_list[:max_results]:
                try:
                    place = self._parse_place(record)
                    if place:
                        results.append(place)
                except Exception as e:
                    logger.warning(f"Failed to parse Google Place record: {e}")
                    continue

            logger.info(f"Found {len(results)} Google Places for '{query}'")
            return results

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error searching Google Places: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error searching Google Places: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error searching Google Places: {e}")
            return []

    async def get_place_details(self, place_id: str) -> Optional[GooglePlace]:
        """
        Get a specific Google Place by its place ID.

        Args:
            place_id: The Google Place ID.

        Returns:
            GooglePlace object if found, None otherwise.
        """
        params = {
            "place_id": place_id,
            "fields": "place_id,name,rating,user_ratings_total,price_level,types,website",
        }

        try:
            data = await self._make_request("/details/json", params=params)

            # Check for API-level errors
            status = data.get("status", "")
            if status == "NOT_FOUND":
                logger.warning(f"Google Place not found: {place_id}")
                return None
            if status not in ("OK",):
                logger.error(f"Google Places API error: {status}")
                return None

            result = data.get("result", {})
            if not result:
                return None

            return self._parse_place(result)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Google Place not found: {place_id}")
            else:
                logger.error(f"HTTP error fetching Google Place {place_id}: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching Google Place {place_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching Google Place {place_id}: {e}")
            return None

    def _parse_place(self, record: dict) -> Optional[GooglePlace]:
        """
        Parse a Google Place record from the API response into a GooglePlace dataclass.

        Args:
            record: Raw place record from the API response.

        Returns:
            GooglePlace object, or None if required fields are missing.
        """
        try:
            place_id = record.get("place_id", "")
            name = record.get("name", "")

            if not place_id:
                logger.warning("Google Place record missing place_id, skipping")
                return None

            if not name:
                logger.warning("Google Place record missing name, skipping")
                return None

            return GooglePlace(
                entity_id="",  # Will be set when saving to storage
                place_id=place_id,
                name=name,
                rating=record.get("rating", 0.0),
                user_ratings_total=record.get("user_ratings_total", 0),
                price_level=record.get("price_level"),
                types=record.get("types", []),
                website=record.get("website"),
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.error(f"Error parsing Google Place record: {e}")
            return None
