"""
Plug and Play Portfolio Collector.

Collects startup data from Plug and Play's industry-specific accelerator batches.
Supports multiple verticals including Travel & Hospitality, Health, Fintech, etc.

Source: https://www.plugandplaytechcenter.com/portfolio/

Usage:
    collector = PlugAndPlayCollector(verticals=["travel", "health"])
    companies = await collector.collect_all()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import httpx  # For future implementation

logger = logging.getLogger(__name__)

# Mapping of vertical names to Plug and Play URL slugs
PLUGANDPLAY_VERTICALS = {
    "travel": "travel-hospitality",
    "health": "health",
    "fintech": "fintech",
    "retail": "brand-retail",
    "supply_chain": "supply-chain",
    "insurtech": "insurtech",
    "mobility": "mobility",
    "food": "food-ag-tech",
    "energy": "energy",
    "real_estate": "real-estate",
}

PLUGANDPLAY_BASE_URL = "https://www.plugandplaytechcenter.com"


@dataclass
class PlugAndPlayCompany:
    """Company from Plug and Play portfolio."""

    name: str
    vertical: str
    description: str
    website: Optional[str]
    batch: Optional[str]
    headquarters: Optional[str]
    collected_at: datetime


class PlugAndPlayCollector:
    """
    Collects portfolio companies from Plug and Play accelerator.

    Implements polite scraping with rate limiting.
    """

    def __init__(
        self,
        verticals: Optional[List[str]] = None,
        rate_limit: float = 1.0
    ):
        """
        Initialize Plug and Play collector.

        Args:
            verticals: List of verticals to collect (default: ["travel"])
            rate_limit: Max requests per second (default: 1.0)
        """
        self.verticals = verticals or ["travel"]
        self.rate_limit = rate_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            if self._last_request_time is not None:
                elapsed = time.time() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.time()

    async def collect_vertical(self, vertical: str) -> List[PlugAndPlayCompany]:
        """
        Collect all companies from a specific vertical.

        Args:
            vertical: Vertical name (e.g., "travel", "health")

        Returns:
            List of PlugAndPlayCompany objects
        """
        if vertical not in PLUGANDPLAY_VERTICALS:
            logger.warning(f"Unknown vertical: {vertical}")
            return []

        try:
            companies = await self._fetch_portfolio(vertical)
            logger.info(f"Collected {len(companies)} companies from {vertical} vertical")
            return companies
        except Exception as e:
            logger.error(f"Error collecting {vertical} portfolio: {e}")
            return []

    async def collect_all(self) -> Dict[str, List[PlugAndPlayCompany]]:
        """
        Collect from all configured verticals.

        Returns:
            Dict mapping vertical names to lists of companies
        """
        results = {}

        for vertical in self.verticals:
            companies = await self.collect_vertical(vertical)
            results[vertical] = companies

        total = sum(len(c) for c in results.values())
        logger.info(f"Collected {total} companies across {len(self.verticals)} verticals")
        return results

    async def _fetch_portfolio(self, vertical: str) -> List[PlugAndPlayCompany]:
        """
        Fetch portfolio companies for a vertical.

        Note: This is a stub implementation. In production, would scrape
        the Plug and Play portfolio page or use their API if available.

        Args:
            vertical: Vertical name

        Returns:
            List of PlugAndPlayCompany objects
        """
        await self._wait_for_rate_limit()

        url_slug = PLUGANDPLAY_VERTICALS.get(vertical, vertical)
        url = f"{PLUGANDPLAY_BASE_URL}/portfolio/?industry={url_slug}"

        # Stub: In production, would scrape the portfolio page
        # The page uses JavaScript rendering, so would need:
        # 1. Playwright/Selenium for JS rendering, or
        # 2. Find their internal API endpoints, or
        # 3. Use a service like ScrapingBee

        logger.debug(f"Would fetch portfolio from: {url}")

        # Return empty list for stub
        # In production: parse HTML/JSON and return companies
        return []

    async def _parse_company_card(self, card_html: str, vertical: str) -> Optional[PlugAndPlayCompany]:
        """
        Parse a company card from the portfolio page.

        Args:
            card_html: HTML of the company card
            vertical: Vertical name

        Returns:
            PlugAndPlayCompany or None if parsing fails
        """
        # Stub: Would parse HTML to extract company details
        # In production: use BeautifulSoup or similar
        return None
