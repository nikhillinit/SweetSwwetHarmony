"""Tests for consumer enrichment orchestrator."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestConsumerEnrichmentResult:
    """Tests for ConsumerEnrichmentResult."""

    def test_result_fields(self):
        from enrichment.consumer_orchestrator import ConsumerEnrichmentResult
        result = ConsumerEnrichmentResult(
            entity_id="test",
            sub_vertical="premium_consumer",
            brand_sentiment=None,
            community_metrics=None,
            success=True
        )
        assert result.entity_id == "test"
        assert result.success is True


class TestConsumerEnrichmentOrchestrator:
    """Tests for consumer orchestrator."""

    def test_orchestrator_initialization(self):
        from enrichment.consumer_orchestrator import ConsumerEnrichmentOrchestrator
        orchestrator = ConsumerEnrichmentOrchestrator()
        assert orchestrator is not None

    @pytest.mark.asyncio
    async def test_enrich_brand_returns_result(self):
        from enrichment.consumer_orchestrator import ConsumerEnrichmentOrchestrator
        orchestrator = ConsumerEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_brand_sentiment', new_callable=AsyncMock) as mock:
            mock.return_value = None

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestBrand",
                sub_vertical="premium_consumer"
            )

            assert result is not None
            assert result.entity_id == "test"

    @pytest.mark.asyncio
    async def test_enrich_platform_returns_result(self):
        from enrichment.consumer_orchestrator import ConsumerEnrichmentOrchestrator
        orchestrator = ConsumerEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_community_metrics', new_callable=AsyncMock) as mock:
            mock.return_value = None

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestMarket",
                sub_vertical="consumer_platforms",
                domain="testmarket.com"
            )

            assert result is not None
            assert result.sub_vertical == "consumer_platforms"

    @pytest.mark.asyncio
    async def test_handles_partial_failures(self):
        from enrichment.consumer_orchestrator import ConsumerEnrichmentOrchestrator
        orchestrator = ConsumerEnrichmentOrchestrator()

        with patch.object(orchestrator, '_enrich_brand_sentiment', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")

            result = await orchestrator.enrich_entity(
                entity_id="test",
                company_name="TestBrand",
                sub_vertical="premium_consumer"
            )

            # Should still return a result with errors
            assert result is not None
