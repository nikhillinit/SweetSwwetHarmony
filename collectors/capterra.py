"""
Capterra Collector for SaaS Product Intelligence.

Collects SaaS product data from Capterra including:
- Product listings by category
- Ratings (overall, ease of use, value for money)
- Review counts
- Vendor and feature information

Uses rate-limited HTTP requests with graceful error handling.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


CAPTERRA_CATEGORIES: Dict[str, str] = {
    "project_management": "project-management-software",
    "accounting": "accounting-software",
    "crm": "crm-software",
    "hr": "human-resources-software",
    "marketing": "marketing-software",
    "sales": "sales-software",
    "helpdesk": "help-desk-software",
    "ecommerce": "ecommerce-software",
    "inventory": "inventory-management-software",
    "scheduling": "appointment-scheduling-software",
}


@dataclass
class CapterraProduct:
    """Capterra product listing."""

    name: str
    slug: str
    category: str
    overall_rating: float
    review_count: int
    description: str
    vendor: str
    ease_of_use_rating: float
    value_for_money_rating: float
    features: Optional[List[str]] = None
    pricing_model: Optional[str] = None


class CapterraCollector:
    """
    Collects SaaS product data from Capterra.

    Features:
    - Rate-limited requests to avoid throttling
    - Graceful error handling for partial failures
    - Configurable category filtering
    - Debug logging for all operations
    """

    BASE_URL = "https://www.capterra.com"
    RATE_LIMIT_DELAY = 1.0  # seconds between requests

    def __init__(
        self,
        categories: Optional[List[str]] = None,
        api_key: Optional[str] = None
    ):
        """
        Initialize Capterra collector.

        Args:
            categories: List of category keys to collect. Defaults to all.
            api_key: Optional API key for authenticated access.
        """
        self.categories = categories or list(CAPTERRA_CATEGORIES.keys())
        self.api_key = api_key
        self._last_request_time = 0.0
        logger.debug(f"CapterraCollector initialized with {len(self.categories)} categories")

    async def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    async def _fetch_category(self, category: str) -> List[CapterraProduct]:
        """
        Fetch products from a Capterra category.

        Args:
            category: Category key to fetch.

        Returns:
            List of CapterraProduct objects.
        """
        await self._rate_limit()

        category_slug = CAPTERRA_CATEGORIES.get(category, category)
        url = f"{self.BASE_URL}/directory/{category_slug}"

        logger.debug(f"Fetching Capterra category: {category} from {url}")

        async with httpx.AsyncClient() as client:
            try:
                headers = {"User-Agent": "HarmonicBot/1.0"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                response = await client.get(
                    url,
                    headers=headers,
                    timeout=30.0
                )
                response.raise_for_status()

                products = self._parse_products(response.text, category)
                logger.debug(f"Fetched {len(products)} products from {category}")
                return products

            except httpx.HTTPError as e:
                logger.error(f"HTTP error fetching {category}: {e}")
                return []

    def _parse_products(self, html: str, category: str) -> List[CapterraProduct]:
        """
        Parse products from Capterra response.

        Args:
            html: Response body.
            category: Category being parsed.

        Returns:
            List of CapterraProduct objects.

        Note:
            This is a placeholder implementation. Production code would
            parse the actual HTML or use Capterra's API response format.
        """
        # Placeholder - real implementation would parse HTML or use API
        return []

    async def collect_category(self, category: str) -> List[CapterraProduct]:
        """
        Collect products from a single category.

        Args:
            category: Category key to collect.

        Returns:
            List of CapterraProduct objects.
        """
        logger.debug(f"Collecting Capterra category: {category}")
        return await self._fetch_category(category)

    async def collect_all(self) -> Dict[str, List[CapterraProduct]]:
        """
        Collect products from all configured categories.

        Returns:
            Dict mapping category keys to product lists.
        """
        results: Dict[str, List[CapterraProduct]] = {}

        for category in self.categories:
            try:
                products = await self._fetch_category(category)
                results[category] = products
            except Exception as e:
                logger.error(f"Error collecting {category}: {e}")
                results[category] = []

        logger.debug(f"Collected products from {len(results)} categories")
        return results

    async def search_product(self, query: str) -> List[CapterraProduct]:
        """
        Search for products by name or keyword.

        Args:
            query: Search query string.

        Returns:
            List of matching CapterraProduct objects.
        """
        await self._rate_limit()

        url = f"{self.BASE_URL}/search"
        logger.debug(f"Searching Capterra for: {query}")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url,
                    params={"query": query},
                    headers={"User-Agent": "HarmonicBot/1.0"},
                    timeout=30.0
                )
                response.raise_for_status()
                return self._parse_products(response.text, "search")
            except httpx.HTTPError as e:
                logger.error(f"HTTP error searching for {query}: {e}")
                return []
