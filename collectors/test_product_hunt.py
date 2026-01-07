"""
Tests for Product Hunt collector.

Basic coverage for BaseCollector integration and dataclasses.
"""

import pytest
from unittest.mock import MagicMock, Mock
from datetime import datetime, timedelta, timezone


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


# =============================================================================
# COMPREHENSIVE API TESTS
# =============================================================================

class TestProductHuntAPIIntegration:
    """Test Product Hunt API integration with mocked responses."""

    @pytest.mark.asyncio
    async def test_fetch_launches_success(self):
        """Should successfully fetch launches from API."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch

        collector = ProductHuntCollector(api_key="test_api_key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={
            "data": {
                "posts": {
                    "edges": [
                        {
                            "node": {
                                "id": "12345",
                                "name": "Awesome Product",
                                "tagline": "The best product",
                                "description": "A detailed description",
                                "url": "https://www.producthunt.com/posts/awesome",
                                "website": "https://awesome.com",
                                "votesCount": 150,
                                "commentsCount": 25,
                                "createdAt": "2024-01-15T10:00:00Z",
                                "topics": {"edges": [{"node": {"name": "AI"}}]},
                                "makers": [],
                                "thumbnail": {"url": "https://ph-files.example.com/123.png"},
                            }
                        }
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None}
                }
            }
        })

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                launches = await collector._fetch_launches()

                assert len(launches) == 1
                assert launches[0].name == "Awesome Product"
                assert launches[0].votes_count == 150
                assert launches[0].website == "https://awesome.com"

    @pytest.mark.asyncio
    async def test_fetch_launches_with_pagination(self):
        """Should handle paginated responses."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch

        collector = ProductHuntCollector(api_key="test_api_key", lookback_days=7)

        # First page
        mock_response_page1 = Mock()
        mock_response_page1.status_code = 200
        mock_response_page1.json = Mock(return_value={
            "data": {
                "posts": {
                    "edges": [
                        {
                            "node": {
                                "id": "1",
                                "name": "Product 1",
                                "tagline": "First",
                                "description": "Desc 1",
                                "url": "https://www.producthunt.com/posts/prod1",
                                "website": "https://prod1.com",
                                "votesCount": 100,
                                "commentsCount": 10,
                                "createdAt": "2024-01-15T10:00:00Z",
                                "topics": {"edges": []},
                                "makers": [],
                                "thumbnail": {"url": ""},
                            }
                        }
                    ],
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor123"}
                }
            }
        })

        # Second page
        mock_response_page2 = Mock()
        mock_response_page2.status_code = 200
        mock_response_page2.json = Mock(return_value={
            "data": {
                "posts": {
                    "edges": [
                        {
                            "node": {
                                "id": "2",
                                "name": "Product 2",
                                "tagline": "Second",
                                "description": "Desc 2",
                                "url": "https://www.producthunt.com/posts/prod2",
                                "website": "https://prod2.com",
                                "votesCount": 80,
                                "commentsCount": 8,
                                "createdAt": "2024-01-14T10:00:00Z",
                                "topics": {"edges": []},
                                "makers": [],
                                "thumbnail": {"url": ""},
                            }
                        }
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None}
                }
            }
        })

        async with collector:
            with patch.object(collector.client, "post", side_effect=[mock_response_page1, mock_response_page2]):
                launches = await collector._fetch_launches()

                assert len(launches) == 2
                assert launches[0].name == "Product 1"
                assert launches[1].name == "Product 2"

    @pytest.mark.asyncio
    async def test_fetch_launches_api_error(self):
        """Should handle API errors gracefully."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch

        collector = ProductHuntCollector(api_key="test_api_key")

        mock_response = Mock()
        mock_response.status_code = 500

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                launches = await collector._fetch_launches()

                # Should return empty list on error
                assert launches == []

    @pytest.mark.asyncio
    async def test_fetch_launches_graphql_error(self):
        """Should handle GraphQL errors."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch

        collector = ProductHuntCollector(api_key="test_api_key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={
            "errors": [
                {"message": "Invalid API key"}
            ]
        })

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                launches = await collector._fetch_launches()

                assert launches == []

    @pytest.mark.asyncio
    async def test_collect_signals_without_api_key(self):
        """Should skip collection without API key."""
        from collectors.product_hunt import ProductHuntCollector

        collector = ProductHuntCollector(api_key=None)

        async with collector:
            signals = await collector._collect_signals()

            assert signals == []


class TestConfidenceScoring:
    """Test confidence score calculation."""

    def test_base_score_for_launch(self):
        """Should have base score of 0.5 for any launch."""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="123",
            name="Basic Product",
            tagline="Simple",
            description="Basic",
            url="https://www.producthunt.com/posts/basic",
            website="https://basic.com",
            votes_count=15,  # Minimal votes
            comments_count=2,
            launched_at=datetime.now(timezone.utc) - timedelta(days=30),
        )

        score = launch.calculate_signal_score()

        assert score >= 0.5
        assert score < 0.6  # No boosts

    def test_high_score_for_popular_launch(self):
        """Should have high score for popular launches."""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="456",
            name="Viral Product",
            tagline="Everyone loves it",
            description="Amazing product",
            url="https://www.producthunt.com/posts/viral",
            website="https://viral.com",
            votes_count=600,  # High votes  (+0.15)
            comments_count=60,  # High comments (+0.1)
            launched_at=datetime.now(timezone.utc) - timedelta(days=3),  # Recent (+0.05)
        )

        score = launch.calculate_signal_score()

        # 0.5 + 0.15 + 0.1 + 0.05 = 0.8
        assert score == pytest.approx(0.8)

    def test_mid_score_for_moderate_launch(self):
        """Should have mid score for moderate engagement."""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="789",
            name="Decent Product",
            tagline="Pretty good",
            description="Decent engagement",
            url="https://www.producthunt.com/posts/decent",
            website="https://decent.com",
            votes_count=250,  # Mid votes (+0.1)
            comments_count=25,  # Mid comments (+0.05)
            launched_at=datetime.now(timezone.utc) - timedelta(days=10),  # Not recent
        )

        score = launch.calculate_signal_score()

        # 0.5 + 0.1 + 0.05 = 0.65
        assert score == pytest.approx(0.65)


