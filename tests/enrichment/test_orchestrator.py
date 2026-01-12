"""
Tests for Health Enrichment Orchestrator.

Tests cover:
- Orchestrator initialization and configuration
- Entity enrichment with parallel API calls
- Result storage and counting
- Graceful handling of partial failures
- Batch processing of multiple entities
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enrichment.orchestrator import EnrichmentResult, HealthEnrichmentOrchestrator
from storage.health_enrichment import ClinicalTrial, FDAClearance, Publication


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def orchestrator() -> HealthEnrichmentOrchestrator:
    """Create a HealthEnrichmentOrchestrator instance for testing."""
    return HealthEnrichmentOrchestrator(db_path=":memory:")


@pytest.fixture
def sample_trials() -> List[ClinicalTrial]:
    """Sample clinical trial records for mocking."""
    return [
        ClinicalTrial(
            entity_id="",
            nct_id="NCT12345678",
            title="Phase 2 Study of Drug X",
            phase="Phase 2",
            status="Recruiting",
            enrollment=100,
            conditions=["Diabetes"],
            start_date=date(2023, 1, 15),
            fetched_at=datetime.utcnow(),
        ),
        ClinicalTrial(
            entity_id="",
            nct_id="NCT87654321",
            title="Phase 3 Study of Drug Y",
            phase="Phase 3",
            status="Completed",
            enrollment=500,
            conditions=["Obesity"],
            start_date=date(2022, 6, 1),
            fetched_at=datetime.utcnow(),
        ),
    ]


@pytest.fixture
def sample_clearances() -> List[FDAClearance]:
    """Sample FDA clearance records for mocking."""
    return [
        FDAClearance(
            entity_id="",
            application_number="K123456",
            device_name="Cardiac Monitor",
            device_class="II",
            clearance_type="510k",
            decision="SESE",
            decision_date=date(2023, 5, 10),
            fetched_at=datetime.utcnow(),
        ),
    ]


@pytest.fixture
def sample_publications() -> List[Publication]:
    """Sample publication records for mocking."""
    return [
        Publication(
            entity_id="",
            pmid="12345678",
            title="Novel Therapeutic Approaches",
            authors="Smith J, Doe A",
            journal="Nature Medicine",
            pub_date=date(2023, 3, 1),
            citation_count=25,
            fetched_at=datetime.utcnow(),
        ),
        Publication(
            entity_id="",
            pmid="87654321",
            title="Clinical Trial Results",
            authors="Johnson B, Williams C",
            journal="NEJM",
            pub_date=date(2023, 6, 15),
            citation_count=50,
            fetched_at=datetime.utcnow(),
        ),
        Publication(
            entity_id="",
            pmid="11223344",
            title="Device Innovation Study",
            authors="Brown D",
            journal="Lancet",
            pub_date=date(2023, 9, 1),
            citation_count=10,
            fetched_at=datetime.utcnow(),
        ),
    ]


# =============================================================================
# TestOrchestratorBasics
# =============================================================================


class TestOrchestratorBasics:
    """Tests for basic orchestrator functionality."""

    def test_orchestrator_exists(self) -> None:
        """Test that HealthEnrichmentOrchestrator class exists and can be instantiated."""
        orchestrator = HealthEnrichmentOrchestrator()
        assert orchestrator is not None
        assert isinstance(orchestrator, HealthEnrichmentOrchestrator)

    def test_default_db_path(self) -> None:
        """Test that default database path is signals.db."""
        orchestrator = HealthEnrichmentOrchestrator()
        assert orchestrator.db_path == "signals.db"

    def test_custom_db_path(self) -> None:
        """Test that custom database path can be set."""
        orchestrator = HealthEnrichmentOrchestrator(db_path="test.db")
        assert orchestrator.db_path == "test.db"

    def test_has_required_attributes(self) -> None:
        """Test that orchestrator has all required client and store attributes."""
        orchestrator = HealthEnrichmentOrchestrator()
        assert hasattr(orchestrator, "trials_client")
        assert hasattr(orchestrator, "fda_client")
        assert hasattr(orchestrator, "pubmed_client")
        assert hasattr(orchestrator, "store")
        assert hasattr(orchestrator, "_initialized")

    def test_not_initialized_by_default(self) -> None:
        """Test that orchestrator is not initialized by default."""
        orchestrator = HealthEnrichmentOrchestrator()
        assert orchestrator._initialized is False

    @pytest.mark.asyncio
    async def test_initialize_creates_store(self) -> None:
        """Test that initialize() sets up the store and marks initialized."""
        orchestrator = HealthEnrichmentOrchestrator(db_path=":memory:")

        await orchestrator.initialize()

        assert orchestrator._initialized is True
        # Clean up
        await orchestrator.store.close()


# =============================================================================
# TestEnrichEntity
# =============================================================================


class TestEnrichEntity:
    """Tests for single entity enrichment."""

    @pytest.mark.asyncio
    async def test_enrich_entity_searches_all_sources(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
        sample_trials: List[ClinicalTrial],
        sample_clearances: List[FDAClearance],
        sample_publications: List[Publication],
    ) -> None:
        """Test that enrich_entity searches all three data sources."""
        await orchestrator.initialize()

        # Mock all three clients
        orchestrator.trials_client.search_by_sponsor = AsyncMock(
            return_value=sample_trials
        )
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(
            return_value=sample_clearances
        )
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(
            return_value=sample_publications
        )

        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Acme Therapeutics",
        )

        # Verify all clients were called
        orchestrator.trials_client.search_by_sponsor.assert_called_once_with(
            "Acme Therapeutics", max_results=50
        )
        orchestrator.fda_client.search_510k_by_applicant.assert_called_once_with(
            "Acme Therapeutics", max_results=50
        )
        orchestrator.pubmed_client.search_by_affiliation.assert_called_once_with(
            "Acme Therapeutics", max_results=50
        )

        # Verify result
        assert result.success is True
        assert result.entity_id == "entity-123"

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_entity_stores_results(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
        sample_trials: List[ClinicalTrial],
        sample_clearances: List[FDAClearance],
        sample_publications: List[Publication],
    ) -> None:
        """Test that enrich_entity stores results in the database."""
        await orchestrator.initialize()

        # Mock all three clients
        orchestrator.trials_client.search_by_sponsor = AsyncMock(
            return_value=sample_trials
        )
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(
            return_value=sample_clearances
        )
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(
            return_value=sample_publications
        )

        await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Acme Therapeutics",
        )

        # Verify data was stored
        trials = await orchestrator.store.get_trials_for_entity("entity-123")
        clearances = await orchestrator.store.get_fda_clearances_for_entity("entity-123")
        publications = await orchestrator.store.get_publications_for_entity("entity-123")

        assert len(trials) == 2
        assert len(clearances) == 1
        assert len(publications) == 3

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_entity_handles_partial_failures(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
        sample_trials: List[ClinicalTrial],
        sample_publications: List[Publication],
    ) -> None:
        """Test that one client failing doesn't stop others from completing."""
        await orchestrator.initialize()

        # Mock: trials succeed, FDA fails, publications succeed
        orchestrator.trials_client.search_by_sponsor = AsyncMock(
            return_value=sample_trials
        )
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(
            side_effect=Exception("FDA API timeout")
        )
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(
            return_value=sample_publications
        )

        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Acme Therapeutics",
        )

        # Result should still be success (partial data is better than none)
        assert result.success is True
        assert result.clinical_trials_count == 2
        assert result.fda_clearances_count == 0  # Failed
        assert result.publications_count == 3

        # Verify successful data was stored
        trials = await orchestrator.store.get_trials_for_entity("entity-123")
        publications = await orchestrator.store.get_publications_for_entity("entity-123")

        assert len(trials) == 2
        assert len(publications) == 3

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_entity_returns_counts(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
        sample_trials: List[ClinicalTrial],
        sample_clearances: List[FDAClearance],
        sample_publications: List[Publication],
    ) -> None:
        """Test that enrich_entity returns correct counts in the result."""
        await orchestrator.initialize()

        # Mock all three clients
        orchestrator.trials_client.search_by_sponsor = AsyncMock(
            return_value=sample_trials
        )
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(
            return_value=sample_clearances
        )
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(
            return_value=sample_publications
        )

        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Acme Therapeutics",
        )

        assert isinstance(result, EnrichmentResult)
        assert result.entity_id == "entity-123"
        assert result.clinical_trials_count == 2
        assert result.fda_clearances_count == 1
        assert result.publications_count == 3
        assert result.enriched_at is not None
        assert result.success is True
        assert result.error is None

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_entity_all_failures_still_returns_result(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
    ) -> None:
        """Test that even if all clients fail, a result is returned."""
        await orchestrator.initialize()

        # Mock all clients to fail
        orchestrator.trials_client.search_by_sponsor = AsyncMock(
            side_effect=Exception("Clinical Trials API down")
        )
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(
            side_effect=Exception("OpenFDA API down")
        )
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(
            side_effect=Exception("PubMed API down")
        )

        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Acme Therapeutics",
        )

        # Result should indicate partial success (no data but no crash)
        assert result.entity_id == "entity-123"
        assert result.clinical_trials_count == 0
        assert result.fda_clearances_count == 0
        assert result.publications_count == 0
        # With all failures, success should be False
        assert result.success is False
        assert result.error is not None

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_entity_with_medical_concepts(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
    ) -> None:
        """Test that medical_concepts parameter is accepted."""
        await orchestrator.initialize()

        # Mock all clients to return empty lists
        orchestrator.trials_client.search_by_sponsor = AsyncMock(return_value=[])
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(return_value=[])
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(return_value=[])

        # Should not raise an error when medical_concepts is provided
        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Acme Therapeutics",
            medical_concepts=["diabetes", "insulin"],
        )

        assert result.entity_id == "entity-123"

        # Clean up
        await orchestrator.store.close()


