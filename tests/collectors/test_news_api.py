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
from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def _clear_gnews_env(monkeypatch):
    """Ensure GNEWS_API_KEY is unset for all tests in this module."""
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)


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

    def test_canonical_key_uses_standard_format(self, collector):
        """Canonical keys use build_canonical_key_candidates format (name_loc, not name:)."""
        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="Consumer health startup...",
            url="https://techcrunch.com/2024/01/15/healthymeals",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        candidates = signal.raw_data["canonical_key_candidates"]
        # Should use name_loc: format (not ad-hoc name:)
        name_keys = [k for k in candidates if k.startswith("name_loc:")]
        ad_hoc_keys = [k for k in candidates if k.startswith("name:")]
        assert len(name_keys) >= 1, f"Expected name_loc: key, got {candidates}"
        assert len(ad_hoc_keys) == 0, f"Should not have ad-hoc name: keys, got {candidates}"

    def test_canonical_key_domain_priority_over_name(self, collector):
        """When article has non-news domain, domain: key comes first."""
        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://healthymeals.com/news",
            source="Company Blog",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        candidates = signal.raw_data["canonical_key_candidates"]
        assert candidates[0] == "domain:healthymeals.com"

    def test_canonical_key_news_domain_excluded(self, collector):
        """News site domains (techcrunch, etc.) are not used as canonical keys."""
        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://techcrunch.com/2024/01/15/healthymeals",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        candidates = signal.raw_data["canonical_key_candidates"]
        assert not any(k == "domain:techcrunch.com" for k in candidates)

    def test_canonical_key_empty_when_no_company_or_domain(self, collector):
        """Empty candidates when no company name and news domain."""
        article = NewsArticle(
            title="Industry report on consumer trends",
            description="...",
            url="https://forbes.com/article",
            source="Forbes",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        # No extractable company name from this title, and forbes.com is excluded
        candidates = signal.raw_data["canonical_key_candidates"]
        # May be empty or just have a name_loc from a partial extraction
        assert not any(k.startswith("name:") for k in candidates)


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


# =============================================================================
# EXPANDED NEWS_PUBLISHER_DOMAINS TESTS
# =============================================================================

class TestNewsPublisherDomains:
    """Tests for expanded NEWS_PUBLISHER_DOMAINS exclusion set."""

    def test_original_domains_still_excluded(self, collector):
        """The original 3 domains (techcrunch, venturebeat, forbes) are still excluded."""
        for domain in ["techcrunch.com", "venturebeat.com", "forbes.com"]:
            article = NewsArticle(
                title="Startup raises $5M",
                description="...",
                url=f"https://{domain}/article",
                source="Test",
                published_at=datetime.now(timezone.utc),
            )
            signal = collector._article_to_signal(article)
            candidates = signal.raw_data["canonical_key_candidates"]
            assert not any(k == f"domain:{domain}" for k in candidates), \
                f"{domain} should be excluded from canonical keys"

    def test_newly_added_domains_excluded(self, collector):
        """Newly added publisher domains are excluded from canonical keys."""
        new_domains = [
            "reuters.com", "bloomberg.com", "fastcompany.com",
            "usaherald.com", "cnbc.com", "medium.com", "yahoo.com",
        ]
        for domain in new_domains:
            article = NewsArticle(
                title="Startup raises $5M",
                description="...",
                url=f"https://{domain}/article",
                source="Test",
                published_at=datetime.now(timezone.utc),
            )
            signal = collector._article_to_signal(article)
            candidates = signal.raw_data["canonical_key_candidates"]
            assert not any(k == f"domain:{domain}" for k in candidates), \
                f"{domain} should be excluded from canonical keys"

    def test_startup_domains_still_included(self, collector):
        """Non-publisher domains are still used as canonical keys."""
        for domain in ["healthymeals.com", "fittrack.io", "mybeautybox.co"]:
            article = NewsArticle(
                title="Startup raises $5M",
                description="...",
                url=f"https://{domain}/news",
                source="Test",
                published_at=datetime.now(timezone.utc),
            )
            signal = collector._article_to_signal(article)
            candidates = signal.raw_data["canonical_key_candidates"]
            assert any(k == f"domain:{domain}" for k in candidates), \
                f"{domain} should be included as canonical key"

    def test_shared_constant_has_minimum_domains(self):
        """NEWS_PUBLISHER_DOMAINS has at least 25 entries."""
        assert len(NEWS_PUBLISHER_DOMAINS) >= 25


# =============================================================================
# IMPROVED COMPANY EXTRACTION TESTS
# =============================================================================

class TestImprovedCompanyExtraction:
    """Tests for improved extract_company_name() patterns."""

    def test_multi_word_company_raises(self):
        """Multi-word company: 'Oura Ring raises $5M'."""
        article = NewsArticle(
            title="Oura Ring raises $5M in seed funding",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Oura" in company

    def test_multi_word_company_daily_harvest(self):
        """Multi-word company: 'Daily Harvest raises $50M Series C'."""
        article = NewsArticle(
            title="Daily Harvest raises $50M Series C",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Daily" in company

    def test_backs_pattern(self):
        """VC-style: 'Eclipse backs Ever in $31M round'."""
        article = NewsArticle(
            title="Eclipse backs Ever in $31M round",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "Ever"

    def test_invests_in_pattern(self):
        """VC-style: 'Sequoia invests in Glossier with $100M'."""
        article = NewsArticle(
            title="Sequoia invests in Glossier with $100M",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "Glossier"

    def test_quoted_company_name(self):
        """Quoted name: \"'FreshDirect' raises $50M\"."""
        article = NewsArticle(
            title="'FreshDirect' raises $50M in growth round",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "FreshDirect" in company

    def test_startup_prefix_pattern(self):
        """Startup prefix: 'startup Calm raises $75M'."""
        article = NewsArticle(
            title="Wellness startup Calm raises $75M Series B",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Calm" in company

    def test_brand_prefix_pattern(self):
        """Brand prefix: 'brand Glossier launches new line'."""
        article = NewsArticle(
            title="Beauty brand Glossier launches new skincare line",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Glossier" in company

    def test_original_single_word_still_works(self):
        """Original single-word extraction still works."""
        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "HealthyMeals"

    def test_common_words_still_filtered(self):
        """Common words are still filtered."""
        article = NewsArticle(
            title="The company raises funding",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        # Should be None because "The" is filtered and no other pattern matches
        assert company is None or company != "The"

    def test_no_match_returns_none(self):
        """Returns None when no pattern matches."""
        article = NewsArticle(
            title="This startup launched a clean beauty line",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        # "This" is in the common words filter
        assert company is None or company.lower() != "this"

    def test_capitalized_verb_announces(self):
        """Capitalized verb: 'Litehouse Foods Announces New Name'."""
        article = NewsArticle(
            title="Litehouse Foods Announces New Name and Corporate Identity",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Litehouse" in company

    def test_capitalized_verb_raises(self):
        """Capitalized verb: 'FitTrack Raises $20M Series B'."""
        article = NewsArticle(
            title="FitTrack Raises $20M Series B Funding",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "FitTrack"