class TestCanonicalKeyGeneration:
    """Test canonical key generation."""

    def test_canonical_key_with_domain(self):
        """Should use domain for canonical key."""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="123",
            name="Product",
            tagline="Tag",
            description="Desc",
            url="https://www.producthunt.com/posts/product",
            website="https://mycompany.com",
            votes_count=50,
            comments_count=5,
            launched_at=datetime.now(timezone.utc),
        )

        signal = launch.to_signal()

        assert "canonical_key" in signal.raw_data
        assert "mycompany.com" in signal.raw_data["canonical_key"]

    def test_canonical_key_without_domain(self):
        """Should fall back to product_hunt ID without domain."""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="123",
            name="Stealth Product",
            tagline="Secret",
            description="Stealth mode",
            url="https://www.producthunt.com/posts/stealth",
            website="",  # No website
            votes_count=30,
            comments_count=3,
            launched_at=datetime.now(timezone.utc),
        )

        signal = launch.to_signal()

        assert "canonical_key" in signal.raw_data
        assert "product_hunt:123" in signal.raw_data["canonical_key"]


class TestErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_handles_malformed_api_response(self):
        """Should handle malformed API responses."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch

        collector = ProductHuntCollector(api_key="test_key")

        # Missing required fields
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={
            "data": {
                "posts": {
                    "edges": [
                        {"node": {"id": "123"}}  # Missing most fields
                    ],
                    "pageInfo": {"hasNextPage": False}
                }
            }
        })

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                # Should handle gracefully
                launches = await collector._fetch_launches()
                # May return empty or partial data
                assert isinstance(launches, list)

    @pytest.mark.asyncio
    async def test_handles_network_error(self):
        """Should handle network errors."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch
        import httpx

        collector = ProductHuntCollector(api_key="test_key")

        async with collector:
            with patch.object(collector.client, "post", side_effect=httpx.NetworkError("Connection failed")):
                launches = await collector._fetch_launches()

                assert launches == []

    @pytest.mark.asyncio
    async def test_filters_low_vote_launches(self):
        """Should filter out launches below minimum votes."""
        from collectors.product_hunt import ProductHuntCollector
        from unittest.mock import AsyncMock, patch

        collector = ProductHuntCollector(api_key="test_key", min_votes=20)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={
            "data": {
                "posts": {
                    "edges": [
                        {
                            "node": {
                                "id": "1",
                                "name": "Low Votes",
                                "tagline": "Not popular",
                                "description": "Few votes",
                                "url": "https://www.producthunt.com/posts/low",
                                "website": "https://low.com",
                                "votesCount": 5,  # Below threshold
                                "commentsCount": 1,
                                "createdAt": "2024-01-15T10:00:00Z",
                                "topics": {"edges": []},
                                "makers": [],
                                "thumbnail": {"url": ""},
                            }
                        }
                    ],
                    "pageInfo": {"hasNextPage": False}
                }
            }
        })

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                launches = await collector._fetch_launches()

                # Should be filtered out
                assert len(launches) == 0


class TestSignalTypeAssignment:
    """Test signal type assignment."""

    def test_signal_type_is_product_hunt_launch(self):
        """Should assign correct signal type."""
        from collectors.product_hunt import ProductHuntLaunch

        launch = ProductHuntLaunch(
            product_id="123",
            name="Test",
            tagline="Test",
            description="Test",
            url="https://www.producthunt.com/posts/test",
            website="https://test.com",
            votes_count=50,
            comments_count=5,
            launched_at=datetime.now(timezone.utc),
        )

        signal = launch.to_signal()

        assert signal.signal_type == "product_hunt_launch"
        assert signal.source_api == "product_hunt"


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_full_collection_flow():
    """Test full collection flow from API to signals."""
    from collectors.product_hunt import ProductHuntCollector
    from unittest.mock import AsyncMock, patch

    collector = ProductHuntCollector(api_key="test_key", min_votes=10)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={
        "data": {
            "posts": {
                "edges": [
                    {
                        "node": {
                            "id": "test123",
                            "name": "Test Product",
                            "tagline": "A test product",
                            "description": "Full test description",
                            "url": "https://www.producthunt.com/posts/test",
                            "website": "https://testproduct.com",
                            "votesCount": 100,
                            "commentsCount": 15,
                            "createdAt": "2024-01-15T10:00:00Z",
                            "topics": {
                                "edges": [
                                    {"node": {"name": "SaaS"}},
                                    {"node": {"name": "Productivity"}},
                                ]
                            },
                            "makers": [
                                {
                                    "id": "maker1",
                                    "name": "John Doe",
                                    "headline": "Founder @ TestProduct",
                                }
                            ],
                            "thumbnail": {"url": "https://ph-files.example.com/test.png"},
                        }
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None}
            }
        }
    })

    async with collector:
        with patch.object(collector.client, "post", return_value=mock_response):
            signals = await collector._collect_signals()

            assert len(signals) == 1
            assert signals[0].signal_type == "product_hunt_launch"
            assert signals[0].raw_data["company_name"] == "Test Product"
            assert signals[0].raw_data["votes_count"] == 100
            assert signals[0].confidence > 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
