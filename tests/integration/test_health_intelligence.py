"""Integration tests for health intelligence pipeline."""
import pytest
from unittest.mock import AsyncMock, patch

from intelligence import Domain, DomainRouter, HealthClassifier
from intelligence.medical_entity_resolver import MedicalEntityResolver
from storage.health_enrichment import (
    ClinicalTrial,
    FDAClearance,
    HealthEnrichmentStore,
    Publication,
)
from enrichment.orchestrator import HealthEnrichmentOrchestrator


class TestHealthIntelligencePipeline:
    """Test the complete health intelligence flow."""

    @pytest.mark.asyncio
    async def test_health_signal_full_flow(self):
        """Test signal flows through domain detection -> classification -> resolution."""
        # 1. Domain detection
        router = DomainRouter()
        domain_result = router.detect_domain(
            "FDA-cleared wearable for cardiac monitoring",
            source="producthunt_health"
        )
        assert domain_result.primary_domain == Domain.HEALTH
        assert domain_result.confidence >= 0.8

        # 2. Health classification
        classifier = HealthClassifier()
        classification = await classifier.classify(
            "FDA-cleared wearable for cardiac monitoring",
            company_name="CardioTech Inc"
        )
        assert classification is not None

        # 3. Entity resolution
        resolver = MedicalEntityResolver(load_model=False)
        entity = resolver.resolve(
            content="FDA-cleared wearable for cardiac monitoring",
            company_name="CardioTech Inc"
        )
        # Note: Implementation uses "health_" prefix, not "health:"
        assert entity.entity_id.startswith("health_")
        assert entity.normalized_name == "cardiotech"

    def test_non_health_signal_filtered(self):
        """Non-health signals should not be classified as health."""
        router = DomainRouter()
        result = router.detect_domain("New B2B SaaS platform for enterprises")
        assert result.primary_domain != Domain.HEALTH

    @pytest.mark.asyncio
    async def test_multiple_health_signals_flow(self):
        """Test multiple health signals are correctly identified."""
        router = DomainRouter()

        health_signals = [
            ("FDA-approved device for monitoring blood glucose", "health_tracker"),
            ("Telehealth platform for remote patient care", None),
            ("New clinical trial results for diabetes treatment", None),
            ("Digital health app for mental health support", "wellness_app"),
        ]

        for content, source in health_signals:
            result = router.detect_domain(content, source=source)
            assert result.primary_domain == Domain.HEALTH, f"Failed for: {content}"
            assert result.confidence >= 0.5

    def test_domain_detection_with_source_boost(self):
        """Source-based health detection should boost confidence."""
        router = DomainRouter()

        # Generic content, but from health source
        result_without_source = router.detect_domain("New product launch")
        result_with_health_source = router.detect_domain(
            "New product launch",
            source="producthunt_health"
        )

        # Health source should trigger health domain even with generic content
        assert result_with_health_source.primary_domain == Domain.HEALTH
        assert result_with_health_source.confidence >= 0.5

    def test_keyword_extraction_accuracy(self):
        """Keyword matching should extract relevant health terms."""
        router = DomainRouter()

        result = router.detect_domain(
            "Our FDA-cleared digital health wearable monitors cardiac activity"
        )

        assert result.primary_domain == Domain.HEALTH
        # Should match multiple keywords
        assert len(result.matched_keywords) >= 2
        assert "fda-cleared" in result.matched_keywords or "fda" in result.matched_keywords


