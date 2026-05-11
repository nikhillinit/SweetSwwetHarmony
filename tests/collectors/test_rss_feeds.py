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
    _normalize_title,
)
from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS
from utils.company_name_extractor import extract_via_regex


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


class TestRSSIdentityExtraction:
    """Regression tests for RSS article-subject identity extraction."""

    def test_wonderbelly_uses_subject_name_not_publisher_domain(self, collector, monkeypatch):
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "baseline")
        article = RSSArticle(
            title="5 Startup Lessons That Helped Wonderbelly Land a 9-Figure Exit",
            description=(
                "The brothers behind Wonderbelly didn't have a background in CPG "
                "or wellness, but they had a personal story and a vision."
            ),
            url="https://www.inc.com/leila-sheridan/5-startup-lessons-that-helped-wonderbelly-land-a-nine-figure-exit/91297579",
            source_feed="Inc.",
            published_at=datetime.now(timezone.utc),
        )

        signal = collector._article_to_signal(article)

        assert signal.raw_data["company_name"] == "Wonderbelly"
        assert "name_loc:wonderbelly" in signal.raw_data["canonical_key_candidates"]
        assert "domain:inc.com" not in signal.raw_data["canonical_key_candidates"]
        assert "name_loc:lessons-that-helped" not in signal.raw_data["canonical_key_candidates"]

    def test_sollos_uses_subject_name_not_publisher_domain(self, collector, monkeypatch):
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "baseline")
        article = RSSArticle(
            title="Barron Trump linked to beverage company based near Mar-a-Lago",
            description=(
                "Barron Trump is listed in public records as a partner in "
                "SOLLOS Yerba Mate Inc., a beverage startup headquartered near "
                "Mar-a-Lago in Palm Beach, Florida, according to January filings."
            ),
            url="https://www.foxbusiness.com/politics/barron-trump-linked-beverage-company-based-near-mar-a-lago",
            source_feed="Fox Business",
            published_at=datetime.now(timezone.utc),
        )

        signal = collector._article_to_signal(article)

        assert signal.raw_data["company_name"] == "SOLLOS Yerba Mate"
        assert "name_loc:sollos-yerba-mate" in signal.raw_data["canonical_key_candidates"]
        assert "domain:foxbusiness.com" not in signal.raw_data["canonical_key_candidates"]

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


# =============================================================================
# TITLE PRE-NORMALIZATION TESTS
# =============================================================================


class TestNormalizeTitle:
    """Tests for _normalize_title() helper."""

    def test_html_entity_amp(self):
        assert _normalize_title("Chili's Grill &amp; Bar") == "Chili's Grill & Bar"

    def test_html_entity_apostrophe(self):
        assert _normalize_title("Chili&#39;s Grill") == "Chili's Grill"

    def test_trademark_symbol_stripped(self):
        assert _normalize_title("Chili's® Grill") == "Chili's Grill"

    def test_copyright_symbol_stripped(self):
        assert _normalize_title("Acme© Products") == "Acme Products"

    def test_tm_symbol_stripped(self):
        assert _normalize_title("Brand™ Launches") == "Brand Launches"

    def test_service_mark_stripped(self):
        assert _normalize_title("Service℠ Expands") == "Service Expands"

    def test_whitespace_collapsed(self):
        assert _normalize_title("  Too   much   space  ") == "Too much space"

    def test_combined_normalization(self):
        """Full chain: unescape + strip trademark + collapse whitespace."""
        raw = "Chili&#39;s®  Grill  &amp;  Bar"
        assert _normalize_title(raw) == "Chili's Grill & Bar"

    def test_empty_string(self):
        assert _normalize_title("") == ""

    def test_no_changes_needed(self):
        assert _normalize_title("Normal Title Here") == "Normal Title Here"


# =============================================================================
# SEGMENTED EXTRACTION TESTS
# =============================================================================


