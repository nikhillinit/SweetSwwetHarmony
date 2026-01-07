"""
Tests for Product Hunt collector.

Basic coverage for BaseCollector integration and dataclasses.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone


class TestProductHuntCollectorBaseIntegration:
    """Test ProductHuntCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """ProductHuntCollector should inherit from BaseCollector"""
        from collectors.product_hunt import ProductHuntCollector
        from collectors.base import BaseCollector

        assert issubclass(ProductHuntCollector, BaseCollector)

    def test_has_collect_signals_method(self):
        """ProductHuntCollector should have _collect_signals method"""
        from collectors.product_hunt import ProductHuntCollector

        collector = ProductHuntCollector()

        assert hasattr(collector, '_collect_signals')
        assert callable(collector._collect_signals)

    def test_has_rate_limiter(self):
        """ProductHuntCollector should have rate limiter"""
        from collectors.product_hunt import ProductHuntCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = ProductHuntCollector()

        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    def test_has_retry_config(self):
        """ProductHuntCollector should have retry_config"""
        from collectors.product_hunt import ProductHuntCollector
        from collectors.retry_strategy import RetryConfig

        collector = ProductHuntCollector()

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)

    def test_accepts_store_parameter(self):
        """ProductHuntCollector should accept store parameter"""
        from collectors.product_hunt import ProductHuntCollector

        mock_store = MagicMock()
        collector = ProductHuntCollector(store=mock_store)

        assert collector.store is mock_store

    def test_accepts_api_key_parameter(self):
        """ProductHuntCollector should accept api_key parameter"""
        from collectors.product_hunt import ProductHuntCollector

        collector = ProductHuntCollector(api_key="test_key")

        assert collector.api_key == "test_key"


class TestProductHuntLaunch:
    """Test ProductHuntLaunch dataclass"""

    def test_launch_dataclass_exists(self):
        """ProductHuntLaunch dataclass should exist"""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="123",
            name="Test Product",
            tagline="A test product",
            description="Full description",
            url="https://producthunt.com/posts/test",
            website="https://test.com",
            votes_count=100,
            comments_count=10,
            launched_at=datetime.now(timezone.utc),
        )

        assert launch.name == "Test Product"
        assert launch.votes_count == 100

    def test_signal_score_calculation(self):
        """calculate_signal_score should return valid score"""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="123",
            name="Test Product",
            tagline="A test product",
            description="Full description",
            url="https://producthunt.com/posts/test",
            website="https://test.com",
            votes_count=500,  # High votes
            comments_count=50,  # High comments
            launched_at=datetime.now(timezone.utc),  # Recent
        )

        score = launch.calculate_signal_score()

        # 0.5 base + 0.15 (votes) + 0.1 (comments) + 0.05 (fresh) = 0.8
        assert score == pytest.approx(0.8)

    def test_to_signal_method(self):
        """to_signal should return Signal object"""
        from collectors.product_hunt import ProductHuntLaunch
        from verification.verification_gate_v2 import Signal

        launch = ProductHuntLaunch(
            product_id="123",
            name="Test Product",
            tagline="A test product",
            description="Full description",
            url="https://producthunt.com/posts/test",
            website="https://test.com",
            votes_count=50,
            comments_count=5,
            launched_at=datetime.now(timezone.utc),
        )

        result = launch.to_signal()

        assert isinstance(result, Signal)
        assert result.signal_type == "product_hunt_launch"