class TestEnrichmentIntegration:
    """Test enrichment storage and orchestrator integration."""

    @pytest.mark.asyncio
    async def test_enrichment_store_integration(self):
        """Test that storage works with all three data types."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        try:
            # Test Clinical Trial storage
            trial = ClinicalTrial(
                entity_id="health:test",
                nct_id="NCT12345",
                title="Test Trial"
            )
            trial_id = await store.save_clinical_trial(trial)
            trials = await store.get_trials_for_entity("health:test")
            assert len(trials) == 1
            assert trials[0].nct_id == "NCT12345"
            assert trials[0].title == "Test Trial"

            # Test FDA Clearance storage
            clearance = FDAClearance(
                entity_id="health:test",
                application_number="K123456",
                device_name="Test Device",
                device_class="II",
                clearance_type="510k"
            )
            clearance_id = await store.save_fda_clearance(clearance)
            clearances = await store.get_fda_clearances_for_entity("health:test")
            assert len(clearances) == 1
            assert clearances[0].application_number == "K123456"
            assert clearances[0].device_name == "Test Device"

            # Test Publication storage
            pub = Publication(
                entity_id="health:test",
                pmid="12345678",
                title="Test Publication",
                journal="Test Journal"
            )
            pub_id = await store.save_publication(pub)
            pubs = await store.get_publications_for_entity("health:test")
            assert len(pubs) == 1
            assert pubs[0].pmid == "12345678"
            assert pubs[0].title == "Test Publication"

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_enrichment_store_multiple_records(self):
        """Test that storage can handle multiple records per entity."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        try:
            entity_id = "health:multi_record_test"

            # Add multiple trials
            for i in range(3):
                trial = ClinicalTrial(
                    entity_id=entity_id,
                    nct_id=f"NCT{i:08d}",
                    title=f"Trial {i}"
                )
                await store.save_clinical_trial(trial)

            # Verify all records are retrieved
            trials = await store.get_trials_for_entity(entity_id)
            assert len(trials) == 3

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_orchestrator_with_mocked_apis(self):
        """Test orchestrator coordinates correctly with mocked API responses."""
        orchestrator = HealthEnrichmentOrchestrator(":memory:")
        await orchestrator.initialize()

        try:
            # Mock the API client methods to return test data
            mock_trials = [
                ClinicalTrial(
                    entity_id="",  # Will be set by orchestrator
                    nct_id="NCT11111111",
                    title="Mock Trial 1",
                    phase="Phase 2",
                    status="Recruiting"
                ),
            ]

            mock_clearances = [
                FDAClearance(
                    entity_id="",  # Will be set by orchestrator
                    application_number="K111111",
                    device_name="Mock Device",
                    device_class="II",
                    clearance_type="510k"
                ),
            ]

            mock_publications = [
                Publication(
                    entity_id="",  # Will be set by orchestrator
                    pmid="11111111",
                    title="Mock Publication",
                    journal="Mock Journal"
                ),
            ]

            # Patch the API clients
            with patch.object(
                orchestrator.trials_client,
                'search_by_sponsor',
                new_callable=AsyncMock,
                return_value=mock_trials
            ), patch.object(
                orchestrator.fda_client,
                'search_510k_by_applicant',
                new_callable=AsyncMock,
                return_value=mock_clearances
            ), patch.object(
                orchestrator.pubmed_client,
                'search_by_affiliation',
                new_callable=AsyncMock,
                return_value=mock_publications
            ):
                # Run enrichment
                result = await orchestrator.enrich_entity(
                    entity_id="health:test_entity",
                    company_name="Test Company",
                    medical_concepts=["diabetes"]
                )

                # Verify result
                assert result.success is True
                assert result.clinical_trials_count == 1
                assert result.fda_clearances_count == 1
                assert result.publications_count == 1
                assert result.entity_id == "health:test_entity"

                # Verify data was stored
                trials = await orchestrator.store.get_trials_for_entity("health:test_entity")
                assert len(trials) == 1
                assert trials[0].nct_id == "NCT11111111"

                clearances = await orchestrator.store.get_fda_clearances_for_entity("health:test_entity")
                assert len(clearances) == 1
                assert clearances[0].application_number == "K111111"

                pubs = await orchestrator.store.get_publications_for_entity("health:test_entity")
                assert len(pubs) == 1
                assert pubs[0].pmid == "11111111"

        finally:
            await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_orchestrator_partial_failure(self):
        """Test orchestrator handles partial API failures gracefully."""
        orchestrator = HealthEnrichmentOrchestrator(":memory:")
        await orchestrator.initialize()

        try:
            # Mock partial failure - trials succeed, FDA fails, pubmed succeeds
            mock_trials = [
                ClinicalTrial(
                    entity_id="",
                    nct_id="NCT22222222",
                    title="Successful Trial"
                ),
            ]

            mock_publications = [
                Publication(
                    entity_id="",
                    pmid="22222222",
                    title="Successful Publication"
                ),
            ]

            with patch.object(
                orchestrator.trials_client,
                'search_by_sponsor',
                new_callable=AsyncMock,
                return_value=mock_trials
            ), patch.object(
                orchestrator.fda_client,
                'search_510k_by_applicant',
                new_callable=AsyncMock,
                side_effect=Exception("API Error")
            ), patch.object(
                orchestrator.pubmed_client,
                'search_by_affiliation',
                new_callable=AsyncMock,
                return_value=mock_publications
            ):
                result = await orchestrator.enrich_entity(
                    entity_id="health:partial_test",
                    company_name="Test Company"
                )

                # Should still succeed (not all failed)
                assert result.success is True
                assert result.clinical_trials_count == 1
                assert result.fda_clearances_count == 0
                assert result.publications_count == 1

        finally:
            await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_orchestrator_batch_enrichment(self):
        """Test orchestrator batch enrichment with multiple entities."""
        orchestrator = HealthEnrichmentOrchestrator(":memory:")
        await orchestrator.initialize()

        try:
            # Mock empty returns for simplicity
            with patch.object(
                orchestrator.trials_client,
                'search_by_sponsor',
                new_callable=AsyncMock,
                return_value=[]
            ), patch.object(
                orchestrator.fda_client,
                'search_510k_by_applicant',
                new_callable=AsyncMock,
                return_value=[]
            ), patch.object(
                orchestrator.pubmed_client,
                'search_by_affiliation',
                new_callable=AsyncMock,
                return_value=[]
            ):
                entities = [
                    ("entity-1", "Company A", ["diabetes"]),
                    ("entity-2", "Company B", ["cardiology"]),
                    ("entity-3", "Company C", ["oncology"]),
                ]

                results = await orchestrator.enrich_batch(entities)

                assert len(results) == 3
                assert all(r.success for r in results)
                assert results[0].entity_id == "entity-1"
                assert results[1].entity_id == "entity-2"
                assert results[2].entity_id == "entity-3"

        finally:
            await orchestrator.store.close()

    @pytest.mark.asyncio
    async def test_orchestrator_requires_initialization(self):
        """Test orchestrator raises error if not initialized."""
        orchestrator = HealthEnrichmentOrchestrator(":memory:")

        with pytest.raises(RuntimeError, match="not initialized"):
            await orchestrator.enrich_entity(
                entity_id="test",
                company_name="Test"
            )

    @pytest.mark.asyncio
    async def test_storage_requires_initialization(self):
        """Test storage raises error if not initialized."""
        store = HealthEnrichmentStore(":memory:")

        trial = ClinicalTrial(
            entity_id="test",
            nct_id="NCT00000000",
            title="Test"
        )

        with pytest.raises(RuntimeError, match="not initialized"):
            await store.save_clinical_trial(trial)


