"""
Consumer Enrichment Orchestrator.

Orchestrates consumer enrichment from multiple sources:
- Brand sentiment for DTC brands
- Community metrics for marketplace platforms
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from enrichment.brand_sentiment import BrandSentimentClient, SentimentResult
from enrichment.community_metrics import CommunityMetricsClient, CommunityMetrics

logger = logging.getLogger(__name__)


@dataclass
class ConsumerEnrichmentResult:
    """Result of consumer enrichment."""
    entity_id: str
    sub_vertical: str
    brand_sentiment: Optional[SentimentResult]
    community_metrics: Optional[CommunityMetrics]
    success: bool
    errors: List[str] = field(default_factory=list)


class ConsumerEnrichmentOrchestrator:
    """Orchestrates consumer enrichment from multiple sources."""

    def __init__(
        self,
        sentiment_client: Optional[BrandSentimentClient] = None,
        metrics_client: Optional[CommunityMetricsClient] = None
    ):
        self.sentiment_client = sentiment_client or BrandSentimentClient()
        self.metrics_client = metrics_client or CommunityMetricsClient()
        logger.debug("ConsumerEnrichmentOrchestrator initialized")

    async def _enrich_brand_sentiment(
        self,
        company_name: str
    ) -> Optional[SentimentResult]:
        """Get brand sentiment."""
        logger.debug(f"Enriching brand sentiment for {company_name}")
        return await self.sentiment_client.analyze(company_name)

    async def _enrich_community_metrics(
        self,
        company_name: str,
        domain: str
    ) -> Optional[CommunityMetrics]:
        """Get community metrics."""
        logger.debug(f"Enriching community metrics for {company_name}")
        return await self.metrics_client.analyze(company_name, domain)

    async def enrich_entity(
        self,
        entity_id: str,
        company_name: str,
        sub_vertical: str,
        domain: Optional[str] = None
    ) -> ConsumerEnrichmentResult:
        """Enrich a consumer entity using asyncio.gather for parallel calls."""
        errors: List[str] = []
        brand_sentiment: Optional[SentimentResult] = None
        community_metrics: Optional[CommunityMetrics] = None

        tasks: List[Any] = []
        task_names: List[str] = []

        # For premium_consumer (DTC brands), get sentiment
        if sub_vertical == "premium_consumer":
            tasks.append(self._enrich_brand_sentiment(company_name))
            task_names.append("brand_sentiment")

        # For consumer_platforms (marketplaces), get community metrics
        if sub_vertical == "consumer_platforms" and domain:
            tasks.append(self._enrich_community_metrics(company_name, domain))
            task_names.append("community_metrics")

        # Run all tasks in parallel with return_exceptions=True
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for name, result in zip(task_names, results):
                if isinstance(result, Exception):
                    errors.append(f"{name} error: {result}")
                    logger.error(f"Error in {name} enrichment: {result}")
                elif name == "brand_sentiment":
                    brand_sentiment = result
                elif name == "community_metrics":
                    community_metrics = result

        # Success if we got at least some data or no errors
        success = brand_sentiment is not None or community_metrics is not None or not errors

        logger.debug(f"Enriched {entity_id}: success={success}, errors={len(errors)}")

        return ConsumerEnrichmentResult(
            entity_id=entity_id,
            sub_vertical=sub_vertical,
            brand_sentiment=brand_sentiment,
            community_metrics=community_metrics,
            success=success,
            errors=errors
        )
