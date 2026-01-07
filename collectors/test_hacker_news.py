"""
Tests for Hacker News collector.

TDD: Write tests first, then implement.
Tests cover BaseCollector integration, dataclasses, and API interactions.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestHackerNewsCollectorBaseIntegration:
    """Test HackerNewsCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """HackerNewsCollector should inherit from BaseCollector"""
        from collectors.hacker_news import HackerNewsCollector
        from collectors.base import BaseCollector

        assert issubclass(HackerNewsCollector, BaseCollector)

    def test_has_collect_signals_method(self):
        """HackerNewsCollector should have _collect_signals method"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        assert hasattr(collector, '_collect_signals')
        assert callable(collector._collect_signals)

    def test_has_rate_limiter(self):
        """HackerNewsCollector should have rate limiter"""
        from collectors.hacker_news import HackerNewsCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = HackerNewsCollector()

        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    def test_has_retry_config(self):
        """HackerNewsCollector should have retry_config"""
        from collectors.hacker_news import HackerNewsCollector
        from collectors.retry_strategy import RetryConfig

        collector = HackerNewsCollector()

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)

    def test_accepts_store_parameter(self):
        """HackerNewsCollector should accept store parameter"""
        from collectors.hacker_news import HackerNewsCollector

        mock_store = MagicMock()
        collector = HackerNewsCollector(store=mock_store)

        assert collector.store is mock_store

    def test_collector_name_is_hacker_news(self):
        """HackerNewsCollector should have correct collector name"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        assert collector.collector_name == "hacker_news"

    def test_api_name_is_hacker_news(self):
        """HackerNewsCollector should use hacker_news API name for rate limiting"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        assert collector.api_name == "hacker_news"


class TestHackerNewsPost:
    """Test HackerNewsPost dataclass"""

    def test_post_dataclass_exists(self):
        """HackerNewsPost dataclass should exist"""
        from collectors.hacker_news import HackerNewsPost

        post = HackerNewsPost(
            object_id="12345",
            title="Show HN: My New Startup",
            url="https://mystartup.com",
            author="founder123",
            points=150,
            num_comments=42,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story", "show_hn"],
        )

        assert post.title == "Show HN: My New Startup"
        assert post.points == 150

    def test_signal_score_base(self):
        """Base score should be 0.5 for any HN post"""
        from collectors.hacker_news import HackerNewsPost

        post = HackerNewsPost(
            object_id="12345",
            title="Test Post",
            url="https://example.com",
            author="user",
            points=0,
            num_comments=0,
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            story_text="",
            tags=["story"],
        )

        score = post.calculate_signal_score()
        assert score >= 0.5

    def test_signal_score_high_points(self):
        """High points should boost score"""
        from collectors.hacker_news import HackerNewsPost

        post = HackerNewsPost(
            object_id="12345",
            title="Popular Post",
            url="https://example.com",
            author="user",
            points=500,
            num_comments=100,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story"],
        )

        score = post.calculate_signal_score()
        # High points + comments + fresh = should be near max
        assert score >= 0.75

    def test_signal_score_show_hn_bonus(self):
        """Show HN posts should get a bonus"""
        from collectors.hacker_news import HackerNewsPost

        regular_post = HackerNewsPost(
            object_id="12345",
            title="Regular Post",
            url="https://example.com",
            author="user",
            points=100,
            num_comments=20,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story"],
        )

        show_hn_post = HackerNewsPost(
            object_id="12346",
            title="Show HN: My Startup",
            url="https://example.com",
            author="user",
            points=100,
            num_comments=20,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story", "show_hn"],
        )

        assert show_hn_post.calculate_signal_score() > regular_post.calculate_signal_score()

    def test_is_show_hn_property(self):
        """is_show_hn should detect Show HN posts"""
        from collectors.hacker_news import HackerNewsPost

        show_hn_post = HackerNewsPost(
            object_id="12345",
            title="Show HN: My Startup",
            url="https://example.com",
            author="user",
            points=100,
            num_comments=20,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story", "show_hn"],
        )

        regular_post = HackerNewsPost(
            object_id="12346",
            title="Some Article",
            url="https://example.com",
            author="user",
            points=100,
            num_comments=20,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story"],
        )

        assert show_hn_post.is_show_hn is True
        assert regular_post.is_show_hn is False

    def test_to_signal_method(self):
        """to_signal should return Signal object"""
        from collectors.hacker_news import HackerNewsPost
        from verification.verification_gate_v2 import Signal

        post = HackerNewsPost(
            object_id="12345",
            title="Show HN: My Startup",
            url="https://mystartup.com",
            author="founder123",
            points=150,
            num_comments=42,
            created_at=datetime.now(timezone.utc),
            story_text="We built this for developers",
            tags=["story", "show_hn"],
        )

        result = post.to_signal()

        assert isinstance(result, Signal)
        assert result.signal_type == "hacker_news_mention"
        assert result.source_api == "hacker_news"

    def test_to_signal_canonical_key_uses_domain(self):
        """Canonical key should use domain when available"""
        from collectors.hacker_news import HackerNewsPost

        post = HackerNewsPost(
            object_id="12345",
            title="Test Post",
            url="https://mystartup.com/product",
            author="user",
            points=50,
            num_comments=10,
            created_at=datetime.now(timezone.utc),
            story_text="",
            tags=["story"],
        )

        signal = post.to_signal()

        assert signal.raw_data["canonical_key"] == "domain:mystartup.com"

    def test_to_signal_canonical_key_fallback(self):
        """Canonical key should fallback to HN ID when no URL"""
        from collectors.hacker_news import HackerNewsPost

        post = HackerNewsPost(
            object_id="12345",
            title="Ask HN: What do you think?",
            url="",  # Ask HN posts have no URL
            author="user",
            points=50,
            num_comments=10,
            created_at=datetime.now(timezone.utc),
            story_text="What's your opinion?",
            tags=["story", "ask_hn"],
        )

        signal = post.to_signal()

        assert signal.raw_data["canonical_key"] == "hacker_news:12345"


class TestHackerNewsCollectorParameters:
    """Test HackerNewsCollector initialization parameters"""

    def test_default_lookback_days(self):
        """Default lookback should be 7 days"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        assert collector.lookback_days == 7

    def test_custom_lookback_days(self):
        """Should accept custom lookback_days"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector(lookback_days=30)

        assert collector.lookback_days == 30

    def test_default_min_points(self):
        """Default min_points should be 10"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        assert collector.min_points == 10

    def test_custom_min_points(self):
        """Should accept custom min_points"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector(min_points=50)

        assert collector.min_points == 50

    def test_default_search_domains(self):
        """search_domains should default to None (Show HN mode)"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        assert collector.search_domains is None

    def test_custom_search_domains(self):
        """Should accept list of domains to search"""
        from collectors.hacker_news import HackerNewsCollector

        domains = ["mystartup.com", "anotherstartup.io"]
        collector = HackerNewsCollector(search_domains=domains)

        assert collector.search_domains == domains


