"""Tests for G2Crowd collector for SaaS products."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestG2Product:
    """Tests for G2Product dataclass."""

    def test_product_fields(self):
        """G2Product should have all required fields."""
        from collectors.g2crowd import G2Product
        product = G2Product(
            name="Acme CRM",
            slug="acme-crm",
            category="CRM",
            rating=4.5,
            review_count=150,
            description="CRM for sales teams",
            vendor="Acme Inc",
            url="https://g2.com/products/acme-crm"
        )
        assert product.name == "Acme CRM"
        assert product.rating == 4.5
        assert product.review_count == 150
        assert product.vendor == "Acme Inc"

    def test_product_optional_fields(self):
        """G2Product optional fields should have defaults."""
        from collectors.g2crowd import G2Product
        product = G2Product(
            name="Test",
            slug="test",
            category="Test",
            rating=4.0,
            review_count=10,
            description="Test product",
            vendor="Test Inc",
            url="https://g2.com/products/test"
        )
        assert product.features is None
        assert product.pricing is None


class TestG2Categories:
    """Tests for G2 categories."""

    def test_saas_categories_exist(self):
        """G2 categories should include common SaaS categories."""
        from collectors.g2crowd import G2_CATEGORIES
        assert "crm" in G2_CATEGORIES
        assert "project_management" in G2_CATEGORIES

    def test_developer_tools_category(self):
        """G2 categories should include developer tools."""
        from collectors.g2crowd import G2_CATEGORIES
        assert "developer_tools" in G2_CATEGORIES

    def test_categories_have_slugs(self):
        """G2 categories should map to URL slugs."""
        from collectors.g2crowd import G2_CATEGORIES
        for category, slug in G2_CATEGORIES.items():
            assert isinstance(slug, str)
            assert len(slug) > 0


class TestG2CrowdCollector:
    """Tests for G2Crowd collector."""

    def test_collector_initialization(self):
        """G2CrowdCollector should initialize correctly."""
        from collectors.g2crowd import G2CrowdCollector, G2_CATEGORIES
        collector = G2CrowdCollector()
        assert collector is not None
        assert collector.categories == list(G2_CATEGORIES.keys())

    def test_collector_custom_categories(self):
        """G2CrowdCollector should accept custom categories."""
        from collectors.g2crowd import G2CrowdCollector
        collector = G2CrowdCollector(categories=["crm", "erp"])
        assert collector.categories == ["crm", "erp"]

    @pytest.mark.asyncio
    async def test_collect_category_returns_products(self):
        """collect_category should return list of products."""
        from collectors.g2crowd import G2CrowdCollector, G2Product
        collector = G2CrowdCollector()
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.return_value = [
                G2Product(
                    name="TestCRM",
                    slug="test-crm",
                    category="CRM",
                    rating=4.2,
                    review_count=100,
                    description="Test CRM product",
                    vendor="Test Inc",
                    url="https://g2.com/products/test-crm"
                )
            ]
            results = await collector.collect_category("crm")
            assert len(results) == 1
            assert results[0].name == "TestCRM"

    @pytest.mark.asyncio
    async def test_collect_all_returns_dict(self):
        """collect_all should return dict with category keys."""
        from collectors.g2crowd import G2CrowdCollector
        collector = G2CrowdCollector(categories=["crm"])
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.return_value = []
            results = await collector.collect_all()
            assert isinstance(results, dict)
            assert "crm" in results

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        """collect_all should handle errors gracefully."""
        from collectors.g2crowd import G2CrowdCollector
        collector = G2CrowdCollector(categories=["crm"])
        with patch.object(collector, '_fetch_category', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")
            results = await collector.collect_all()
            assert results["crm"] == []

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Collector should enforce rate limiting."""
        from collectors.g2crowd import G2CrowdCollector
        collector = G2CrowdCollector()
        assert hasattr(collector, '_last_request_time')
        assert collector.RATE_LIMIT_DELAY > 0
