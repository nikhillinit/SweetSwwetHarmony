"""
Travel Enrichment Orchestrator.

Coordinates enrichment of travel entities by querying multiple data sources
in parallel and storing results.

Data Sources:
- Yelp Fusion (reviews, ratings)
- Google Places (ratings, details)
- Travel Certifications (Forbes, AAA, Michelin)

Usage:
    orchestrator = TravelEnrichmentOrchestrator("signals.db")
    await orchestrator.initialize()

    result = await orchestrator.enrich_entity(
        entity_id="entity-123",
        company_name="The Ritz-Carlton",
        location="New York"
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from enrichment.yelp_fusion import YelpFusionClient, YelpBusiness
from enrichment.google_places import GooglePlacesClient, GooglePlace
from enrichment.travel_certifications import TravelCertificationsClient, TravelCertification
from storage.travel_enrichment import TravelEnrichmentStore

logger = logging.getLogger(__name__)


@dataclass
class TravelEnrichmentResult:
    """Result of enriching a travel entity."""

    entity_id: str
    yelp_count: int
    google_places_count: int
    certifications_count: int
    enriched_at: datetime
    success: bool
    error: Optional[str] = None


class TravelEnrichmentOrchestrator:
    """
    Orchestrates travel entity enrichment across multiple data sources.
    """

    def __init__(self, db_path: str = "signals.db"):
        """
        Initialize the travel enrichment orchestrator.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path

        # Initialize clients (API keys from environment)
        self.yelp_client = YelpFusionClient(
            api_key=os.getenv("YELP_API_KEY", ""),
            rate_limit=5.0
        )
        self.google_client = GooglePlacesClient(
            api_key=os.getenv("GOOGLE_PLACES_API_KEY", ""),
            rate_limit=10.0
        )
        self.cert_client = TravelCertificationsClient()

        self.store = TravelEnrichmentStore(db_path)
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the orchestrator and its storage."""
        await self.store.initialize()
        self._initialized = True
        logger.info("TravelEnrichmentOrchestrator initialized")

    async def enrich_entity(
        self,
        entity_id: str,
        company_name: str,
        location: Optional[str] = None,
    ) -> TravelEnrichmentResult:
        """
        Enrich a travel entity by searching all data sources.

        Args:
            entity_id: Unique identifier for the entity
            company_name: Company/property name to search for
            location: Optional location for better matching

        Returns:
            TravelEnrichmentResult with counts and status
        """
        if not self._initialized:
            raise RuntimeError("Orchestrator not initialized. Call initialize() first.")

        enriched_at = datetime.utcnow()
        errors = []

        # Search all sources in parallel
        yelp_task = self._search_yelp(company_name, location)
        google_task = self._search_google(company_name, location)
        cert_task = self._search_certifications(company_name)

        results = await asyncio.gather(
            yelp_task, google_task, cert_task,
            return_exceptions=True
        )

        # Process Yelp results
        yelp_businesses: List[YelpBusiness] = []
        if isinstance(results[0], Exception):
            logger.error(f"Yelp search failed for {company_name}: {results[0]}")
            errors.append(f"Yelp: {results[0]}")
        else:
            yelp_businesses = results[0] or []

        # Process Google results
        google_places: List[GooglePlace] = []
        if isinstance(results[1], Exception):
            logger.error(f"Google search failed for {company_name}: {results[1]}")
            errors.append(f"Google: {results[1]}")
        else:
            google_places = results[1] or []

        # Process certification results
        certifications: List[TravelCertification] = []
        if isinstance(results[2], Exception):
            logger.error(f"Certification search failed for {company_name}: {results[2]}")
            errors.append(f"Certifications: {results[2]}")
        else:
            certifications = results[2] or []

        # Store results
        await self._store_yelp(entity_id, yelp_businesses)
        await self._store_google(entity_id, google_places)
        await self._store_certifications(entity_id, certifications)

        # Determine success
        all_failed = len(errors) == 3

        return TravelEnrichmentResult(
            entity_id=entity_id,
            yelp_count=len(yelp_businesses),
            google_places_count=len(google_places),
            certifications_count=len(certifications),
            enriched_at=enriched_at,
            success=not all_failed,
            error="; ".join(errors) if all_failed else None,
        )

    async def enrich_batch(
        self,
        entities: List[Tuple[str, str, str]]
    ) -> List[TravelEnrichmentResult]:
        """
        Enrich multiple entities in batch.

        Args:
            entities: List of tuples (entity_id, company_name, location)

        Returns:
            List of TravelEnrichmentResult objects
        """
        if not entities:
            return []

        if not self._initialized:
            raise RuntimeError("Orchestrator not initialized.")

        results = []
        for entity_id, company_name, location in entities:
            result = await self.enrich_entity(entity_id, company_name, location)
            results.append(result)

        logger.info(f"Batch enrichment complete: {len(results)} entities processed")
        return results

    async def _search_yelp(
        self, company_name: str, location: Optional[str]
    ) -> List[YelpBusiness]:
        """Search Yelp for businesses."""
        return await self.yelp_client.search_by_name(
            company_name,
            location=location or "United States",
            max_results=5
        )

    async def _search_google(
        self, company_name: str, location: Optional[str]
    ) -> List[GooglePlace]:
        """Search Google Places."""
        return await self.google_client.search_places(
            company_name,
            location=location,
            max_results=5
        )

    async def _search_certifications(
        self, company_name: str
    ) -> List[TravelCertification]:
        """Search travel certifications."""
        return await self.cert_client.search_certifications(company_name)

    async def _store_yelp(
        self, entity_id: str, businesses: List[YelpBusiness]
    ) -> None:
        """Store Yelp businesses."""
        for business in businesses:
            business.entity_id = entity_id
            await self.store.save_yelp_review(business)

    async def _store_google(
        self, entity_id: str, places: List[GooglePlace]
    ) -> None:
        """Store Google places."""
        for place in places:
            place.entity_id = entity_id
            await self.store.save_google_place(place)

    async def _store_certifications(
        self, entity_id: str, certs: List[TravelCertification]
    ) -> None:
        """Store certifications."""
        for cert in certs:
            cert.entity_id = entity_id
            await self.store.save_certification(cert)
