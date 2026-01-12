"""
SaaS Enrichment Orchestrator for B2B SaaS Intelligence.

Coordinates enrichment of SaaS entities by querying multiple data sources
in parallel and collecting results.

Data Sources:
- G2Crowd: Product reviews and ratings
- Capterra: Product reviews and ratings
- Tech Stack: Technology detection for domains

Usage:
    orchestrator = SaaSEnrichmentOrchestrator()

    # Enrich a single entity
    result = await orchestrator.enrich_entity(
        entity_id="entity-123",
        company_name="Acme SaaS",
        domain="acme.com"
    )

    # Enrich multiple entities in batch
    entities = [
        ("entity-1", "Company Alpha", "alpha.com"),
        ("entity-2", "Company Beta", "beta.com"),
    ]
    results = await orchestrator.enrich_batch(entities)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from collectors.g2crowd import G2CrowdCollector
from collectors.capterra import CapterraCollector
from enrichment.tech_stack import TechStackClient, TechStackResult

logger = logging.getLogger(__name__)


@dataclass
class SaaSEnrichmentResult:
    """
    Result of enriching a single SaaS entity.

    Contains data from all sources and success/error status.
    """

    entity_id: str
    g2_data: List[Dict[str, Any]]
    tech_stack: Optional[TechStackResult]
    capterra_data: List[Dict[str, Any]]
    success: bool
    errors: List[str] = field(default_factory=list)


class SaaSEnrichmentOrchestrator:
    """
    Orchestrates SaaS entity enrichment across multiple data sources.

    Coordinates parallel API calls to G2Crowd, Capterra, and tech stack
    detection, then aggregates results.

    Features:
    - Parallel API calls for faster enrichment
    - Graceful handling of partial failures
    - Batch processing support
    - Debug logging for all operations
    """

    def __init__(
        self,
        g2_client: Optional[G2CrowdCollector] = None,
        capterra_client: Optional[CapterraCollector] = None,
        tech_stack_client: Optional[TechStackClient] = None
    ):
        """
        Initialize the SaaS enrichment orchestrator.

        Args:
            g2_client: Optional G2CrowdCollector instance.
            capterra_client: Optional CapterraCollector instance.
            tech_stack_client: Optional TechStackClient instance.
        """
        self.g2_client = g2_client or G2CrowdCollector()
        self.capterra_client = capterra_client or CapterraCollector()
        self.tech_stack_client = tech_stack_client or TechStackClient()
        logger.debug("SaaSEnrichmentOrchestrator initialized")

    async def _enrich_g2(self, company_name: str) -> List[Dict[str, Any]]:
        """
        Search G2 for company products.

        Args:
            company_name: Company name to search.

        Returns:
            List of product data dictionaries.
        """
        logger.debug(f"Enriching G2 data for: {company_name}")
        products = await self.g2_client.search_product(company_name)
        return [
            {
                "name": p.name,
                "rating": p.rating,
                "review_count": p.review_count,
                "category": p.category,
                "vendor": p.vendor,
            }
            for p in products
        ]

    async def _enrich_capterra(self, company_name: str) -> List[Dict[str, Any]]:
        """
        Search Capterra for company products.

        Args:
            company_name: Company name to search.

        Returns:
            List of product data dictionaries.
        """
        logger.debug(f"Enriching Capterra data for: {company_name}")
        products = await self.capterra_client.search_product(company_name)
        return [
            {
                "name": p.name,
                "overall_rating": p.overall_rating,
                "review_count": p.review_count,
                "category": p.category,
                "vendor": p.vendor,
                "ease_of_use_rating": p.ease_of_use_rating,
                "value_for_money_rating": p.value_for_money_rating,
            }
            for p in products
        ]

    async def _enrich_tech_stack(self, domain: str) -> Optional[TechStackResult]:
        """
        Get tech stack for domain.

        Args:
            domain: Domain to analyze.

        Returns:
            TechStackResult or None if analysis fails.
        """
        logger.debug(f"Enriching tech stack for: {domain}")
        return await self.tech_stack_client.analyze(domain)

    async def enrich_entity(
        self,
        entity_id: str,
        company_name: str,
        domain: Optional[str] = None
    ) -> SaaSEnrichmentResult:
        """
        Enrich a SaaS entity from all sources.

        Searches G2Crowd, Capterra, and analyzes tech stack in parallel,
        then aggregates results.

        Args:
            entity_id: Unique identifier for the entity.
            company_name: Company name to search for.
            domain: Optional domain for tech stack analysis.

        Returns:
            SaaSEnrichmentResult with collected data.
        """
        errors: List[str] = []
        g2_data: List[Dict[str, Any]] = []
        capterra_data: List[Dict[str, Any]] = []
        tech_stack: Optional[TechStackResult] = None

        logger.debug(f"Starting enrichment for entity {entity_id}: {company_name}")

        # Build list of tasks
        tasks = [
            self._enrich_g2(company_name),
            self._enrich_capterra(company_name),
        ]

        if domain:
            tasks.append(self._enrich_tech_stack(domain))

        # Run enrichments in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process G2 result
        if isinstance(results[0], Exception):
            logger.error(f"G2 enrichment failed for {company_name}: {results[0]}")
            errors.append(f"G2: {results[0]}")
        else:
            g2_data = results[0] or []

        # Process Capterra result
        if isinstance(results[1], Exception):
            logger.error(f"Capterra enrichment failed for {company_name}: {results[1]}")
            errors.append(f"Capterra: {results[1]}")
        else:
            capterra_data = results[1] or []

        # Process tech stack result
        if domain and len(results) > 2:
            if isinstance(results[2], Exception):
                logger.error(f"Tech stack enrichment failed for {domain}: {results[2]}")
                errors.append(f"TechStack: {results[2]}")
            else:
                tech_stack = results[2]

        # Determine success - true if at least one source returned data or no errors
        has_data = bool(g2_data or capterra_data or tech_stack)
        all_failed = len(errors) >= (3 if domain else 2)

        success = has_data or not all_failed

        logger.debug(
            f"Enrichment complete for {entity_id}: "
            f"g2={len(g2_data)}, capterra={len(capterra_data)}, "
            f"tech_stack={'yes' if tech_stack else 'no'}, success={success}"
        )

        return SaaSEnrichmentResult(
            entity_id=entity_id,
            g2_data=g2_data,
            tech_stack=tech_stack,
            capterra_data=capterra_data,
            success=success,
            errors=errors
        )

    async def enrich_batch(
        self,
        entities: List[Tuple[str, str, Optional[str]]]
    ) -> List[SaaSEnrichmentResult]:
        """
        Enrich multiple entities in batch.

        Processes each entity sequentially to avoid rate limiting issues
        with the underlying APIs.

        Args:
            entities: List of tuples (entity_id, company_name, domain).

        Returns:
            List of SaaSEnrichmentResult objects, one per entity.
        """
        if not entities:
            return []

        results = []
        for entity_tuple in entities:
            entity_id = entity_tuple[0]
            company_name = entity_tuple[1]
            domain = entity_tuple[2] if len(entity_tuple) > 2 else None

            result = await self.enrich_entity(entity_id, company_name, domain)
            results.append(result)

        logger.info(f"Batch enrichment complete: {len(results)} entities processed")
        return results