class TestEndToEndFlow:
    """Test complete end-to-end flow from signal to enrichment."""

    @pytest.mark.asyncio
    async def test_full_pipeline_integration(self):
        """Test complete flow: detection -> classification -> resolution -> enrichment."""
        # Step 1: Domain Detection
        router = DomainRouter()
        content = "FDA-cleared cardiac monitoring wearable by HeartTech Inc"

        domain_result = router.detect_domain(content, source="health_news")
        assert domain_result.primary_domain == Domain.HEALTH

        # Step 2: Health Classification
        classifier = HealthClassifier()
        classification = await classifier.classify(content, company_name="HeartTech Inc")
        assert classification is not None

        # Step 3: Entity Resolution
        resolver = MedicalEntityResolver(load_model=False)
        entity = resolver.resolve(content, company_name="HeartTech Inc")
        assert entity.normalized_name == "hearttech"
        assert entity.entity_id.startswith("health_")

        # Step 4: Enrichment (with mocked APIs)
        orchestrator = HealthEnrichmentOrchestrator(":memory:")
        await orchestrator.initialize()

        try:
            with patch.object(
                orchestrator.trials_client,
                'search_by_sponsor',
                new_callable=AsyncMock,
                return_value=[]
            ), patch.object(
                orchestrator.fda_client,
                'search_510k_by_applicant',
                new_callable=AsyncMock,
                return_value=[
                    FDAClearance(
                        entity_id="",
                        application_number="K999999",
                        device_name="HeartTech Cardiac Monitor",
                        device_class="II",
                        clearance_type="510k"
                    )
                ]
            ), patch.object(
                orchestrator.pubmed_client,
                'search_by_affiliation',
                new_callable=AsyncMock,
                return_value=[]
            ):
                result = await orchestrator.enrich_entity(
                    entity_id=entity.entity_id,
                    company_name=entity.company_name,
                    medical_concepts=entity.medical_concepts
                )

                assert result.success is True
                assert result.fda_clearances_count == 1

                # Verify enrichment data is stored with correct entity_id
                clearances = await orchestrator.store.get_fda_clearances_for_entity(entity.entity_id)
                assert len(clearances) == 1
                assert clearances[0].device_name == "HeartTech Cardiac Monitor"

        finally:
            await orchestrator.store.close()
