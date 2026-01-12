"""Tests for SaaS enrichment orchestrator."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSaaSEnrichmentResult:
    """Tests for SaaSEnrichmentResult."""

    def test_result_fields(self):
        """SaaSEnrichmentResult should have all required fields."""
        from enrichment.saas_orchestrator import SaaSEnrichmentResult
        result = SaaSEnrichmentResult(
            entity_id="test",
            g2_data=[],
            tech_stack=None,
            capterra_data=[],
            success=True
        )
        assert result.entity_id == "test"
        assert result.success is True
        assert result.errors == []

    def test_result_with_errors(self):
        """SaaSEnrichmentResult should support error tracking."""
        from enrichment.saas_orchestrator import SaaSEnrichmentResult
        result = SaaSEnrichmentResult(
            entity_id="test",
            g2_data=[],
            tech_stack=None,
            capterra_data=[],
            success=False,
            errors=["API error"]
        )
        assert result.success is False
        assert "API error" in result.errors


class TestSaaSEnrichmentOrchestrator:
    """Tests for SaaS orchestrator."""

    def test_orchestrator_initialization(self):
        """SaaSEnrichmentOrchestrator should initialize correctly."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator
        orchestrator = SaaSEnrichmentOrchestrator()
        assert orchestrator is not None

    def test_orchestrator_custom_clients(self):
        """Orchestrator should accept custom clients."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator
        from collectors.g2crowd import G2CrowdCollector
        from collectors.capterra import CapterraCollector
        from enrichment.tech_stack import TechStackClient

        g2 = G2CrowdCollector()
        capterra = CapterraCollector()
        tech = TechStackClient()

        orchestrator = SaaSEnrichmentOrchestrator(
            g2_client=g2,
            capterra_client=capterra,
            tech_stack_client=tech
        )
        assert orchestrator.g2_client is g2
        assert orchestrator.capterra_client is capterra
        assert orchestrator.tech_stack_client is tech

    @pytest.mark.asyncio
    async def test_enrich_entity_returns_result(self):
        """enrich_entity should return SaaSEnrichmentResult."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator, SaaSEnrichmentResult
        orchestrator = SaaSEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_g2', new_callable=AsyncMock) as g2_mock, \
             patch.object(orchestrator, '_enrich_capterra', new_callable=AsyncMock) as capterra_mock, \
             patch.object(orchestrator, '_enrich_tech_stack', new_callable=AsyncMock) as tech_mock:
            g2_mock.return_value = []
            capterra_mock.return_value = []
            tech_mock.return_value = None

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestCo",
                domain="test.com"
            )

            assert result is not None
            assert isinstance(result, SaaSEnrichmentResult)
            assert result.entity_id == "test"

    @pytest.mark.asyncio
    async def test_handles_partial_failures(self):
        """enrich_entity should handle partial failures gracefully."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator
        orchestrator = SaaSEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_g2', new_callable=AsyncMock) as g2_mock, \
             patch.object(orchestrator, '_enrich_capterra', new_callable=AsyncMock) as capterra_mock, \
             patch.object(orchestrator, '_enrich_tech_stack', new_callable=AsyncMock) as tech_mock:
            g2_mock.return_value = [{"name": "TestProduct"}]
            capterra_mock.return_value = []
            tech_mock.side_effect = Exception("API error")

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestCo",
                domain="test.com"
            )

            # Should still succeed with partial data
            assert result.success is True
            assert len(result.g2_data) == 1

    @pytest.mark.asyncio
    async def test_all_sources_fail(self):
        """enrich_entity should handle all sources failing."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator
        orchestrator = SaaSEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_g2', new_callable=AsyncMock) as g2_mock, \
             patch.object(orchestrator, '_enrich_capterra', new_callable=AsyncMock) as capterra_mock, \
             patch.object(orchestrator, '_enrich_tech_stack', new_callable=AsyncMock) as tech_mock:
            g2_mock.side_effect = Exception("G2 error")
            capterra_mock.side_effect = Exception("Capterra error")
            tech_mock.side_effect = Exception("Tech stack error")

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestCo",
                domain="test.com"
            )

            # Should not raise, but mark as failed
            assert result.success is False
            assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_enrich_without_domain(self):
        """enrich_entity should work without domain."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator
        orchestrator = SaaSEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_g2', new_callable=AsyncMock) as g2_mock, \
             patch.object(orchestrator, '_enrich_capterra', new_callable=AsyncMock) as capterra_mock:
            g2_mock.return_value = []
            capterra_mock.return_value = []

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestCo"
            )

            # Should not call tech stack without domain
            assert result is not None
            assert result.tech_stack is None

    @pytest.mark.asyncio
    async def test_enrich_batch(self):
        """enrich_batch should process multiple entities."""
        from enrichment.saas_orchestrator import SaaSEnrichmentOrchestrator
        orchestrator = SaaSEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_g2', new_callable=AsyncMock) as g2_mock, \
             patch.object(orchestrator, '_enrich_capterra', new_callable=AsyncMock) as capterra_mock, \
             patch.object(orchestrator, '_enrich_tech_stack', new_callable=AsyncMock) as tech_mock:
            g2_mock.return_value = []
            capterra_mock.return_value = []
            tech_mock.return_value = None

            entities = [
                ("entity1", "Company1", "company1.com"),
                ("entity2", "Company2", "company2.com"),
            ]

            results = await orchestrator.enrich_batch(entities)
            assert len(results) == 2
            assert results[0].entity_id == "entity1"
            assert results[1].entity_id == "entity2"
