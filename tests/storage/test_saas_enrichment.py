"""Tests for SaaS enrichment storage."""
from __future__ import annotations

import pytest


class TestG2Review:
    """Tests for G2Review dataclass."""

    def test_review_fields(self):
        """G2Review should have all required fields."""
        from storage.saas_enrichment import G2Review
        review = G2Review(
            entity_id="test-entity",
            product_name="TestCRM",
            rating=4.5,
            review_count=100,
            category="CRM"
        )
        assert review.entity_id == "test-entity"
        assert review.product_name == "TestCRM"
        assert review.rating == 4.5

    def test_review_optional_fields(self):
        """G2Review optional fields should have defaults."""
        from storage.saas_enrichment import G2Review
        review = G2Review(
            entity_id="test",
            product_name="Test",
            rating=4.0,
            review_count=10,
            category="Test"
        )
        assert review.vendor is None
        assert review.fetched_at is None


class TestTechStackRecord:
    """Tests for TechStackRecord dataclass."""

    def test_record_fields(self):
        """TechStackRecord should have all required fields."""
        from storage.saas_enrichment import TechStackRecord
        record = TechStackRecord(
            entity_id="test-entity",
            domain="test.com",
            technologies=["React", "Node.js"],
            hosting="AWS"
        )
        assert record.entity_id == "test-entity"
        assert record.domain == "test.com"
        assert "React" in record.technologies

    def test_record_optional_fields(self):
        """TechStackRecord optional fields should have defaults."""
        from storage.saas_enrichment import TechStackRecord
        record = TechStackRecord(
            entity_id="test",
            domain="test.com",
            technologies=[]
        )
        assert record.hosting is None
        assert record.analytics is None


class TestSaaSEnrichmentStore:
    """Tests for SaaS enrichment storage."""

    @pytest.mark.asyncio
    async def test_store_initialization(self):
        """SaaSEnrichmentStore should initialize correctly."""
        from storage.saas_enrichment import SaaSEnrichmentStore
        store = SaaSEnrichmentStore(":memory:")
        await store.initialize()
        assert store._db is not None
        await store.close()

    @pytest.mark.asyncio
    async def test_save_g2_review(self):
        """save_g2_review should persist review to database."""
        from storage.saas_enrichment import SaaSEnrichmentStore, G2Review
        store = SaaSEnrichmentStore(":memory:")
        await store.initialize()

        review = G2Review(
            entity_id="test-entity",
            product_name="TestCRM",
            rating=4.5,
            review_count=100,
            category="CRM"
        )
        await store.save_g2_review(review)

        reviews = await store.get_g2_reviews_for_entity("test-entity")
        assert len(reviews) == 1
        assert reviews[0].product_name == "TestCRM"
        await store.close()

    @pytest.mark.asyncio
    async def test_save_multiple_g2_reviews(self):
        """Should save and retrieve multiple G2 reviews."""
        from storage.saas_enrichment import SaaSEnrichmentStore, G2Review
        store = SaaSEnrichmentStore(":memory:")
        await store.initialize()

        for i in range(3):
            review = G2Review(
                entity_id="test-entity",
                product_name=f"Product{i}",
                rating=4.0 + i * 0.1,
                review_count=100 + i * 10,
                category="CRM"
            )
            await store.save_g2_review(review)

        reviews = await store.get_g2_reviews_for_entity("test-entity")
        assert len(reviews) == 3
        await store.close()

    @pytest.mark.asyncio
    async def test_save_tech_stack(self):
        """save_tech_stack should persist record to database."""
        from storage.saas_enrichment import SaaSEnrichmentStore, TechStackRecord
        store = SaaSEnrichmentStore(":memory:")
        await store.initialize()

        record = TechStackRecord(
            entity_id="test-entity",
            domain="test.com",
            technologies=["React", "Node.js"],
            hosting="AWS"
        )
        await store.save_tech_stack(record)

        stacks = await store.get_tech_stack_for_entity("test-entity")
        assert len(stacks) == 1
        assert "React" in stacks[0].technologies
        await store.close()

    @pytest.mark.asyncio
    async def test_get_empty_results(self):
        """Should return empty lists for non-existent entities."""
        from storage.saas_enrichment import SaaSEnrichmentStore
        store = SaaSEnrichmentStore(":memory:")
        await store.initialize()

        reviews = await store.get_g2_reviews_for_entity("non-existent")
        stacks = await store.get_tech_stack_for_entity("non-existent")

        assert reviews == []
        assert stacks == []
        await store.close()

    @pytest.mark.asyncio
    async def test_error_before_initialization(self):
        """Should raise error if not initialized."""
        from storage.saas_enrichment import SaaSEnrichmentStore, G2Review
        store = SaaSEnrichmentStore(":memory:")

        review = G2Review(
            entity_id="test",
            product_name="Test",
            rating=4.0,
            review_count=10,
            category="Test"
        )

        with pytest.raises(RuntimeError):
            await store.save_g2_review(review)
