"""Tests for community metrics enrichment client."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestCommunityMetrics:
    """Tests for CommunityMetrics dataclass."""

    def test_metrics_fields(self):
        from enrichment.community_metrics import CommunityMetrics
        metrics = CommunityMetrics(
            platform_name="TestMarket",
            total_users=50000,
            active_users=10000,
            growth_rate=0.15,
            engagement_rate=0.25,
            transaction_volume=1000000
        )
        assert metrics.total_users == 50000
        assert metrics.growth_rate == 0.15


class TestCommunityMetricsClient:
    """Tests for community metrics client."""

    def test_client_initialization(self):
        from enrichment.community_metrics import CommunityMetricsClient
        client = CommunityMetricsClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_analyze_platform_returns_metrics(self):
        from enrichment.community_metrics import CommunityMetricsClient, CommunityMetrics
        client = CommunityMetricsClient()
        with patch.object(client, '_fetch_metrics', new_callable=AsyncMock) as mock:
            mock.return_value = CommunityMetrics(
                platform_name="TestPlatform",
                total_users=1000,
                active_users=500,
                growth_rate=0.1,
                engagement_rate=0.2,
                transaction_volume=None
            )
            result = await client.analyze("TestPlatform", "test.com")
            assert result is not None

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        from enrichment.community_metrics import CommunityMetricsClient
        client = CommunityMetricsClient()
        with patch.object(client, '_fetch_metrics', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")
            result = await client.analyze("TestPlatform", "test.com")
            assert result is None or result.total_users == 0
