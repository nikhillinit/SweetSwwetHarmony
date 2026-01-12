# tests/collectors/test_plugandplay.py
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from collectors.plugandplay import (
    PlugAndPlayCollector,
    PlugAndPlayCompany,
    PLUGANDPLAY_VERTICALS,
)


class TestPlugAndPlayCompany:
    """Tests for PlugAndPlayCompany dataclass."""

    def test_company_fields(self):
        company = PlugAndPlayCompany(
            name="TravelTech Inc",
            vertical="travel",
            description="AI-powered hotel booking",
            website="https://traveltech.com",
            batch="Winter 2026",
            headquarters="San Francisco",
            collected_at=datetime.utcnow()
        )
        assert company.name == "TravelTech Inc"
        assert company.vertical == "travel"


class TestPlugAndPlayVerticals:
    """Tests for vertical configuration."""

    def test_travel_vertical_exists(self):
        assert "travel" in PLUGANDPLAY_VERTICALS
        assert PLUGANDPLAY_VERTICALS["travel"] == "travel-hospitality"

    def test_health_vertical_exists(self):
        assert "health" in PLUGANDPLAY_VERTICALS


class TestPlugAndPlayCollector:
    """Tests for PlugAndPlayCollector."""

    def test_collector_initialization(self):
        collector = PlugAndPlayCollector()
        assert collector.verticals == ["travel"]

    def test_collector_custom_verticals(self):
        collector = PlugAndPlayCollector(verticals=["travel", "health"])
        assert "travel" in collector.verticals
        assert "health" in collector.verticals

    @pytest.mark.asyncio
    async def test_collect_vertical_returns_companies(self):
        collector = PlugAndPlayCollector()

        mock_companies = [
            PlugAndPlayCompany(
                name="Test Startup",
                vertical="travel",
                description="Hotel tech",
                website="https://test.com",
                batch="Winter 2026",
                headquarters="NYC",
                collected_at=datetime.utcnow()
            )
        ]

        with patch.object(collector, '_fetch_portfolio', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_companies
            results = await collector.collect_vertical("travel")

            assert len(results) == 1
            assert results[0].name == "Test Startup"

    @pytest.mark.asyncio
    async def test_collect_all_returns_dict(self):
        collector = PlugAndPlayCollector(verticals=["travel"])

        with patch.object(collector, 'collect_vertical', new_callable=AsyncMock) as mock_collect:
            mock_collect.return_value = []
            results = await collector.collect_all()

            assert isinstance(results, dict)
            assert "travel" in results

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        collector = PlugAndPlayCollector()

        with patch.object(collector, '_fetch_portfolio', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Network error")
            results = await collector.collect_vertical("travel")

            assert results == []