class TestSegmentedExtraction:
    """Tests for segmented extraction fallback in _article_to_signal."""

    def test_signal_324_cosrx_after_question_mark(self):
        """Signal 324: 'Best Eye Patches...? COSRX Expands...' → 'COSRX'."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="Best Eye Patches for Dark Circles? COSRX Expands Its Skincare Line",
            description="Beauty brand COSRX launches new eye patches",
            url="https://prnewswire.com/news/cosrx",
            source_feed="PR Newswire",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        assert signal.raw_data["company_name"] == "COSRX"

    def test_segmented_colon_delimiter(self):
        """Segmented extraction on colon delimiter."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="Market Update: FreshBowl Launches New Product",
            description="Consumer brand launches...",
            url="https://example.com/news",
            source_feed="Test Feed",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        assert signal.raw_data["company_name"] == "FreshBowl"

    def test_segmented_em_dash_delimiter(self):
        """Segmented extraction on em-dash delimiter."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="Industry trends\u2014GlowSkin Announces Expansion",
            description="Beauty company expands",
            url="https://example.com/news",
            source_feed="Test Feed",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        assert signal.raw_data["company_name"] == "GlowSkin"

    def test_segmented_does_not_override_primary(self):
        """When primary extraction finds a company, segmentation is skipped."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="Acme Raises $5M? More Details Inside",
            description="...",
            url="https://example.com/news",
            source_feed="Test Feed",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        # Primary extraction should find "Acme" from "Acme Raises"
        assert signal.raw_data["company_name"] == "Acme"


# =============================================================================
# SIGNAL REGRESSION TESTS (SIGNALS 318-325)
# =============================================================================


class TestSignalRegression:
    """Regression tests for signals 318-325 from latest collection."""

    def test_signal_318_polsia_single_word_no_verb(self):
        """Signal 318: 'Polsia' — single word, no verb → None."""
        assert extract_via_regex("Polsia") is None

    def test_signal_320_market_report(self):
        """Signal 320: Market report title → None (no company)."""
        result = extract_via_regex(
            "Probiotic Ingredients Market to Reach $9.2 Billion by 2030"
        )
        assert result is None

    def test_signal_321_chilis_html_entities(self):
        """Signal 321: HTML entities and trademark in title."""
        raw = "Chili\u2019s\u00ae Grill &amp; Bar Announces New Menu"
        normalized = _normalize_title(raw)
        assert "\u00ae" not in normalized
        assert "&amp;" not in normalized
        assert "\u2019" not in normalized  # smart quote → ASCII apostrophe
        assert "Chili's" in normalized
        # After normalization, extraction should work on clean title
        result = extract_via_regex(normalized)
        # "Chili's" starts with uppercase and has verb "Announces"
        assert result is not None
        assert "Chili's" in result

    def test_signal_322_particles_for_humanity(self):
        """Signal 322: Connector-aware extraction."""
        result = extract_via_regex("Particles for Humanity Announces New Initiative")
        assert result == "Particles for Humanity"

    def test_signal_323_firehook_allcaps(self):
        """Signal 323: ALL-CAPS with connector + base verb 'UNVEIL'."""
        result = extract_via_regex(
            "FIREHOOK AND ITHACA HUMMUS UNVEIL New Snack Line"
        )
        assert result is not None
        assert "FIREHOOK" in result
        assert "ITHACA HUMMUS" in result

    def test_signal_324_cosrx_segmented(self):
        """Signal 324: Mid-sentence after '?' via segmented extraction."""
        collector = RSSFeedCollector(store=None)
        article = RSSArticle(
            title="Best Eye Patches for Dark Circles? COSRX Expands Its Skincare Line",
            description="...",
            url="https://prnewswire.com/news/cosrx",
            source_feed="PR Newswire",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)
        assert signal.raw_data["company_name"] == "COSRX"

    def test_signal_325_half_of_us_shoppers(self):
        """Signal 325: Overcapture prevention — 'Half of U.S. Shoppers' → None."""
        result = extract_via_regex(
            "Half of U.S. Shoppers Choose to Buy New Products from Brands "
            "with Values Aligned to Theirs, Acosta Group Finds"
        )
        assert result is None


# =============================================================================
# DNS PROBE ENRICHMENT INTEGRATION TESTS
# =============================================================================