# =============================================================================
# TestEnrichBatch
# =============================================================================


class TestEnrichBatch:
    """Tests for batch entity enrichment."""

    @pytest.mark.asyncio
    async def test_enrich_batch_processes_multiple_entities(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
        sample_trials: List[ClinicalTrial],
        sample_clearances: List[FDAClearance],
        sample_publications: List[Publication],
    ) -> None:
        """Test that enrich_batch processes all entities in the batch."""
        await orchestrator.initialize()

        # Mock all three clients
        orchestrator.trials_client.search_by_sponsor = AsyncMock(
            return_value=sample_trials
        )
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(
            return_value=sample_clearances
        )
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(
            return_value=sample_publications
        )

        entities = [
            ("entity-1", "Company Alpha", ["diabetes"]),
            ("entity-2", "Company Beta", ["cardiology"]),
            ("entity-3", "Company Gamma", []),
        ]

        results = await orchestrator.enrich_batch(entities)

        # Verify all entities were processed
        assert len(results) == 3
        assert results[0].entity_id == "entity-1"
        assert results[1].entity_id == "entity-2"
        assert results[2].entity_id == "entity-3"

        # Verify each result has data
        for result in results:
            assert result.clinical_trials_count == 2
            assert result.fda_clearances_count == 1
            assert result.publications_count == 3
            assert result.success is True

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_batch_returns_empty_list_for_empty_input(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
    ) -> None:
        """Test that enrich_batch returns empty list for empty input."""
        await orchestrator.initialize()

        results = await orchestrator.enrich_batch([])

        assert results == []

        # Clean up
        await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_enrich_batch_handles_mixed_results(
        self,
        orchestrator: HealthEnrichmentOrchestrator,
        sample_trials: List[ClinicalTrial],
    ) -> None:
        """Test that batch handles some entities succeeding and some failing."""
        await orchestrator.initialize()

        call_count = 0

        async def mock_trials_search(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # Second entity fails
                raise Exception("API error for second entity")
            return sample_trials

        orchestrator.trials_client.search_by_sponsor = mock_trials_search
        orchestrator.fda_client.search_510k_by_applicant = AsyncMock(return_value=[])
        orchestrator.pubmed_client.search_by_affiliation = AsyncMock(return_value=[])

        entities = [
            ("entity-1", "Company Alpha", []),
            ("entity-2", "Company Beta", []),
            ("entity-3", "Company Gamma", []),
        ]

        results = await orchestrator.enrich_batch(entities)

        # All entities should have results
        assert len(results) == 3

        # First and third should have trials
        assert results[0].clinical_trials_count == 2
        assert results[0].success is True

        # Second should have failed for trials but still have a result
        assert results[1].clinical_trials_count == 0

        # Third should have trials
        assert results[2].clinical_trials_count == 2
        assert results[2].success is True

        # Clean up
        await orchestrator.store.close()


# =============================================================================
# TestEnrichmentResult
# =============================================================================


class TestEnrichmentResult:
    """Tests for the EnrichmentResult dataclass."""

    def test_enrichment_result_creation(self) -> None:
        """Test that EnrichmentResult can be created with all fields."""
        now = datetime.utcnow()
        result = EnrichmentResult(
            entity_id="entity-123",
            clinical_trials_count=5,
            fda_clearances_count=2,
            publications_count=10,
            enriched_at=now,
            success=True,
            error=None,
        )

        assert result.entity_id == "entity-123"
        assert result.clinical_trials_count == 5
        assert result.fda_clearances_count == 2
        assert result.publications_count == 10
        assert result.enriched_at == now
        assert result.success is True
        assert result.error is None

    def test_enrichment_result_with_error(self) -> None:
        """Test that EnrichmentResult can represent a failed enrichment."""
        now = datetime.utcnow()
        result = EnrichmentResult(
            entity_id="entity-456",
            clinical_trials_count=0,
            fda_clearances_count=0,
            publications_count=0,
            enriched_at=now,
            success=False,
            error="All API calls failed",
        )

        assert result.entity_id == "entity-456"
        assert result.success is False
        assert result.error == "All API calls failed"
