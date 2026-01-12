"""Tests for brand sentiment enrichment client."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestSentimentResult:
    """Tests for SentimentResult dataclass."""

    def test_result_fields(self):
        from enrichment.brand_sentiment import SentimentResult
        result = SentimentResult(
            brand_name="AcmeBev",
            overall_sentiment=0.75,
            mention_count=1500,
            positive_ratio=0.65,
            negative_ratio=0.10,
            neutral_ratio=0.25,
            trending_topics=["healthy", "organic"]
        )
        assert result.brand_name == "AcmeBev"
        assert result.overall_sentiment == 0.75


class TestBrandSentimentClient:
    """Tests for brand sentiment client."""

    def test_client_initialization(self):
        from enrichment.brand_sentiment import BrandSentimentClient
        client = BrandSentimentClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_analyze_brand_returns_result(self):
        from enrichment.brand_sentiment import BrandSentimentClient, SentimentResult
        client = BrandSentimentClient()
        with patch.object(client, '_fetch_sentiment', new_callable=AsyncMock) as mock:
            mock.return_value = SentimentResult(
                brand_name="TestBrand",
                overall_sentiment=0.6,
                mention_count=100,
                positive_ratio=0.5,
                negative_ratio=0.2,
                neutral_ratio=0.3,
                trending_topics=[]
            )
            result = await client.analyze("TestBrand")
            assert result is not None
            assert result.brand_name == "TestBrand"

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        from enrichment.brand_sentiment import BrandSentimentClient
        client = BrandSentimentClient()
        with patch.object(client, '_fetch_sentiment', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")
            result = await client.analyze("TestBrand")
            assert result is None or result.mention_count == 0
