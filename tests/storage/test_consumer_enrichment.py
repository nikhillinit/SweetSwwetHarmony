"""Tests for consumer enrichment storage."""
from __future__ import annotations

import pytest


class TestConsumerEnrichmentStore:
    """Tests for consumer enrichment storage."""

    @pytest.mark.asyncio
    async def test_store_initialization(self):
        from storage.consumer_enrichment import ConsumerEnrichmentStore
        store = ConsumerEnrichmentStore(":memory:")
        await store.initialize()
        assert store._db is not None
        await store.close()

    @pytest.mark.asyncio
    async def test_save_brand_sentiment(self):
        from storage.consumer_enrichment import (
            ConsumerEnrichmentStore,
            BrandSentimentRecord
        )
        store = ConsumerEnrichmentStore(":memory:")
        await store.initialize()

        record = BrandSentimentRecord(
            entity_id="test-entity",
            brand_name="TestBrand",
            overall_sentiment=0.7,
            mention_count=500,
            positive_ratio=0.6
        )
        await store.save_brand_sentiment(record)

        sentiments = await store.get_brand_sentiment_for_entity("test-entity")
        assert len(sentiments) == 1
        assert sentiments[0].overall_sentiment == 0.7
        await store.close()

    @pytest.mark.asyncio
    async def test_save_community_metrics(self):
        from storage.consumer_enrichment import (
            ConsumerEnrichmentStore,
            CommunityMetricsRecord
        )
        store = ConsumerEnrichmentStore(":memory:")
        await store.initialize()

        record = CommunityMetricsRecord(
            entity_id="test-entity",
            platform_name="TestMarket",
            total_users=10000,
            active_users=5000,
            growth_rate=0.15
        )
        await store.save_community_metrics(record)

        metrics = await store.get_community_metrics_for_entity("test-entity")
        assert len(metrics) == 1
        assert metrics[0].total_users == 10000
        await store.close()
