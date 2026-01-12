"""Tests for Capterra collector for SaaS products."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestCapterraProduct:
    """Tests for CapterraProduct dataclass."""

    def test_product_fields(self):
        """CapterraProduct should have all required fields."""
        from collectors.capterra import CapterraProduct
        product = CapterraProduct(
            name="Acme PM",
            slug="acme-pm",
            category="Project Management",
            overall_rating=4.3,
            review_count=200,
            description="PM tool for teams",
            vendor="Acme",
            ease_of_use_rating=4.5,
            value_for_money_rating=4.1
        )
        assert product.name == "Acme PM"
        assert product.overall_rating == 4.3
        assert product.ease_of_use_rating == 4.5
        assert product.value_for_money_rating == 4.1

    def test_product_optional_fields(self):
        """CapterraProduct optional fields should have defaults."""
        from collectors.capterra import CapterraProduct
        product = CapterraProduct(
            name="Test",
            slug="test",
            category="Test",
            overall_rating=4.0,
            review_count=10,
            description="Test product",
            vendor="Test Inc",
            ease_of_use_rating=4.0,
            value_for_money_rating=4.0
        )
        assert product.features is None


class TestCapterraCategories:
    """Tests for Capterra categories."""

    def test_categories_exist(self):
        """Capterra categories should include common categories."""
        from collectors.capterra import CAPTERRA_CATEGORIES
        assert "project_management" in CAPTERRA_CATEGORIES
        assert "accounting" in CAPTERRA_CATEGORIES

    def test_categories_have_slugs(self):
        """Capterra categories should map to URL slugs."""
        from collectors.capterra import CAPTERRA_CATEGORIES
        for category, slug in CAPTERRA_CATEGORIES.items():
            assert isinstance(slug, str)
            assert len(slug) > 0


class TestCapterraCollector:
    """Tests for Capterra collector."""

    def test_collector_initialization(self):
        """CapterraCollector should initialize correctly."""
        from collectors.capterra import CapterraCollector
        collector = CapterraCollector()
        assert collector is not None

    def test_collector_custom_categories(self):
        """CapterraCollector should accept custom categories."""
        from collectors.capterra import CapterraCollector
        collector = CapterraCollector(categories=["project_management", "crm"])
        assert collector.categories == ["project_management", "crm"]

    @pytest.mark.asyncio
    async def test_collect_category_returns_products(self):
        """collect_category should return list of products."""
        from collectors.capterra import CapterraCollector, CapterraProduct
        collector = CapterraCollector()
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.return_value = [
                CapterraProduct(
                    name="TestPM",
                    slug="test-pm",
                    category="Project Management",
                    overall_rating=4.0,
                    review_count=50,
                    description="Test product",
                    vendor="Test",
                    ease_of_use_rating=4.0,
                    value_for_money_rating=4.0
                )
            ]
            results = await collector.collect_category("project_management")
            assert len(results) == 1

    @pytest.mark.asyncio
    async def test_collect_all_returns_dict(self):
        """collect_all should return dict with category keys."""
        from collectors.capterra import CapterraCollector
        collector = CapterraCollector(categories=["project_management"])
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.return_value = []
            results = await collector.collect_all()
            assert isinstance(results, dict)
            assert "project_management" in results

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        """collect_all should handle errors gracefully."""
        from collectors.capterra import CapterraCollector
        collector = CapterraCollector(categories=["project_management"])
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")
            results = await collector.collect_all()
            assert results["project_management"] == []

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Collector should enforce rate limiting."""
        from collectors.capterra import CapterraCollector
        collector = CapterraCollector()
        assert hasattr(collector, '_last_request_time')
        assert collector.RATE_LIMIT_DELAY > 0
