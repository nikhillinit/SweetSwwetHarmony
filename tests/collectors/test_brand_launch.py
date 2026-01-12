"""Tests for brand launch collector."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestBrandLaunch:
    """Tests for BrandLaunch dataclass."""

    def test_launch_fields(self):
        from collectors.brand_launch import BrandLaunch
        launch = BrandLaunch(
            name="AcmeBev",
            tagline="Premium craft beverages",
            category="beverage",
            source="producthunt",
            url="https://producthunt.com/posts/acmebev",
            upvotes=150,
            launch_date="2026-01-11"
        )
        assert launch.name == "AcmeBev"
        assert launch.upvotes == 150


class TestLaunchSources:
    """Tests for launch sources."""

    def test_producthunt_source_exists(self):
        from collectors.brand_launch import LAUNCH_SOURCES
        assert "producthunt" in LAUNCH_SOURCES

    def test_kickstarter_source_exists(self):
        from collectors.brand_launch import LAUNCH_SOURCES
        assert "kickstarter" in LAUNCH_SOURCES


class TestBrandLaunchCollector:
    """Tests for brand launch collector."""

    def test_collector_initialization(self):
        from collectors.brand_launch import BrandLaunchCollector
        collector = BrandLaunchCollector()
        assert collector is not None

    def test_collector_custom_sources(self):
        from collectors.brand_launch import BrandLaunchCollector
        collector = BrandLaunchCollector(sources=["producthunt"])
        assert collector.sources == ["producthunt"]

    @pytest.mark.asyncio
    async def test_collect_source_returns_launches(self):
        from collectors.brand_launch import BrandLaunchCollector, BrandLaunch
        collector = BrandLaunchCollector()
        with patch.object(collector, '_fetch_source', new_callable=AsyncMock) as mock:
            mock.return_value = [
                BrandLaunch(
                    name="TestBrand",
                    tagline="Test tagline",
                    category="beverage",
                    source="producthunt",
                    url="https://test.com",
                    upvotes=50,
                    launch_date="2026-01-11"
                )
            ]
            results = await collector.collect_source("producthunt")
            assert len(results) == 1

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        from collectors.brand_launch import BrandLaunchCollector
        collector = BrandLaunchCollector(sources=["producthunt"])
        with patch.object(collector, '_fetch_source', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")
            results = await collector.collect_all()
            assert results["producthunt"] == []
