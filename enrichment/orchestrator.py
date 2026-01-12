"""
Health Enrichment Orchestrator for Digital Health Intelligence.

Coordinates enrichment of health entities by querying multiple data sources
in parallel and storing results for later analysis.

Data Sources:
- ClinicalTrials.gov: Clinical trial records
- OpenFDA: FDA 510(k) clearances
- PubMed: Scientific publications

Usage:
    orchestrator = HealthEnrichmentOrchestrator("signals.db")
    await orchestrator.initialize()

    # Enrich a single entity
    result = await orchestrator.enrich_entity(
        entity_id="entity-123",
        company_name="Acme Therapeutics",
        medical_concepts=["diabetes", "insulin"]
    )

    # Enrich multiple entities in batch
    entities = [
        ("entity-1", "Company Alpha", ["diabetes"]),
        ("entity-2", "Company Beta", ["cardiology"]),
    ]
    results = await orchestrator.enrich_batch(entities)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from enrichment.clinical_trials import ClinicalTrialsClient
from enrichment.openfda import OpenFDAClient
from enrichment.pubmed import PubMedClient
from storage.health_enrichment import (
    ClinicalTrial,
    FDAClearance,
    HealthEnrichmentStore,
    Publication,
)

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    """
    Result of enriching a single entity.

    Contains counts of records found from each source and success/error status.
    """

    entity_id: str
    clinical_trials_count: int
    fda_clearances_count: int
    publications_count: int
    enriched_at: datetime
    success: bool
    error: Optional[str] = None


class HealthEnrichmentOrchestrator:
    """
    Orchestrates health entity enrichment across multiple data sources.

    Coordinates parallel API calls to ClinicalTrials.gov, OpenFDA, and PubMed,
    then stores results in the HealthEnrichmentStore.

    Features:
    - Parallel API calls for faster enrichment
    - Graceful handling of partial failures
    - Automatic storage of results
    - Batch processing support
    """

    def __init__(self, db_path: str = "signals.db"):
        """
        Initialize the health enrichment orchestrator.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for testing.
        """
        self.db_path = db_path
        self.trials_client = ClinicalTrialsClient()
        self.fda_client = OpenFDAClient()
        self.pubmed_client = PubMedClient()
        self.store = HealthEnrichmentStore(db_path)
        self._initialized = False

    async def initialize(self) -> None:
        """
        Initialize the orchestrator and its storage.

        Must be called before any enrichment operations.
        """
        await self.store.initialize()
        self._initialized = True
        logger.info("HealthEnrichmentOrchestrator initialized")

    async def enrich_entity(
        self,
        entity_id: str,
        company_name: str,
        medical_concepts: Optional[List[str]] = None,
    ) -> EnrichmentResult:
        """
        Enrich a single entity by searching all data sources.

        Searches ClinicalTrials.gov, OpenFDA, and PubMed in parallel,
        then stores the results in the database.

        Args:
            entity_id: Unique identifier for the entity.
            company_name: Company name to search for.
            medical_concepts: Optional list of medical concepts for filtering.
                TODO: Currently unused. Reserved for future filtering functionality
                to narrow search results based on specific medical domains/concepts.

        Returns:
            EnrichmentResult with counts and success status.
        """
        if not self._initialized:
            raise RuntimeError("Orchestrator not initialized. Call initialize() first.")

        enriched_at = datetime.utcnow()
        errors = []

        # Search all sources in parallel
        trials_task = self._search_trials(company_name)
        fda_task = self._search_fda(company_name)
        pubmed_task = self._search_pubmed(company_name)

        results = await asyncio.gather(
            trials_task, fda_task, pubmed_task, return_exceptions=True
        )

        # Process trials results
        trials = []
        if isinstance(results[0], Exception):
            logger.error(f"Clinical trials search failed for {company_name}: {results[0]}")
            errors.append(f"ClinicalTrials: {results[0]}")
        else:
            trials = results[0] or []

        # Process FDA results
        clearances = []
        if isinstance(results[1], Exception):
            logger.error(f"FDA search failed for {company_name}: {results[1]}")
            errors.append(f"OpenFDA: {results[1]}")
        else:
            clearances = results[1] or []

        # Process PubMed results
        publications = []
        if isinstance(results[2], Exception):
            logger.error(f"PubMed search failed for {company_name}: {results[2]}")
            errors.append(f"PubMed: {results[2]}")
        else:
            publications = results[2] or []

        # Store results
        await self._store_trials(entity_id, trials)
        await self._store_clearances(entity_id, clearances)
        await self._store_publications(entity_id, publications)

        # Determine success status
        total_records = len(trials) + len(clearances) + len(publications)
        all_failed = len(errors) == 3

        return EnrichmentResult(
            entity_id=entity_id,
            clinical_trials_count=len(trials),
            fda_clearances_count=len(clearances),
            publications_count=len(publications),
            enriched_at=enriched_at,
            success=not all_failed,
            error="; ".join(errors) if all_failed else None,
        )

    async def enrich_batch(
        self, entities: List[Tuple[str, str, List[str]]]
    ) -> List[EnrichmentResult]:
        """
        Enrich multiple entities in batch.

        Processes each entity sequentially to avoid rate limiting issues
        with the underlying APIs.

        Args:
            entities: List of tuples (entity_id, company_name, medical_concepts).

        Returns:
            List of EnrichmentResult objects, one per entity.
        """
        if not entities:
            return []

        if not self._initialized:
            raise RuntimeError("Orchestrator not initialized. Call initialize() first.")

        results = []
        for entity_id, company_name, medical_concepts in entities:
            result = await self.enrich_entity(entity_id, company_name, medical_concepts)
            results.append(result)

        logger.info(f"Batch enrichment complete: {len(results)} entities processed")
        return results

    async def _search_trials(self, company_name: str) -> List[ClinicalTrial]:
        """
        Search ClinicalTrials.gov for clinical trials.

        Args:
            company_name: Company name to search for.

        Returns:
            List of ClinicalTrial objects.
        """
        return await self.trials_client.search_by_sponsor(company_name, max_results=50)

    async def _search_fda(self, company_name: str) -> List[FDAClearance]:
        """
        Search OpenFDA for 510(k) clearances.

        Args:
            company_name: Company name to search for.

        Returns:
            List of FDAClearance objects.
        """
        return await self.fda_client.search_510k_by_applicant(company_name, max_results=50)

    async def _search_pubmed(self, company_name: str) -> List[Publication]:
        """
        Search PubMed for publications.

        Args:
            company_name: Company name to search for.

        Returns:
            List of Publication objects.
        """
        return await self.pubmed_client.search_by_affiliation(company_name, max_results=50)

    async def _store_trials(
        self, entity_id: str, trials: List[ClinicalTrial]
    ) -> None:
        """
        Store clinical trials in the database.

        Args:
            entity_id: Entity identifier.
            trials: List of ClinicalTrial objects to store.
        """
        for trial in trials:
            trial.entity_id = entity_id
            await self.store.save_clinical_trial(trial)

    async def _store_clearances(
        self, entity_id: str, clearances: List[FDAClearance]
    ) -> None:
        """
        Store FDA clearances in the database.

        Args:
            entity_id: Entity identifier.
            clearances: List of FDAClearance objects to store.
        """
        for clearance in clearances:
            clearance.entity_id = entity_id
            await self.store.save_fda_clearance(clearance)

    async def _store_publications(
        self, entity_id: str, publications: List[Publication]
    ) -> None:
        """
        Store publications in the database.

        Args:
            entity_id: Entity identifier.
            publications: List of Publication objects to store.
        """
        for publication in publications:
            publication.entity_id = entity_id
            await self.store.save_publication(publication)
