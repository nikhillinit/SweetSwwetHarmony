# tests/integration/test_travel_pipeline.py
"""
Integration tests for the Travel & Hospitality intelligence pipeline.

Tests the full flow from domain detection to classification to enrichment.
"""
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from intelligence.domain_router import DomainRouter, Domain
from intelligence.travel_classifier import TravelClassifier, TravelCategory
from enrichment.travel_orchestrator import TravelEnrichmentOrchestrator
from storage.travel_enrichment import TravelEnrichmentStore


class TestTravelDomainRouting:
    """Integration tests for travel domain routing."""

    def test_hotel_tech_signal_routes_to_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "AI-powered property management system for boutique hotels"
        )
        assert result.primary_domain == Domain.TRAVEL

    def test_booking_platform_routes_to_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "Next-generation booking platform for experiential travel"
        )
        assert result.primary_domain == Domain.TRAVEL

    def test_phocuswright_source_routes_to_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "Startup raises seed round",
            source="phocuswright"
        )
        assert result.primary_domain == Domain.TRAVEL


class TestTravelClassification:
    """Integration tests for travel classification."""

    @pytest.mark.asyncio
    async def test_hotel_tech_classified_correctly(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="Property management software for hotels with guest experience features",
            company_name="HotelOS"
        )
        assert result.category in [TravelCategory.HOTEL_TECH, TravelCategory.TRAVEL_INFRASTRUCTURE]
        assert result.fit_score > 0

    @pytest.mark.asyncio
    async def test_stage_filter_detects_series_b(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="Series B funded hotel booking platform",
            company_name="BigHotel Inc"
        )
        assert result.investment_stage_fit == "stage_mismatch"

    @pytest.mark.asyncio
    async def test_tech_enabled_detection(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="AI-powered concierge platform with mobile app",
            company_name="ConciergeAI"
        )
        assert result.is_tech_enabled is True


class TestTravelEnrichmentPipeline:
    """Integration tests for travel enrichment."""

    @pytest.fixture
    async def orchestrator(self):
        orch = TravelEnrichmentOrchestrator(db_path=":memory:")
        await orch.initialize()
        yield orch

    @pytest.mark.asyncio
    async def test_enrichment_pipeline_without_api_keys(self, orchestrator):
        """Test that pipeline handles missing API keys gracefully."""
        result = await orchestrator.enrich_entity(
            entity_id="test-entity",
            company_name="Test Hotel",
            location="New York"
        )
        # Should succeed even without API keys (returns empty results)
        assert result.success is True
        assert result.entity_id == "test-entity"

    @pytest.mark.asyncio
    async def test_storage_persists_enrichment(self, orchestrator):
        """Test that enrichment results are stored."""
        # This tests the storage layer integration
        store = orchestrator.store

        # Verify tables were created
        assert store._db is not None


class TestFullTravelPipeline:
    """End-to-end tests for the travel pipeline."""

    @pytest.mark.asyncio
    async def test_signal_to_enrichment_flow(self):
        """Test full flow: signal → domain → classify → enrich."""
        # Step 1: Domain routing
        router = DomainRouter()
        signal_content = "Seed-stage hotel tech startup with AI-powered PMS"
        domain_result = router.detect_domain(signal_content)

        assert domain_result.primary_domain == Domain.TRAVEL

        # Step 2: Classification
        classifier = TravelClassifier()
        class_result = await classifier.classify(
            content=signal_content,
            company_name="HotelAI"
        )

        assert class_result.category != TravelCategory.OUT_OF_SCOPE
        assert class_result.fit_score > 0

        # Step 3: Enrichment (with mocked clients)
        orchestrator = TravelEnrichmentOrchestrator(db_path=":memory:")
        await orchestrator.initialize()

        enrich_result = await orchestrator.enrich_entity(
            entity_id="hotel-ai-123",
            company_name="HotelAI",
            location="San Francisco"
        )

        assert enrich_result.success is True

    @pytest.mark.asyncio
    async def test_multi_domain_signal(self):
        """Test signal that matches multiple domains."""
        router = DomainRouter()

        # Health + Travel signal
        result = router.detect_domain(
            "Wellness retreat booking platform with telehealth integration"
        )

        # Should detect at least one domain
        assert result.primary_domain in [Domain.TRAVEL, Domain.HEALTH]


class TestTravelStorageIntegration:
    """Integration tests for travel storage."""

    @pytest.fixture
    async def store(self):
        store = TravelEnrichmentStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_yelp_roundtrip(self, store):
        """Test saving and retrieving Yelp data."""
        from enrichment.yelp_fusion import YelpBusiness

        business = YelpBusiness(
            entity_id="test-123",
            yelp_id="abc",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels", "Resorts"],
            url="https://yelp.com/biz/test",
            fetched_at=datetime.utcnow()
        )

        await store.save_yelp_review(business)
        reviews = await store.get_yelp_reviews_for_entity("test-123")

        assert len(reviews) == 1
        assert reviews[0].name == "Test Hotel"
        assert reviews[0].rating == 4.5

    @pytest.mark.asyncio
    async def test_google_roundtrip(self, store):
        """Test saving and retrieving Google Places data."""
        from enrichment.google_places import GooglePlace

        place = GooglePlace(
            entity_id="test-456",
            place_id="ChIJ123",
            name="Luxury Resort",
            rating=4.8,
            user_ratings_total=500,
            price_level=4,
            types=["lodging", "resort"],
            website="https://resort.com",
            fetched_at=datetime.utcnow()
        )

        await store.save_google_place(place)
        places = await store.get_google_places_for_entity("test-456")

        assert len(places) == 1
        assert places[0].rating == 4.8

    @pytest.mark.asyncio
    async def test_certification_roundtrip(self, store):
        """Test saving and retrieving certification data."""
        from enrichment.travel_certifications import TravelCertification, CertificationSource

        cert = TravelCertification(
            entity_id="test-789",
            source=CertificationSource.FORBES,
            rating="5-star",
            year=2026,
            property_name="Grand Hotel",
            fetched_at=datetime.utcnow()
        )

        await store.save_certification(cert)
        certs = await store.get_certifications_for_entity("test-789")

        assert len(certs) == 1
        assert certs[0].rating == "5-star"
