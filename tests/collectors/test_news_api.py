"""
Tests for News API Collector

Tests the GNews-based news collector for discovering consumer-relevant
funding announcements, product launches, and market intelligence.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from collectors.news_api import (
    NewsAPICollector,
    MockNewsAPICollector,
    NewsArticle,
    CONSUMER_KEYWORDS,
    SIGNAL_CONFIDENCE,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def collector():
    """Create a news API collector without API key (test mode)."""
    return NewsAPICollector(store=None)


@pytest.fixture
def mock_collector():
    """Create a mock news API collector for testing."""
    return MockNewsAPICollector(store=None)


@pytest.fixture
def sample_article():
    """Create a sample NewsArticle."""
    return NewsArticle(
        title="HealthyMeals raises $5M Series A for meal delivery service",
        description="Consumer health startup HealthyMeals announced today...",
        url="https://techcrunch.com/2024/01/15/healthymeals-raises-5m",
        source="TechCrunch",
        published_at=datetime.now(timezone.utc),
        image_url="https://example.com/image.jpg",
    )


# =============================================================================
# NEWS ARTICLE TESTS
# =============================================================================

class TestNewsArticle:
    """Tests for NewsArticle dataclass."""

    def test_article_creation(self, sample_article):
        """Article is created with all fields."""
        assert sample_article.title is not None
        assert sample_article.url is not None
        assert sample_article.source == "TechCrunch"

    def test_domain_extraction(self):
        """Domain is extracted from URL."""
        article = NewsArticle(
            title="Test",
            description="Test",
            url="https://www.example.com/article/123",
            source="Test Source",
            published_at=datetime.now(timezone.utc),
        )
        assert article.domain == "example.com"

    def test_domain_removes_www(self):
        """www prefix is removed from domain."""
        article = NewsArticle(
            title="Test",
            description="Test",
            url="https://www.techcrunch.com/article",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.domain == "techcrunch.com"

    def test_age_days_calculation(self):
        """Age in days is calculated correctly."""
        old_date = datetime.now(timezone.utc) - timedelta(days=5)
        article = NewsArticle(
            title="Test",
            description="Test",
            url="https://example.com",
            source="Test",
            published_at=old_date,
        )
        assert article.age_days >= 5

    def test_is_funding_news(self):
        """Funding news is detected from title."""
        article = NewsArticle(
            title="Startup XYZ raises $10M in Series A funding",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.is_funding_news is True

    def test_is_not_funding_news(self):
        """Non-funding news is not marked as funding."""
        article = NewsArticle(
            title="New product launched by startup",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.is_funding_news is False

    def test_is_product_launch(self):
        """Product launch is detected from title."""
        article = NewsArticle(
            title="Company announces new fitness app launch",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.is_product_launch is True

    def test_extract_company_from_title(self):
        """Company name is extracted from title patterns."""
        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "HealthyMeals"


# =============================================================================
# COLLECTOR INITIALIZATION TESTS
# =============================================================================

class TestNewsAPICollectorInit:
    """Tests for collector initialization."""

    def test_init_without_api_key(self):
        """Collector initializes without API key (test mode)."""
        collector = NewsAPICollector(store=None)
        assert collector._api_key_available is False

    def test_init_with_api_key(self):
        """Collector initializes with API key."""
        collector = NewsAPICollector(store=None, api_key="test_key_123")
        assert collector._api_key_available is True

    def test_init_with_env_api_key(self):
        """Collector picks up API key from environment."""
        with patch.dict("os.environ", {"GNEWS_API_KEY": "env_key_456"}):
            collector = NewsAPICollector(store=None)
            assert collector._api_key_available is True

    def test_init_with_custom_keywords(self):
        """Custom keywords can be provided."""
        custom_keywords = ["fintech", "banking"]
        collector = NewsAPICollector(store=None, keywords=custom_keywords)
        assert collector.keywords == custom_keywords

    def test_init_default_keywords(self):
        """Default keywords are thesis-aligned."""
        collector = NewsAPICollector(store=None)
        assert len(collector.keywords) > 0
        # Should include consumer thesis keywords
        assert any("food" in kw.lower() or "meal" in kw.lower() for kw in collector.keywords)


# =============================================================================
# CONSUMER RELEVANCE TESTS
# =============================================================================

class TestConsumerRelevance:
    """Tests for _is_consumer_relevant method."""

    def test_consumer_keyword_match(self, collector):
        """Articles with consumer keywords are relevant."""
        article = NewsArticle(
            title="New fitness app helps users track workouts",
            description="A wellness startup launched...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is True

    def test_no_consumer_keywords(self, collector):
        """Articles without consumer keywords are not relevant."""
        article = NewsArticle(
            title="Enterprise SaaS platform raises funding",
            description="B2B software company...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is False

    def test_funding_keywords_relevant(self, collector):
        """Funding-related articles with consumer keywords are relevant."""
        article = NewsArticle(
            title="Meal delivery startup raises $10M Series A",
            description="Consumer food company...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is True

    def test_health_tech_keywords(self, collector):
        """Health tech keywords are relevant."""
        article = NewsArticle(
            title="Digital health platform expands to mental wellness",
            description="Telehealth company...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is True


# =============================================================================
# SIGNAL CLASSIFICATION TESTS
# =============================================================================

class TestSignalClassification:
    """Tests for _classify_signal_type method."""

    def test_funding_news_detection(self, collector):
        """Funding announcements are classified correctly."""
        article = NewsArticle(
            title="Company X raised $10M Series A funding",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._classify_signal_type(article) == "funding_announcement"

    def test_product_launch_detection(self, collector):
        """Product launches are classified correctly."""
        article = NewsArticle(
            title="Startup announces new product release",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._classify_signal_type(article) == "product_launch"

    def test_general_news_mention(self, collector):
        """General news is classified as news_mention."""
        article = NewsArticle(
            title="Industry report on consumer trends",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._classify_signal_type(article) == "news_mention"


# =============================================================================
# CONFIDENCE CALCULATION TESTS
# =============================================================================

class TestConfidenceCalculation:
    """Tests for _calculate_confidence method."""

    def test_base_confidence(self, collector):
        """Base confidence for minimal article."""
        article = NewsArticle(
            title="Startup news",
            description="...",
            url="https://example.com",
            source="Unknown Blog",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence >= SIGNAL_CONFIDENCE["low"]

    def test_authoritative_source_boost(self, collector):
        """Authoritative sources get confidence boost."""
        article = NewsArticle(
            title="Startup news",
            description="...",
            url="https://techcrunch.com/article",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_funding_news_boost(self, collector):
        """Funding news gets confidence boost."""
        article = NewsArticle(
            title="Startup raises $5M Series A",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_freshness_boost(self, collector):
        """Recent articles get freshness boost."""
        fresh_article = NewsArticle(
            title="Startup news",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        old_article = NewsArticle(
            title="Startup news",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        assert collector._calculate_confidence(fresh_article) > collector._calculate_confidence(old_article)

    def test_confidence_cap(self, collector):
        """Confidence is capped at 0.95."""
        article = NewsArticle(
            title="Major startup raises $100M Series B!",
            description="Big funding announcement...",
            url="https://techcrunch.com/article",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence <= 0.95


# =============================================================================
# COMPANY EXTRACTION TESTS
# =============================================================================

class TestCompanyExtraction:
    """Tests for company name extraction from articles."""

    def test_extract_from_raises_pattern(self, collector):
        """Extracts company from 'X raises' pattern."""
        article = NewsArticle(
            title="HealthyMeals raises $5M Series A",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = collector._extract_company_name(article)
        assert company == "HealthyMeals"

    def test_extract_from_announces_pattern(self, collector):
        """Extracts company from 'X announces' pattern."""
        article = NewsArticle(
            title="FitApp announces new feature launch",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = collector._extract_company_name(article)
        assert company == "FitApp"

    def test_extract_filters_common_words(self, collector):
        """Common words like 'The' are filtered out."""
        article = NewsArticle(
            title="The company raises funding",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = collector._extract_company_name(article)
        assert company != "The"


# =============================================================================
# ARTICLE TO SIGNAL TESTS
# =============================================================================

class TestArticleToSignal:
    """Tests for _article_to_signal method."""

    def test_basic_conversion(self, collector, sample_article):
        """Article converts to Signal correctly."""
        signal = collector._article_to_signal(sample_article)

        assert signal.source_api == "news_api"
        assert signal.confidence > 0
        assert "title" in signal.raw_data

    def test_raw_data_structure(self, collector, sample_article):
        """Raw data contains expected fields."""
        signal = collector._article_to_signal(sample_article)

        assert "title" in signal.raw_data
        assert "source" in signal.raw_data
        assert "url" in signal.raw_data
        assert "canonical_key_candidates" in signal.raw_data

    def test_canonical_key_from_domain(self, collector):
        """Canonical key uses domain from URL."""
        article = NewsArticle(
            title="Startup news",
            description="...",
            url="https://mystartup.com/news",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        assert "domain:mystartup.com" in signal.raw_data["canonical_key_candidates"]


# =============================================================================
# MOCK COLLECTOR TESTS
# =============================================================================

class TestMockNewsAPICollector:
    """Tests for MockNewsAPICollector."""

    @pytest.mark.asyncio
    async def test_returns_mock_signals(self, mock_collector):
        """Mock collector returns sample signals."""
        signals = await mock_collector._collect_signals()

        assert len(signals) >= 3
        assert all(s.source_api == "news_api" for s in signals)

    @pytest.mark.asyncio
    async def test_mock_signals_have_variety(self, mock_collector):
        """Mock signals have variety of types."""
        signals = await mock_collector._collect_signals()

        signal_types = set(s.signal_type for s in signals)
        assert len(signal_types) >= 2  # Should have multiple types

    @pytest.mark.asyncio
    async def test_run_returns_result(self, mock_collector):
        """Run method returns CollectorResult."""
        result = await mock_collector.run(dry_run=True)

        assert result.collector == "news_api"
        assert result.signals_found >= 3


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestNewsAPICollectorIntegration:
    """Integration tests for the collector."""

    @pytest.mark.asyncio
    async def test_collect_without_api_key(self, collector):
        """Collector returns empty results without API key."""
        signals = await collector._collect_signals()

        assert signals == []

    @pytest.mark.asyncio
    async def test_run_without_api_key(self, collector):
        """Run returns success with empty results without API key."""
        result = await collector.run(dry_run=True)

        assert result.signals_found == 0
        assert result.error_message is None


# =============================================================================
# AUTHORITATIVE SOURCES TESTS
# =============================================================================

class TestAuthoritativeSources:
    """Tests for authoritative source detection."""

    def test_techcrunch_is_authoritative(self, collector):
        """TechCrunch is recognized as authoritative."""
        assert collector._is_authoritative_source("TechCrunch") is True

    def test_venturebeat_is_authoritative(self, collector):
        """VentureBeat is recognized as authoritative."""
        assert collector._is_authoritative_source("VentureBeat") is True

    def test_unknown_blog_not_authoritative(self, collector):
        """Unknown blogs are not authoritative."""
        assert collector._is_authoritative_source("Random Blog") is False

    def test_case_insensitive(self, collector):
        """Source matching is case-insensitive."""
        assert collector._is_authoritative_source("techcrunch") is True
        assert collector._is_authoritative_source("TECHCRUNCH") is True