class TestHackerNewsAPIConstants:
    """Test API constants"""

    def test_algolia_api_url(self):
        """Should use Algolia HN API"""
        from collectors.hacker_news import HN_ALGOLIA_API

        assert HN_ALGOLIA_API == "https://hn.algolia.com/api/v1/search"


@pytest.mark.asyncio
class TestHackerNewsCollectorAsync:
    """Test async collector operations"""

    async def test_context_manager(self):
        """Should work as async context manager"""
        from collectors.hacker_news import HackerNewsCollector

        async with HackerNewsCollector() as collector:
            assert collector.client is not None

    async def test_run_returns_collector_result(self):
        """run() should return CollectorResult"""
        from collectors.hacker_news import HackerNewsCollector
        from discovery_engine.mcp_server import CollectorResult

        collector = HackerNewsCollector()

        with patch.object(collector, '_collect_signals', new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = await collector.run(dry_run=True)

        assert isinstance(result, CollectorResult)
        assert result.collector == "hacker_news"

    async def test_fetch_show_hn_posts(self):
        """Should fetch Show HN posts when no domains specified"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector()

        mock_response = {
            "hits": [
                {
                    "objectID": "12345",
                    "title": "Show HN: My Startup",
                    "url": "https://mystartup.com",
                    "author": "founder",
                    "points": 150,
                    "num_comments": 42,
                    "created_at": "2024-01-15T10:00:00.000Z",
                    "created_at_i": 1705312800,
                    "story_text": "Description",
                    "_tags": ["story", "show_hn", "author_founder"],
                }
            ],
            "nbHits": 1,
            "page": 0,
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.status_code = 200
            mock_response_obj.json.return_value = mock_response
            mock_client.get = AsyncMock(return_value=mock_response_obj)
            mock_client.aclose = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            async with HackerNewsCollector() as collector:
                collector.client = mock_client
                posts = await collector._fetch_posts()

        assert len(posts) == 1
        assert posts[0].title == "Show HN: My Startup"

    async def test_fetch_by_domain(self):
        """Should search by domain when domains specified"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector(search_domains=["mystartup.com"])

        mock_response = {
            "hits": [
                {
                    "objectID": "12345",
                    "title": "mystartup.com - The Future of X",
                    "url": "https://mystartup.com",
                    "author": "someone",
                    "points": 75,
                    "num_comments": 15,
                    "created_at": "2024-01-15T10:00:00.000Z",
                    "created_at_i": 1705312800,
                    "story_text": "",
                    "_tags": ["story", "author_someone"],
                }
            ],
            "nbHits": 1,
            "page": 0,
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.status_code = 200
            mock_response_obj.json.return_value = mock_response
            mock_client.get = AsyncMock(return_value=mock_response_obj)
            mock_client.aclose = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            async with collector:
                collector.client = mock_client
                posts = await collector._fetch_posts()

        # Verify the domain query was used
        call_args = mock_client.get.call_args
        assert "mystartup.com" in str(call_args)

    async def test_filters_by_min_points(self):
        """Should filter out posts below min_points"""
        from collectors.hacker_news import HackerNewsCollector

        collector = HackerNewsCollector(min_points=50)

        mock_response = {
            "hits": [
                {
                    "objectID": "1",
                    "title": "Low points post",
                    "url": "https://example1.com",
                    "author": "user1",
                    "points": 10,  # Below threshold
                    "num_comments": 5,
                    "created_at": "2024-01-15T10:00:00.000Z",
                    "created_at_i": 1705312800,
                    "story_text": "",
                    "_tags": ["story"],
                },
                {
                    "objectID": "2",
                    "title": "High points post",
                    "url": "https://example2.com",
                    "author": "user2",
                    "points": 100,  # Above threshold
                    "num_comments": 20,
                    "created_at": "2024-01-15T10:00:00.000Z",
                    "created_at_i": 1705312800,
                    "story_text": "",
                    "_tags": ["story"],
                },
            ],
            "nbHits": 2,
            "page": 0,
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.status_code = 200
            mock_response_obj.json.return_value = mock_response
            mock_client.get = AsyncMock(return_value=mock_response_obj)
            mock_client.aclose = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            async with collector:
                collector.client = mock_client
                posts = await collector._fetch_posts()

        assert len(posts) == 1
        assert posts[0].points == 100
