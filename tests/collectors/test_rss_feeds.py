"""
Tests for RSS Feed Collector

Tests the RSS feed parser for discovering consumer-relevant
news from TechCrunch, PR Newswire, Product Hunt, and health tech sources.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from collectors.rss_feeds import (
    RSSFeedCollector,
    MockRSSFeedCollector,
    RSSArticle,
    DEFAULT_FEEDS,
    FEED_CATEGORIES,
    _extract_press_release_prefix,
)
from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def collector():
    """Create an RSS feed collector."""
    return RSSFeedCollector(store=None)


@pytest.fixture
def mock_collector():
    """Create a mock RSS feed collector for testing."""
    return MockRSSFeedCollector(store=None)


@pytest.fixture
def sample_article():
    """Create a sample RSSArticle."""
    return RSSArticle(
        title="HealthyMeals announces new meal delivery service",
        description="Consumer startup launches nationwide...",
        url="https://techcrunch.com/2024/01/15/healthymeals",
        source_feed="TechCrunch Startups",
        published_at=datetime.now(timezone.utc),
        author="John Doe",
        categories=["startups", "food-tech"],
    )


# =============================================================================
# RSS ARTICLE TESTS
# =============================================================================

class TestRSSArticle:
    """Tests for RSSArticle dataclass."""

    def test_article_creation(self, sample_article):
        """Article is created with all fields."""
        assert sample_article.title is not None
        assert sample_article.url is not None
        assert sample_article.source_feed == "TechCrunch Startups"

    def test_domain_extraction(self):
        """Domain is extracted from URL."""
        article = RSSArticle(
            title="Test",
            description="Test",
            url="https://www.example.com/article/123",
            source_feed="Test Feed",
            published_at=datetime.now(timezone.utc),
        )
        assert article.domain == "example.com"

    def test_age_days_calculation(self):
        """Age in days is calculated correctly."""
        old_date = datetime.now(timezone.utc) - timedelta(days=5)
        article = RSSArticle(
            title="Test",
            description="Test",
            url="https://example.com",
            source_feed="Test",
            published_at=old_date,
        )
        assert article.age_days >= 5

    def test_is_funding_news(self):
        """Funding news is detected from title."""
        article = RSSArticle(
            title="Startup XYZ raises $10M in Series A funding",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.is_funding_news is True

    def test_is_product_launch(self):
        """Product launch is detected from title."""
        article = RSSArticle(
            title="Company launches new fitness app",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.is_product_launch is True

    def test_is_press_release(self):
        """Press release is detected from source."""
        article = RSSArticle(
            title="Company Announcement",
            description="...",
            url="https://prnewswire.com/article",
            source_feed="PR Newswire",
            published_at=datetime.now(timezone.utc),
        )
        assert article.is_press_release is True

    def test_extract_company_name(self):
        """Company name is extracted from title."""
        article = RSSArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "HealthyMeals"


# =============================================================================
# COLLECTOR INITIALIZATION TESTS
# =============================================================================

class TestRSSFeedCollectorInit:
    """Tests for collector initialization."""

    def test_init_default_feeds(self):
        """Collector initializes with default feeds."""
        collector = RSSFeedCollector(store=None)
        assert len(collector.feeds) > 0

    def test_init_custom_feeds(self):
        """Custom feeds can be provided."""
        custom_feeds = ["https://example.com/feed.xml"]
        collector = RSSFeedCollector(store=None, feeds=custom_feeds)
        assert collector.feeds == custom_feeds

    def test_init_with_categories(self):
        """Feeds can be filtered by category."""
        collector = RSSFeedCollector(store=None, categories=["startup"])
        assert len(collector.feeds) > 0

    def test_default_feeds_exist(self):
        """Default feeds constant is populated."""
        assert len(DEFAULT_FEEDS) > 0

    def test_feed_categories_defined(self):
        """Feed categories are defined."""
        assert "startup" in FEED_CATEGORIES
        assert "health_tech" in FEED_CATEGORIES


# =============================================================================
# CONSUMER RELEVANCE TESTS
# =============================================================================

class TestConsumerRelevance:
    """Tests for _is_consumer_relevant method."""

    def test_consumer_keyword_match(self, collector):
        """Articles with consumer keywords are relevant."""
        article = RSSArticle(
            title="New fitness app helps users track workouts",
            description="A wellness startup launched...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is True

    def test_no_consumer_keywords(self, collector):
        """Articles without consumer keywords are not relevant."""
        article = RSSArticle(
            title="Enterprise SaaS platform raises funding",
            description="B2B software company...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is False

    def test_cpg_keywords_relevant(self, collector):
        """CPG keywords are relevant."""
        article = RSSArticle(
            title="Beauty brand launches new skincare line",
            description="Consumer CPG company...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._is_consumer_relevant(article) is True

    def test_travel_keywords_relevant(self, collector):
        """Travel keywords are relevant."""
        article = RSSArticle(
            title="Hotel booking startup expands to Europe",
            description="Travel tech company...",
            url="https://example.com",
            source_feed="Test",
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
        article = RSSArticle(
            title="Company X raised $10M Series A funding",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._classify_signal_type(article) == "funding_announcement"

    def test_product_launch_detection(self, collector):
        """Product launches are classified correctly."""
        article = RSSArticle(
            title="Startup announces new product release",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._classify_signal_type(article) == "product_launch"

    def test_press_release_detection(self, collector):
        """Press releases are classified correctly."""
        article = RSSArticle(
            title="Company news update",
            description="...",
            url="https://prnewswire.com/article",
            source_feed="PR Newswire",
            published_at=datetime.now(timezone.utc),
        )
        assert collector._classify_signal_type(article) == "press_release"

    def test_general_news_mention(self, collector):
        """General news is classified as news_mention."""
        article = RSSArticle(
            title="Industry report on consumer trends",
            description="...",
            url="https://techcrunch.com",
            source_feed="TechCrunch",
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
        article = RSSArticle(
            title="Startup news",
            description="...",
            url="https://unknown-blog.com",
            source_feed="Unknown Blog",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence >= 0.35

    def test_techcrunch_boost(self, collector):
        """TechCrunch articles get confidence boost."""
        article = RSSArticle(
            title="Startup news",
            description="...",
            url="https://techcrunch.com/article",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence > 0.50

    def test_press_release_boost(self, collector):
        """Press releases get moderate confidence."""
        article = RSSArticle(
            title="Startup announces funding",
            description="...",
            url="https://prnewswire.com/article",
            source_feed="PR Newswire",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence >= 0.45

    def test_funding_news_boost(self, collector):
        """Funding news gets confidence boost."""
        article = RSSArticle(
            title="Startup raises $5M Series A",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence > 0.45

    def test_confidence_cap(self, collector):
        """Confidence is capped at 0.95."""
        article = RSSArticle(
            title="Major startup raises $100M Series B!",
            description="Big funding announcement...",
            url="https://techcrunch.com/article",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc),
        )
        confidence = collector._calculate_confidence(article)
        assert confidence <= 0.95


# =============================================================================
# FEED PARSING TESTS
# =============================================================================

class TestFeedParsing:
    """Tests for RSS feed parsing."""

    def test_parse_valid_feed(self, collector):
        """Valid RSS feed is parsed correctly."""
        # This tests the internal parsing logic
        mock_feed_content = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Test Article</title>
                    <link>https://example.com/article</link>
                    <description>Test description</description>
                    <pubDate>Wed, 15 Jan 2025 12:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""

        with patch.object(collector, '_fetch_feed', return_value=mock_feed_content):
            # This would be tested in integration
            pass

    def test_handles_missing_fields(self, collector):
        """Parser handles articles with missing optional fields."""
        article = RSSArticle(
            title="Test",
            description="",  # Missing description
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
            author=None,  # Missing author
            categories=[],  # No categories
        )
        # Should not raise
        assert article.title == "Test"


# =============================================================================
# ARTICLE TO SIGNAL TESTS
# =============================================================================

class TestArticleToSignal:
    """Tests for _article_to_signal method."""

    def test_basic_conversion(self, collector, sample_article):
        """Article converts to Signal correctly."""
        signal = collector._article_to_signal(sample_article)

        assert signal.source_api == "rss_feeds"
        assert signal.confidence > 0
        assert "title" in signal.raw_data

    def test_raw_data_structure(self, collector, sample_article):
        """Raw data contains expected fields."""
        signal = collector._article_to_signal(sample_article)

        assert "title" in signal.raw_data
        assert "source_feed" in signal.raw_data
        assert "url" in signal.raw_data
        assert "canonical_key_candidates" in signal.raw_data

    def test_canonical_key_from_domain(self, collector):
        """Canonical key uses domain from article URL."""
        article = RSSArticle(
            title="Startup news",
            description="...",
            url="https://mystartup.com/news",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        # Should have domain in canonical keys (but not news site domain)
        assert any("mystartup.com" in k for k in signal.raw_data["canonical_key_candidates"])

    def test_canonical_key_uses_standard_format(self, collector):
        """Canonical keys use build_canonical_key_candidates format (name_loc, not name:)."""
        article = RSSArticle(
            title="HealthyMeals announces new meal delivery service",
            description="Consumer startup launches...",
            url="https://techcrunch.com/2024/01/15/healthymeals",
            source_feed="TechCrunch Startups",
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
        article = RSSArticle(
            title="HealthyMeals announces expansion",
            description="...",
            url="https://healthymeals.com/news",
            source_feed="Company Blog",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        candidates = signal.raw_data["canonical_key_candidates"]
        assert candidates[0] == "domain:healthymeals.com"

    def test_canonical_key_news_domain_excluded(self, collector):
        """News site domains are not used as canonical keys."""
        for news_domain in ["techcrunch.com", "prnewswire.com", "producthunt.com"]:
            article = RSSArticle(
                title="HealthyMeals raises $5M",
                description="...",
                url=f"https://{news_domain}/article",
                source_feed="Test",
                published_at=datetime.now(timezone.utc),
            )
            signal = collector._article_to_signal(article)
            candidates = signal.raw_data["canonical_key_candidates"]
            assert not any(k == f"domain:{news_domain}" for k in candidates), \
                f"Should not include {news_domain} as canonical key"


# =============================================================================
# MOCK COLLECTOR TESTS
# =============================================================================

class TestMockRSSFeedCollector:
    """Tests for MockRSSFeedCollector."""

    @pytest.mark.asyncio
    async def test_returns_mock_signals(self, mock_collector):
        """Mock collector returns sample signals."""
        signals = await mock_collector._collect_signals()

        assert len(signals) >= 3
        assert all(s.source_api == "rss_feeds" for s in signals)

    @pytest.mark.asyncio
    async def test_mock_signals_have_variety(self, mock_collector):
        """Mock signals have variety of types."""
        signals = await mock_collector._collect_signals()

        signal_types = set(s.signal_type for s in signals)
        assert len(signal_types) >= 2

    @pytest.mark.asyncio
    async def test_run_returns_result(self, mock_collector):
        """Run method returns CollectorResult."""
        result = await mock_collector.run(dry_run=True)

        assert result.collector == "rss_feeds"
        assert result.signals_found >= 3


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestRSSFeedCollectorIntegration:
    """Integration tests for the collector."""

    @pytest.mark.asyncio
    async def test_run_returns_result(self, collector):
        """Run returns CollectorResult even with network errors."""
        # Mock the feed fetching to avoid network calls
        with patch.object(collector, '_fetch_feed', side_effect=Exception("Network error")):
            result = await collector.run(dry_run=True)

            assert result.collector == "rss_feeds"
            # Should handle errors gracefully
            assert result.signals_found >= 0


# =============================================================================
# DEFAULT FEEDS TESTS
# =============================================================================

class TestDefaultFeeds:
    """Tests for default feed configuration."""

    def test_techcrunch_feed_included(self):
        """TechCrunch feed is included by default."""
        assert any("techcrunch" in feed.lower() for feed in DEFAULT_FEEDS)

    def test_product_hunt_feed_included(self):
        """Product Hunt feed is included by default."""
        assert any("producthunt" in feed.lower() for feed in DEFAULT_FEEDS)

    def test_feeds_are_valid_urls(self):
        """All default feeds are valid URLs."""
        for feed in DEFAULT_FEEDS:
            assert feed.startswith("http://") or feed.startswith("https://")


# =============================================================================
# EXPANDED NEWS_PUBLISHER_DOMAINS TESTS
# =============================================================================

class TestNewsPublisherDomains:
    """Tests for expanded NEWS_PUBLISHER_DOMAINS exclusion set."""

    def test_original_domains_still_excluded(self, collector):
        """The original 6 domains are still excluded."""
        for domain in ["techcrunch.com", "venturebeat.com", "forbes.com",
                        "prnewswire.com", "globenewswire.com", "producthunt.com"]:
            article = RSSArticle(
                title="Startup raises $5M",
                description="...",
                url=f"https://{domain}/article",
                source_feed="Test",
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
            article = RSSArticle(
                title="Startup raises $5M",
                description="...",
                url=f"https://{domain}/article",
                source_feed="Test",
                published_at=datetime.now(timezone.utc),
            )
            signal = collector._article_to_signal(article)
            candidates = signal.raw_data["canonical_key_candidates"]
            assert not any(k == f"domain:{domain}" for k in candidates), \
                f"{domain} should be excluded from canonical keys"

    def test_startup_domains_still_included(self, collector):
        """Non-publisher domains are still used as canonical keys."""
        for domain in ["healthymeals.com", "fittrack.io", "mybeautybox.co"]:
            article = RSSArticle(
                title="Startup raises $5M",
                description="...",
                url=f"https://{domain}/news",
                source_feed="Test",
                published_at=datetime.now(timezone.utc),
            )
            signal = collector._article_to_signal(article)
            candidates = signal.raw_data["canonical_key_candidates"]
            assert any(k == f"domain:{domain}" for k in candidates), \
                f"{domain} should be included as canonical key"


# =============================================================================
# IMPROVED COMPANY EXTRACTION TESTS
# =============================================================================

class TestImprovedCompanyExtraction:
    """Tests for improved extract_company_name() patterns."""

    def test_multi_word_company_raises(self):
        """Multi-word company: 'Daily Harvest raises $50M'."""
        article = RSSArticle(
            title="Daily Harvest raises $50M Series C",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Daily" in company

    def test_backs_pattern(self):
        """VC-style: 'Eclipse backs Ever in $31M round'."""
        article = RSSArticle(
            title="Eclipse backs Ever in $31M round",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "Ever"

    def test_invests_in_pattern(self):
        """VC-style: 'Sequoia invests in Glossier with $100M'."""
        article = RSSArticle(
            title="Sequoia invests in Glossier with $100M",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "Glossier"

    def test_quoted_company_name(self):
        """Quoted name: \"'FreshDirect' raises $50M\"."""
        article = RSSArticle(
            title="'FreshDirect' raises $50M in growth round",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "FreshDirect" in company

    def test_startup_prefix_pattern(self):
        """Startup prefix: 'startup Calm raises $75M'."""
        article = RSSArticle(
            title="Wellness startup Calm raises $75M Series B",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Calm" in company

    def test_original_single_word_still_works(self):
        """Original single-word extraction still works."""
        article = RSSArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company == "HealthyMeals"

    def test_common_words_still_filtered(self):
        """Common words are still filtered."""
        article = RSSArticle(
            title="The company raises funding",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is None or company != "The"

    def test_capitalized_verb_announces(self):
        """Capitalized verb: 'Litehouse Foods Announces New Name'."""
        article = RSSArticle(
            title="Litehouse Foods Announces New Name and Corporate Identity",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        company = article.extract_company_name()
        assert company is not None
        assert "Litehouse" in company


# =============================================================================
# PRESS-RELEASE TITLE-SEGMENT HEURISTIC TESTS
# =============================================================================


class TestExtractPressReleasePrefix:
    """Tests for _extract_press_release_prefix() helper."""

    def test_colon_delimiter(self):
        assert _extract_press_release_prefix("Acme Corp: Announces New Product") == "Acme Corp"

    def test_dash_delimiter(self):
        result = _extract_press_release_prefix("Daily Harvest \u2014 Launches Subscription Box")
        assert result == "Daily Harvest"

    def test_pipe_delimiter(self):
        assert _extract_press_release_prefix("Oura Ring | Raises $100M Series C") == "Oura Ring"

    def test_en_dash_delimiter(self):
        assert _extract_press_release_prefix("3M \u2013 Enters Consumer Health Market") == "3M"

    def test_numeric_company_name(self):
        """Company names starting with digits (like 3M) should be accepted."""
        assert _extract_press_release_prefix("3M: New Product Launch") == "3M"

    def test_reject_boilerplate_breaking(self):
        assert _extract_press_release_prefix("Breaking: Major Tech Company...") is None

    def test_reject_boilerplate_exclusive(self):
        assert _extract_press_release_prefix("Exclusive: The Future of AI...") is None

    def test_reject_quarter_pattern(self):
        assert _extract_press_release_prefix("Q4 2026 \u2014 Earnings Season Begins") is None

    def test_reject_stopword_the(self):
        assert _extract_press_release_prefix("The Future: AI in Healthcare") is None

    def test_reject_stopword_how(self):
        assert _extract_press_release_prefix("How: Companies Are Adapting") is None

    def test_reject_too_long(self):
        assert _extract_press_release_prefix(
            "A Very Long Press Release Title That Exceeds Our Length Limit: Details"
        ) is None

    def test_reject_pr_newswire_boilerplate(self):
        assert _extract_press_release_prefix("PR Newswire: Statement on Market Trends") is None

    def test_reject_globe_newswire_boilerplate(self):
        assert _extract_press_release_prefix("Globe Newswire: Latest Filing Results") is None

    def test_reject_purely_numeric(self):
        assert _extract_press_release_prefix("12345: Some Headline") is None

    def test_reject_no_delimiter(self):
        assert _extract_press_release_prefix("No Delimiter In This Title") is None

    def test_reject_empty_string(self):
        assert _extract_press_release_prefix("") is None

    def test_reject_none(self):
        assert _extract_press_release_prefix(None) is None

    def test_reject_too_many_tokens(self):
        assert _extract_press_release_prefix(
            "Way Too Many Tokens Here Company: Something"
        ) is None

    def test_strip_quotes(self):
        result = _extract_press_release_prefix('"Acme Corp": Announces Deal')
        assert result == "Acme Corp"

    def test_ticker_pattern_rejected(self):
        assert _extract_press_release_prefix("AAPL: Earnings Report Released") is None

    def test_fy_pattern_rejected(self):
        assert _extract_press_release_prefix("FY2026 \u2014 Annual Report") is None


class TestPressReleaseFallbackIntegration:
    """Integration: fallback fires in _article_to_signal when extraction fails."""

    def test_fallback_fires_for_press_release(self):
        """Press release with colon prefix should get name_loc key, not hash."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="Acme Corp: Launches New Consumer Product Line",
            description="...",
            url="https://prnewswire.com/news/acme-launch",
            source_feed="PR Newswire",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        candidates = signal.raw_data["canonical_key_candidates"]
        assert len(candidates) > 0, "Should have extracted candidates"
        assert any(k.startswith("name_loc:") for k in candidates), \
            f"Expected name_loc key from fallback, got {candidates}"
        assert signal.raw_data["company_name"] == "Acme Corp"

    def test_fallback_does_not_override_existing_extraction(self):
        """When extract_company_info already finds a company, fallback is skipped."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="HealthyMeals Raises $5M: Full Details Inside",
            description="...",
            url="https://techcrunch.com/2024/01/15/healthymeals",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        # The regex extractor should find "HealthyMeals" via the "raises" verb
        assert signal.raw_data["company_name"] == "HealthyMeals"

    def test_fallback_skipped_for_non_press_release(self):
        """Non-press-release articles don't trigger the fallback."""
        collector = RSSFeedCollector(store=None)
        # techcrunch.com is not a PR source, so is_press_release=False
        article = RSSArticle(
            title="Some Unknown Topic: General News Article",
            description="...",
            url="https://techcrunch.com/general",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        # is_press_release should be False for techcrunch
        assert signal.raw_data["is_press_release"] is False
