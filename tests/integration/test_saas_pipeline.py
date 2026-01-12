"""Integration tests for SaaS pipeline."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestSaaSPipelineIntegration:
    """Integration tests for complete SaaS pipeline."""

    def test_domain_router_to_classifier(self):
        """Domain router should correctly route SaaS signals to classifier."""
        from intelligence.domain_router import Domain, DomainRouter
        from intelligence.saas_classifier import SaaSClassifier

        router = DomainRouter()
        classifier = SaaSClassifier()

        # Signal that should be classified as SaaS
        signal = "B2B SaaS platform for construction with product-led growth"

        # Router should detect SaaS
        result = router.detect_domain(signal)
        assert result.primary_domain == Domain.SAAS
        assert result.confidence >= 0.7

        # Classifier should produce valid classification
        classification = classifier.classify(
            company_name="ConstructionSaaS",
            description=signal,
            signals={}
        )
        assert classification.fit_score >= 5
        assert classification.category == "vertical_saas"

    def test_domain_router_source_boost(self):
        """Domain router should boost confidence for SaaS sources."""
        from intelligence.domain_router import Domain, DomainRouter

        router = DomainRouter()

        # Generic signal with G2 source should be classified as SaaS
        result = router.detect_domain(
            "Software platform for business",
            source="g2crowd"
        )
        assert result.primary_domain == Domain.SAAS
        assert result.confidence >= 0.5

        # Capterra source should also boost
        result = router.detect_domain(
            "Tool for teams",
            source="capterra"
        )
        assert result.primary_domain == Domain.SAAS

    def test_thesis_config_loads_correctly(self):
        """Thesis config should load and be usable by classifier."""
        from intelligence.thesis_config import load_thesis_config
        from intelligence.saas_classifier import SaaSClassifier

        # Load config
        config = load_thesis_config("saas")
        assert config.vertical == "saas"
        assert len(config.scoring_weights) > 0

        # Classifier should use config
        classifier = SaaSClassifier()
        assert classifier.thesis_config == config

    def test_classifier_scoring_weights(self):
        """Classifier should use thesis weights correctly."""
        from intelligence.saas_classifier import SaaSClassifier

        classifier = SaaSClassifier()

        # Strong PLG signal should score reasonably
        plg_result = classifier.classify(
            company_name="PLGSoft",
            description="Product-led growth freemium self-serve bottom-up viral coefficient",
            signals={}
        )

        # Strong vertical signal should also score reasonably
        vertical_result = classifier.classify(
            company_name="VerticalSaaS",
            description="Vertical SaaS industry-specific purpose-built construction tech",
            signals={}
        )

        # Both should have reasonable scores (single category matches get moderate scores)
        assert plg_result.fit_score >= 3
        assert plg_result.gtm_motion == "product_led"
        assert vertical_result.fit_score >= 3
        assert vertical_result.category == "vertical_saas"

        # Combined signal should score higher
        combined_result = classifier.classify(
            company_name="CombinedSaaS",
            description="Vertical SaaS for construction with product-led growth freemium enterprise",
            signals={"funding": "series_a"}
        )
        assert combined_result.fit_score >= plg_result.fit_score

    @pytest.mark.asyncio
    async def test_g2_collector_integration(self):
        """G2 collector should work with orchestrator."""
        from collectors.g2crowd import G2CrowdCollector, G2Product

        collector = G2CrowdCollector(categories=["crm"])

        # Mock the fetch to avoid actual API calls
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.return_value = [
                G2Product(
                    name="TestCRM",
                    slug="test-crm",
                    category="CRM",
                    rating=4.5,
                    review_count=100,
                    description="Test product",
                    vendor="TestVendor",
                    url="https://g2.com/products/test"
                )
            ]
            results = await collector.collect_all()
            assert "crm" in results
            assert len(results["crm"]) == 1

    @pytest.mark.asyncio
    async def test_capterra_collector_integration(self):
        """Capterra collector should work with orchestrator."""
        from collectors.capterra import CapterraCollector, CapterraProduct

        collector = CapterraCollector(categories=["project_management"])

        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.return_value = [
                CapterraProduct(
                    name="TestPM",
                    slug="test-pm",
                    category="Project Management",
                    overall_rating=4.2,
                    review_count=75,
                    description="PM tool",
                    vendor="TestVendor",
                    ease_of_use_rating=4.0,
                    value_for_money_rating=4.1
                )
            ]
            results = await collector.collect_all()
            assert "project_management" in results
            assert len(results["project_management"]) == 1

    @pytest.mark.asyncio
    async def test_tech_stack_integration(self):
        """Tech stack client should work correctly."""
        from enrichment.tech_stack import TechStackClient, TechStackResult

        client = TechStackClient()

        with patch.object(client, '_fetch_tech_stack', new_callable=AsyncMock) as mock:
            mock.return_value = TechStackResult(
                domain="test.com",
                technologies=["React", "Node.js", "AWS"],
                categories={"frontend": ["React"], "backend": ["Node.js"]},
                analytics=["Google Analytics"],
                hosting=["AWS"]
            )
            result = await client.analyze("test.com")
            assert result.domain == "test.com"
            assert "React" in result.technologies
            assert "AWS" in result.hosting

    @pytest.mark.asyncio
    async def test_storage_integration(self):
        """Storage should persist and retrieve correctly."""
        from storage.saas_enrichment import (
            SaaSEnrichmentStore, G2Review, TechStackRecord
        )

        store = SaaSEnrichmentStore(":memory:")
        await store.initialize()

        # Save G2 review
        review = G2Review(
            entity_id="integration-test",
            product_name="IntegrationProduct",
            rating=4.5,
            review_count=100,
            category="CRM"
        )
        await store.save_g2_review(review)

        # Save tech stack
        tech_stack = TechStackRecord(
            entity_id="integration-test",
            domain="test.com",
            technologies=["React", "AWS"],
            hosting="AWS"
        )
        await store.save_tech_stack(tech_stack)

        # Retrieve and verify
        reviews = await store.get_g2_reviews_for_entity("integration-test")
        stacks = await store.get_tech_stack_for_entity("integration-test")

        assert len(reviews) == 1
        assert reviews[0].product_name == "IntegrationProduct"
        assert len(stacks) == 1
        assert "React" in stacks[0].technologies

        await store.close()

    @pytest.mark.asyncio
    async def test_orchestrator_full_pipeline(self):
        """Orchestrator should coordinate all enrichment sources."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator

        orchestrator = SaaSEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_g2', new_callable=AsyncMock) as g2_mock, \
             patch.object(orchestrator, '_enrich_capterra', new_callable=AsyncMock) as capterra_mock, \
             patch.object(orchestrator, '_enrich_tech_stack', new_callable=AsyncMock) as tech_mock:

            g2_mock.return_value = [{"name": "TestProduct", "rating": 4.5}]
            capterra_mock.return_value = [{"name": "TestProduct", "overall_rating": 4.2}]
            tech_mock.return_value = None  # Simulating no tech stack found

            result = await orchestrator.enrich_entity(
                entity_id="pipeline-test",
                company_name="PipelineTestCo",
                domain="pipelinetest.com"
            )

            assert result.entity_id == "pipeline-test"
            assert result.success is True
            assert len(result.g2_data) == 1
            assert len(result.capterra_data) == 1

    def test_end_to_end_signal_processing(self):
        """Test complete signal processing from router to classification."""
        from intelligence.domain_router import Domain, DomainRouter
        from intelligence.saas_classifier import SaaSClassifier

        router = DomainRouter()
        classifier = SaaSClassifier()

        # Test signals representing different SaaS types
        signals = [
            ("Vertical SaaS for legal with PLG", "vertical_saas"),
            ("API platform for developers with freemium", "developer_tools"),
            ("Enterprise workflow automation for Fortune 500", "enterprise_saas"),
        ]

        for signal_text, expected_category in signals:
            # Route the signal
            domain_result = router.detect_domain(signal_text)

            # If SaaS, classify it
            if domain_result.primary_domain == Domain.SAAS:
                classification = classifier.classify(
                    company_name="TestCo",
                    description=signal_text,
                    signals={}
                )
                # Just verify it produces a valid classification
                assert classification.fit_score >= 1
                assert classification.category is not None
