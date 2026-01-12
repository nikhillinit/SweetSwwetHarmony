"""
G2Crowd Collector for SaaS Product Intelligence.

Collects SaaS product data from G2Crowd including:
- Product listings by category
- Ratings and review counts
- Vendor information
- Feature and pricing data

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


G2_CATEGORIES: Dict[str, str] = {
    "crm": "crm-software",
    "project_management": "project-management-software",
    "developer_tools": "developer-tools",
    "marketing_automation": "marketing-automation",
    "hr_software": "hr-management-software",
    "erp": "erp-software",
    "analytics": "business-intelligence-software",
    "communication": "team-communication-software",
    "sales": "sales-software",
    "customer_support": "help-desk-software",
}


@dataclass
class G2Product:
    """G2Crowd product listing."""

    name: str
    slug: str
    category: str
    rating: float
    review_count: int
    description: str
    vendor: str
    url: str
    features: Optional[List[str]] = None
    pricing: Optional[str] = None


class G2CrowdCollector:
    """
    Collects SaaS product data from G2Crowd.

    Features:
    - Rate-limited requests to avoid throttling
    - Graceful error handling for partial failures
    - Configurable category filtering
    - Debug logging for all operations
    """

    BASE_URL = "https://www.g2.com"
    RATE_LIMIT_DELAY = 1.0  # seconds between requests

    def __init__(
        self,
        categories: Optional[List[str]] = None,
        api_key: Optional[str] = None
    ):
        """
        Initialize G2Crowd collector.

        Args:
            categories: List of category keys to collect. Defaults to all.
            api_key: Optional API key for authenticated access.
        """
        self.categories = categories or list(G2_CATEGORIES.keys())
        self.api_key = api_key
        self._last_request_time = 0.0
        logger.debug(f"G2CrowdCollector initialized with {len(self.categories)} categories")

    async def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    async def _fetch_category(self, category: str) -> List[G2Product]:
        """
        Fetch products from a G2 category page.

        Args:
            category: Category key to fetch.

        Returns:
            List of G2Product objects.
        """
        await self._rate_limit()

        category_slug = G2_CATEGORIES.get(category, category)
        url = f"{self.BASE_URL}/categories/{category_slug}"

        logger.debug(f"Fetching G2 category: {category} from {url}")

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

                # Parse response (simplified - real impl would parse HTML/JSON)
                products = self._parse_products(response.text, category)
                logger.debug(f"Fetched {len(products)} products from {category}")
                return products

            except httpx.HTTPError as e:
                logger.error(f"HTTP error fetching {category}: {e}")
                return []

    def _parse_products(self, html: str, category: str) -> List[G2Product]:
        """
        Parse products from G2 response.

        Args:
            html: Response body.
            category: Category being parsed.

        Returns:
            List of G2Product objects.

        Note:
            This is a placeholder implementation. Production code would
            parse the actual HTML or use G2's API response format.
        """
        # Placeholder - real implementation would parse HTML or use API
        # For now, return empty list (tests mock this method)
        return []

    async def collect_category(self, category: str) -> List[G2Product]:
        """
        Collect products from a single category.

        Args:
            category: Category key to collect.

        Returns:
            List of G2Product objects.
        """
        logger.debug(f"Collecting G2 category: {category}")
        return await self._fetch_category(category)

    async def collect_all(self) -> Dict[str, List[G2Product]]:
        """
        Collect products from all configured categories.

        Returns:
            Dict mapping category keys to product lists.
        """
        results: Dict[str, List[G2Product]] = {}

        for category in self.categories:
            try:
                products = await self._fetch_category(category)
                results[category] = products
            except Exception as e:
                logger.error(f"Error collecting {category}: {e}")
                results[category] = []

        logger.debug(f"Collected products from {len(results)} categories")
        return results

    async def search_product(self, query: str) -> List[G2Product]:
        """
        Search for products by name or keyword.

        Args:
            query: Search query string.

        Returns:
            List of matching G2Product objects.
        """
        await self._rate_limit()

        url = f"{self.BASE_URL}/search"
        logger.debug(f"Searching G2 for: {query}")

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
