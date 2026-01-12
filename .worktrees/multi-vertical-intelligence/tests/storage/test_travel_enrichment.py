# tests/storage/test_travel_enrichment.py
from __future__ import annotations

import pytest
from datetime import datetime

from storage.travel_enrichment import (
    TravelEnrichmentStore,
    YelpReview,
    GooglePlaceRecord,
    TravelCertificationRecord,
)
from enrichment.yelp_fusion import YelpBusiness
from enrichment.google_places import GooglePlace
from enrichment.travel_certifications import TravelCertification, CertificationSource


class TestTravelEnrichmentStore:
    """Tests for TravelEnrichmentStore."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store for testing."""
        store = TravelEnrichmentStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_store_initialization(self, store):
        assert store._db is not None

    @pytest.mark.asyncio
    async def test_save_yelp_review(self, store):
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="abc123",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels"],
            url="https://yelp.com/biz/test",
            fetched_at=datetime.utcnow()
        )

        record_id = await store.save_yelp_review(business)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_yelp_reviews_for_entity(self, store):
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="abc123",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels"],
            url="https://yelp.com/biz/test",
            fetched_at=datetime.utcnow()
        )
        await store.save_yelp_review(business)

        reviews = await store.get_yelp_reviews_for_entity("entity-123")
        assert len(reviews) == 1
        assert reviews[0].name == "Test Hotel"

    @pytest.mark.asyncio
    async def test_save_google_place(self, store):
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJ123",
            name="Test Hotel",
            rating=4.2,
            user_ratings_total=500,
            price_level=3,
            types=["lodging"],
            website="https://test.com",
            fetched_at=datetime.utcnow()
        )

        record_id = await store.save_google_place(place)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_google_places_for_entity(self, store):
        place = GooglePlace(
            entity_id="entity-456",
            place_id="ChIJ123",
            name="Test Resort",
            rating=4.8,
            user_ratings_total=1000,
            price_level=4,
            types=["lodging", "resort"],
            website="https://resort.com",
            fetched_at=datetime.utcnow()
        )
        await store.save_google_place(place)

        places = await store.get_google_places_for_entity("entity-456")
        assert len(places) == 1
        assert places[0].rating == 4.8

    @pytest.mark.asyncio
    async def test_save_certification(self, store):
        cert = TravelCertification(
            entity_id="entity-789",
            source=CertificationSource.FORBES,
            rating="5-star",
            year=2026,
            property_name="Luxury Hotel",
            fetched_at=datetime.utcnow()
        )

        record_id = await store.save_certification(cert)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_certifications_for_entity(self, store):
        cert = TravelCertification(
            entity_id="entity-789",
            source=CertificationSource.AAA,
            rating="5-diamond",
            year=2026,
            property_name="Diamond Hotel",
            fetched_at=datetime.utcnow()
        )
        await store.save_certification(cert)

        certs = await store.get_certifications_for_entity("entity-789")
        assert len(certs) == 1
        assert certs[0].rating == "5-diamond"
