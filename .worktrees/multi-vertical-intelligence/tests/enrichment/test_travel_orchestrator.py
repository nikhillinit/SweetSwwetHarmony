# tests/enrichment/test_travel_orchestrator.py
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from enrichment.travel_orchestrator import (
    TravelEnrichmentOrchestrator,
    TravelEnrichmentResult,
)
from enrichment.yelp_fusion import YelpBusiness
from enrichment.google_places import GooglePlace


class TestTravelEnrichmentResult:
    """Tests for TravelEnrichmentResult dataclass."""

    def test_result_fields(self):
        result = TravelEnrichmentResult(
            entity_id="entity-123",
            yelp_count=2,
            google_places_count=3,
            certifications_count=1,
            enriched_at=datetime.utcnow(),
            success=True
        )
        assert result.entity_id == "entity-123"
        assert result.yelp_count == 2
        assert result.success is True


class TestTravelEnrichmentOrchestrator:
    """Tests for TravelEnrichmentOrchestrator."""

    @pytest.fixture
    async def orchestrator(self):
        """Create orchestrator with mocked clients."""
        orch = TravelEnrichmentOrchestrator(db_path=":memory:")
        await orch.initialize()
        yield orch

    @pytest.mark.asyncio
    async def test_orchestrator_initialization(self, orchestrator):
        assert orchestrator._initialized is True

    @pytest.mark.asyncio
    async def test_enrich_entity_returns_result(self, orchestrator):
        # Mock all clients
        orchestrator.yelp_client.search_by_name = AsyncMock(return_value=[])
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Test Hotel",
            location="New York"
        )

        assert isinstance(result, TravelEnrichmentResult)
        assert result.entity_id == "entity-123"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enrich_entity_stores_results(self, orchestrator):
        mock_business = YelpBusiness(
            entity_id="",
            yelp_id="abc",
            name="Test",
            rating=4.5,
            review_count=100,
            price="$$",
            categories=[],
            url="",
            fetched_at=datetime.utcnow()
        )

        orchestrator.yelp_client.search_by_name = AsyncMock(return_value=[mock_business])
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        result = await orchestrator.enrich_entity(
            entity_id="entity-456",
            company_name="Test Hotel"
        )

        assert result.yelp_count == 1

    @pytest.mark.asyncio
    async def test_enrich_handles_partial_failures(self, orchestrator):
        orchestrator.yelp_client.search_by_name = AsyncMock(side_effect=Exception("API Error"))
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        result = await orchestrator.enrich_entity(
            entity_id="entity-789",
            company_name="Test Hotel"
        )

        # Should still succeed partially
        assert result.success is True
        assert result.yelp_count == 0

    @pytest.mark.asyncio
    async def test_enrich_batch(self, orchestrator):
        orchestrator.yelp_client.search_by_name = AsyncMock(return_value=[])
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        entities = [
            ("entity-1", "Hotel A", "NYC"),
            ("entity-2", "Hotel B", "LA"),
        ]

        results = await orchestrator.enrich_batch(entities)

        assert len(results) == 2
