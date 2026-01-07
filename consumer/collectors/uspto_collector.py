"""
USPTO Trademark Collector

Collects new trademark applications from USPTO for consumer goods.

Uses TSDR (Trademark Status & Document Retrieval) API.
Filters by Nice Classification for consumer-relevant classes:
- Class 29: Meat, fish, poultry, preserved foods
- Class 30: Coffee, tea, baked goods, confectionery
- Class 32: Beers, non-alcoholic beverages
- Class 33: Alcoholic beverages
- Class 3: Cosmetics, cleaning preparations
- Class 5: Pharmaceuticals, dietary supplements
- Class 25: Clothing, footwear, headwear
- Class 28: Games, toys, sporting goods

Note: USPTO API requires registration for full access.
This collector uses the public TSDR data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from .base import ConsumerCollector, Signal

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# USPTO TSDR API endpoints
USPTO_TSDR_API = "https://tsdrapi.uspto.gov"

# Nice Classification classes for consumer goods
CONSUMER_NICE_CLASSES = {
    3: "Cosmetics, cleaning preparations",
    5: "Pharmaceuticals, dietary supplements",
    25: "Clothing, footwear, headwear",
    28: "Games, toys, sporting goods",
    29: "Meat, fish, preserved foods",
    30: "Coffee, tea, baked goods, confectionery",
    32: "Beers, non-alcoholic beverages",
    33: "Alcoholic beverages",
    35: "Retail services",  # Often consumer-facing
}


# =============================================================================
# USPTO COLLECTOR
# =============================================================================

class USPTOCollector(ConsumerCollector):
    """
    USPTO Trademark collector.

    Collects new trademark applications in consumer goods categories.

    Note: Full API access requires USPTO developer account.
    This implementation uses available public data.

    Usage:
        async with consumer_store("db.sqlite") as store:
            collector = USPTOCollector(store)
            result = await collector.run()
    """

    name = "uspto_tm"

    def __init__(
        self,
        store=None,
        days_lookback: int = 7,
        nice_classes: Optional[List[int]] = None,
    ):
        """
        Initialize USPTO collector.

        Args:
            store: ConsumerStore instance
            days_lookback: Days to look back for new filings
            nice_classes: Nice Classification classes to filter (default: consumer goods)
        """
        super().__init__(store)
        self.days_lookback = days_lookback
        self.nice_classes = nice_classes or list(CONSUMER_NICE_CLASSES.keys())
        self._session: Optional[aiohttp.ClientSession] = None

    async def collect(self) -> List[Signal]:
        """
        Collect trademark applications.

        Note: USPTO public API has limitations. This collector
        demonstrates the pattern - full implementation would use
        USPTO developer API or bulk data downloads.

        Returns:
            List of Signal objects
        """
        signals = []

        async with aiohttp.ClientSession() as session:
            self._session = session

            # Collect from each Nice class
            for nice_class in self.nice_classes:
                try:
                    class_signals = await self._search_by_class(nice_class)
                    signals.extend(class_signals)
                except Exception as e:
                    logger.error(f"USPTO class {nice_class} search failed: {e}")

            logger.info(f"USPTO: Collected {len(signals)} trademark signals")

        return signals

    async def _search_by_class(self, nice_class: int) -> List[Signal]:
        """
        Search for trademarks in a Nice Classification class.

        Note: This is a simplified implementation. Full implementation
        would use USPTO's TSDR API with proper authentication.
        """
        # USPTO TSDR search endpoint
        # Note: This is illustrative - actual USPTO API differs
        url = f"{USPTO_TSDR_API}/ts/cd/casestatus"

        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.days_lookback)

        params = {
            "class": nice_class,
            "filed_since": start_date.strftime("%Y-%m-%d"),
            "filed_before": end_date.strftime("%Y-%m-%d"),
        }

        signals = []

        try:
            async with self._session.get(url, params=params) as response:
                self.track_api_call()

                if response.status == 200:
                    data = await response.json()
                    trademarks = data.get("trademarks", [])

                    for tm in trademarks:
                        signal = self._trademark_to_signal(tm, nice_class)
                        signals.append(signal)

                elif response.status == 403:
                    # API key required - log and continue
                    logger.debug(f"USPTO API requires authentication for class {nice_class}")
                else:
                    logger.warning(f"USPTO API returned {response.status}")

        except aiohttp.ClientError as e:
            logger.debug(f"USPTO API not available: {e}")

        return signals

    def _trademark_to_signal(
        self,
        tm: Dict[str, Any],
        nice_class: int,
    ) -> Signal:
        """Convert trademark data to Signal."""
        serial_number = tm.get("serial_number", "")
        mark = tm.get("mark_literal", tm.get("mark", ""))
        owner = tm.get("owner_name", tm.get("applicant", ""))
        filing_date = tm.get("filing_date", "")
        goods_services = tm.get("goods_services_description", "")

        # Build source context
        class_description = CONSUMER_NICE_CLASSES.get(nice_class, f"Class {nice_class}")
        context = f"Trademark '{mark}' filed by {owner}. {class_description}."
        if goods_services:
            context += f" Goods/Services: {goods_services[:200]}"

        return Signal(
            source_api="uspto_tm",
            source_id=serial_number,
            signal_type="trademark_filing",
            title=f"TM: {mark} by {owner}",
            url=f"https://tsdr.uspto.gov/#caseNumber={serial_number}&caseSearchType=US_APPLICATION&caseType=DEFAULT",
            source_context=context[:500],
            raw_metadata={
                "serial_number": serial_number,
                "mark": mark,
                "owner": owner,
                "filing_date": filing_date,
                "nice_class": nice_class,
                "goods_services": goods_services[:500] if goods_services else None,
            },
            extracted_company_name=owner if owner else None,
        )

    @staticmethod
    def get_nice_class_description(nice_class: int) -> str:
        """Get description for a Nice Classification class."""
        return CONSUMER_NICE_CLASSES.get(nice_class, f"Unknown class {nice_class}")


# =============================================================================
# BULK DATA ALTERNATIVE
# =============================================================================

class USPTOBulkCollector(ConsumerCollector):
    """
    USPTO collector using bulk data downloads.

    USPTO provides weekly XML bulk downloads at:
    https://bulkdata.uspto.gov/

    This is more reliable than the API for historical data.
    """

    name = "uspto_bulk"

    def __init__(self, store=None):
        super().__init__(store)
        logger.warning(
            "USPTOBulkCollector requires downloading bulk XML files. "
            "See https://bulkdata.uspto.gov/ for data access."
        )

    async def collect(self) -> List[Signal]:
        """
        Collect from bulk data.

        Implementation would:
        1. Download weekly XML file from USPTO bulk data
        2. Parse and filter by Nice Classification
        3. Convert to signals

        For now, returns empty list.
        """
        logger.info("USPTO bulk collector not yet implemented")
        return []