class TestDnsProbeEnrichment:
    """Integration tests for DNS probe enrichment in _collect_signals."""

    @pytest.fixture
    def _make_collector(self):
        """Factory for RSSFeedCollector with mocked feed fetching."""
        def factory(articles):
            collector = RSSFeedCollector(store=None, feeds=["https://example.com/feed"])
            # Patch _parse_feed to return given articles
            async def fake_parse(url):
                return articles
            collector._parse_feed = fake_parse
            return collector
        return factory

    @pytest.fixture
    def _consumer_article(self):
        """A consumer-relevant article for testing."""
        return RSSArticle(
            title="WellnessApp raises $8M Series A for fitness tracking",
            description="Consumer health startup expands with wellness features.",
            url="https://techcrunch.com/2024/01/15/wellnessapp-raises",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

    @pytest.fixture
    def _no_company_article(self):
        """An article that won't produce a company_name via regex."""
        return RSSArticle(
            title="New trends in consumer wellness and fitness markets",
            description="The beauty and wellness industry sees growth.",
            url="https://techcrunch.com/2024/01/15/wellness-trends",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

    @pytest.mark.asyncio
    async def test_dns_probe_disabled_status(self, _make_collector, _consumer_article, monkeypatch):
        """When DNS_PROBE_ENABLED is unset/false, all signals get skipped_disabled."""
        monkeypatch.delenv("DNS_PROBE_ENABLED", raising=False)
        collector = _make_collector([_consumer_article])
        signals = await collector._collect_signals()
        assert len(signals) >= 1
        for sig in signals:
            assert sig.raw_data["dns_probe_attempted"] is False
            assert sig.raw_data["dns_probe_domain"] is None
            assert sig.raw_data["dns_probe_status"] == "skipped_disabled"
            # Canonical key candidates untouched
            assert "canonical_key_candidates" in sig.raw_data

    @pytest.mark.asyncio
    async def test_dns_probe_hit_metadata(self, _make_collector, _consumer_article, monkeypatch):
        """When probe hits, dns_probe_domain is populated; canonical unchanged."""
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        collector = _make_collector([_consumer_article])

        async def fake_probe(name, max_attempts=4, cache=None):
            return "wellnessapp.com"

        with patch("utils.dns_probe.dns_probe_company", fake_probe):
            signals = await collector._collect_signals()

        hit_signals = [s for s in signals if s.raw_data.get("dns_probe_status") == "hit"]
        assert len(hit_signals) >= 1
        for sig in hit_signals:
            assert sig.raw_data["dns_probe_attempted"] is True
            assert sig.raw_data["dns_probe_domain"] == "wellnessapp.com"
            # Canonical key candidates should NOT contain DNS domain
            ck = sig.raw_data.get("canonical_key_candidates", [])
            assert not any("wellnessapp.com" in c for c in ck), \
                "DNS probe should NOT promote to canonical key in Phase 1"

    @pytest.mark.asyncio
    async def test_dns_probe_cap_status(self, _make_collector, monkeypatch):
        """Signals beyond DNS_PROBE_CAP get skipped_cap."""
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        monkeypatch.setenv("DNS_PROBE_CAP", "1")

        articles = [
            RSSArticle(
                title="WellnessApp raises $8M for fitness tracking",
                description="Consumer health startup expands with wellness.",
                url="https://techcrunch.com/2024/01/15/wellnessapp",
                source_feed="TechCrunch Startups",
                published_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ),
            RSSArticle(
                title="MealBox launches nationwide meal delivery service",
                description="D2C food startup announces expansion.",
                url="https://techcrunch.com/2024/01/15/mealbox",
                source_feed="TechCrunch Startups",
                published_at=datetime.now(timezone.utc) - timedelta(hours=2),
            ),
        ]
        collector = _make_collector(articles)

        async def fake_probe(name, max_attempts=4, cache=None):
            return f"{name.lower().replace(' ', '')}.com"

        with patch("utils.dns_probe.dns_probe_company", fake_probe):
            signals = await collector._collect_signals()

        statuses = {s.raw_data["dns_probe_status"] for s in signals}
        # With cap=1, at most 1 unique company probed; others get skipped_cap
        # (Both articles have different company names → one gets capped)
        if len(signals) >= 2:
            assert "skipped_cap" in statuses or len({s.raw_data.get("company_name") for s in signals}) <= 1

    @pytest.mark.asyncio
    async def test_dns_probe_no_company(self, _make_collector, _no_company_article, monkeypatch):
        """Signals without company_name get skipped_no_company."""
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        collector = _make_collector([_no_company_article])

        async def fake_probe(name, max_attempts=4, cache=None):
            return f"{name.lower()}.com"

        with patch("utils.dns_probe.dns_probe_company", fake_probe):
            signals = await collector._collect_signals()

        no_company = [s for s in signals if not s.raw_data.get("company_name")]
        for sig in no_company:
            assert sig.raw_data["dns_probe_status"] == "skipped_no_company"
