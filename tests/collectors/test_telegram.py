"""
Tests for Telegram Channel Collector

Tests the Telegram collector without requiring actual API credentials.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from collectors.telegram import (
    TelegramCollector,
    MockTelegramCollector,
    TelegramMessage,
    CONSUMER_KEYWORDS,
    SIGNAL_CONFIDENCE,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def collector():
    """Create a Telegram collector without credentials (test mode)."""
    return TelegramCollector(store=None, channels=["@testchannel"])


@pytest.fixture
def mock_collector():
    """Create a mock Telegram collector for testing."""
    return MockTelegramCollector(store=None)


@pytest.fixture
def sample_message():
    """Create a sample TelegramMessage."""
    return TelegramMessage(
        channel_username="@startupnews",
        message_id=12345,
        text="HealthyMeals just launched their amazing meal delivery service!",
        date=datetime.now(timezone.utc),
        views=5000,
        forwards=50,
        replies=10,
        urls=["https://healthymeals.com"],
        extracted_company="HealthyMeals",
    )


# =============================================================================
# TELEGRAM MESSAGE TESTS
# =============================================================================

class TestTelegramMessage:
    """Tests for TelegramMessage dataclass."""

    def test_message_url_construction(self):
        """Message URL is correctly constructed."""
        msg = TelegramMessage(
            channel_username="@testchannel",
            message_id=12345,
            text="Test message",
            date=datetime.now(timezone.utc),
        )

        assert msg.message_url == "https://t.me/testchannel/12345"

    def test_message_url_strips_at_symbol(self):
        """@ symbol is stripped from channel username in URL."""
        msg = TelegramMessage(
            channel_username="@mychannel",
            message_id=999,
            text="Test",
            date=datetime.now(timezone.utc),
        )

        # Should not have double @ or @@ in URL
        assert "@" not in msg.message_url

    def test_engagement_score_calculation(self):
        """Engagement score combines views, forwards, replies."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Test",
            date=datetime.now(timezone.utc),
            views=1000,
            forwards=10,  # 10 * 5 = 50
            replies=5,     # 5 * 2 = 10
        )

        # 1000 + 50 + 10 = 1060
        assert msg.engagement_score == 1060

    def test_engagement_score_zero(self):
        """Engagement score is 0 for no engagement."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Test",
            date=datetime.now(timezone.utc),
        )

        assert msg.engagement_score == 0


# =============================================================================
# COLLECTOR INITIALIZATION TESTS
# =============================================================================

class TestTelegramCollectorInit:
    """Tests for collector initialization."""

    def test_init_without_credentials(self):
        """Collector initializes without credentials (logs warning)."""
        collector = TelegramCollector(store=None)

        assert collector._credentials_available is False

    def test_init_with_credentials(self):
        """Collector initializes with credentials."""
        collector = TelegramCollector(
            store=None,
            api_id="12345",
            api_hash="abcdef"
        )

        assert collector._credentials_available is True

    def test_init_with_env_credentials(self):
        """Collector picks up credentials from environment."""
        with patch.dict("os.environ", {
            "TELEGRAM_API_ID": "env_id",
            "TELEGRAM_API_HASH": "env_hash"
        }):
            collector = TelegramCollector(store=None)
            assert collector._credentials_available is True

    def test_init_with_custom_channels(self):
        """Custom channels are used."""
        channels = ["@channel1", "@channel2"]
        collector = TelegramCollector(store=None, channels=channels)

        assert collector.channels == channels

    def test_init_sentiment_enabled_by_default(self):
        """Sentiment analysis is enabled by default."""
        collector = TelegramCollector(store=None)

        assert collector.enable_sentiment is True

    def test_init_sentiment_can_be_disabled(self):
        """Sentiment can be disabled."""
        collector = TelegramCollector(store=None, enable_sentiment=False)

        assert collector.enable_sentiment is False


# =============================================================================
# CONSUMER RELEVANCE TESTS
# =============================================================================

class TestConsumerRelevance:
    """Tests for _is_consumer_relevant method."""

    def test_consumer_keyword_match(self, collector):
        """Messages with consumer keywords are relevant."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="New fitness app for workout tracking",
            date=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is True

    def test_no_consumer_keywords(self, collector):
        """Messages without consumer keywords are not relevant."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Enterprise SaaS platform for developers",
            date=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is False

    def test_empty_text_not_relevant(self, collector):
        """Messages with no text are not relevant."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text=None,
            date=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is False

    def test_food_beverage_keywords(self, collector):
        """Food and beverage keywords are relevant."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Organic meal delivery service launching in NYC",
            date=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is True

    def test_startup_indicators(self, collector):
        """Startup indicators are relevant."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Just launched my new startup - feedback welcome!",
            date=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is True


# =============================================================================
# SIGNAL CLASSIFICATION TESTS
# =============================================================================

class TestSignalClassification:
    """Tests for _classify_signal_type method."""

    def test_funding_news_detection(self, collector):
        """Funding announcements are classified correctly."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Company X raised $10M Series A funding",
            date=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "funding_news"

    def test_product_launch_detection(self, collector):
        """Product launches are classified correctly."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Announcing our new product release today!",
            date=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "product_launch"

    def test_general_mention(self, collector):
        """General mentions are classified as community_mention."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Has anyone tried the new wellness app?",
            date=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "community_mention"

    def test_empty_text_classification(self, collector):
        """Empty text defaults to community_mention."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text=None,
            date=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "community_mention"


# =============================================================================
# CONFIDENCE CALCULATION TESTS
# =============================================================================

class TestConfidenceCalculation:
    """Tests for _calculate_confidence method."""

    def test_base_confidence(self, collector):
        """Base confidence for minimal message."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Check out this new app",
            date=datetime.now(timezone.utc),
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence >= SIGNAL_CONFIDENCE["low"]

    def test_high_views_boost(self, collector):
        """High views boost confidence."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Check out this new app",
            date=datetime.now(timezone.utc),
            views=15000,
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_high_forwards_boost(self, collector):
        """High forwards boost confidence."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Check out this new app",
            date=datetime.now(timezone.utc),
            forwards=150,
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_funding_news_boost(self, collector):
        """Funding news gets confidence boost."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Company raised $5M Series A",
            date=datetime.now(timezone.utc),
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_confidence_cap(self, collector):
        """Confidence is capped at 0.95."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Company raised $100M Series B!",
            date=datetime.now(timezone.utc),
            views=100000,
            forwards=500,
            replies=100,
            urls=["https://example.com"],
            extracted_company="BigCompany",
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence <= 0.95


# =============================================================================
# COMPANY EXTRACTION TESTS
# =============================================================================

class TestCompanyExtraction:
    """Tests for _extract_company_from_text method."""

    def test_extract_announced_pattern(self, collector):
        """Extracts company from 'X announced' pattern."""
        text = "HealthyFoods announced their Series A today"
        company = collector._extract_company_from_text(text)

        assert company == "HealthyFoods"

    def test_extract_launched_pattern(self, collector):
        """Extracts company from 'X launched' pattern."""
        text = "WellnessApp launched their new feature"
        company = collector._extract_company_from_text(text)

        assert company == "WellnessApp"

    def test_extract_at_mention(self, collector):
        """Extracts @mentions."""
        text = "Check out @coolstartup for the latest updates"
        company = collector._extract_company_from_text(text)

        assert company == "coolstartup"

    def test_empty_text_returns_none(self, collector):
        """Empty text returns None."""
        company = collector._extract_company_from_text("")
        assert company is None

    def test_filters_common_words(self, collector):
        """Common words like 'The' are not extracted."""
        text = "The company announced today"
        company = collector._extract_company_from_text(text)

        # 'The' should be filtered out
        assert company != "The"


# =============================================================================
# DOMAIN EXTRACTION TESTS
# =============================================================================

class TestDomainExtraction:
    """Tests for _extract_domain method."""

    def test_basic_domain(self, collector):
        """Extracts basic domain."""
        domain = collector._extract_domain("https://example.com/page")
        assert domain == "example.com"

    def test_removes_www(self, collector):
        """Removes www prefix."""
        domain = collector._extract_domain("https://www.example.com")
        assert domain == "example.com"

    def test_lowercase(self, collector):
        """Domain is lowercased."""
        domain = collector._extract_domain("https://EXAMPLE.COM")
        assert domain == "example.com"

    def test_excluded_domains(self, collector):
        """Telegram domains are excluded."""
        assert collector._is_excluded_domain("t.me") is True
        assert collector._is_excluded_domain("telegram.org") is True

    def test_social_domains_excluded(self, collector):
        """Social media domains are excluded."""
        assert collector._is_excluded_domain("twitter.com") is True
        assert collector._is_excluded_domain("facebook.com") is True

    def test_company_domain_not_excluded(self, collector):
        """Normal company domains are not excluded."""
        assert collector._is_excluded_domain("example.com") is False
        assert collector._is_excluded_domain("mycompany.io") is False


# =============================================================================
# MESSAGE TO SIGNAL TESTS
# =============================================================================

class TestMessageToSignal:
    """Tests for _message_to_signal method."""

    def test_basic_conversion(self, collector, sample_message):
        """Message converts to Signal correctly."""
        signal = collector._message_to_signal(sample_message)

        assert signal.id == "telegram_@startupnews_12345"
        assert signal.source_api == "telegram"
        assert signal.source_url == "https://t.me/startupnews/12345"
        assert signal.confidence > 0

    def test_raw_data_structure(self, collector, sample_message):
        """Raw data contains expected fields."""
        signal = collector._message_to_signal(sample_message)

        assert "channel_username" in signal.raw_data
        assert "message_id" in signal.raw_data
        assert "canonical_key_candidates" in signal.raw_data
        assert "engagement" in signal.raw_data

    def test_canonical_key_from_url(self, collector):
        """Canonical key uses domain from URL."""
        msg = TelegramMessage(
            channel_username="@test",
            message_id=1,
            text="Check out this app",
            date=datetime.now(timezone.utc),
            urls=["https://myapp.com"],
        )

        signal = collector._message_to_signal(msg)

        assert "domain:myapp.com" in signal.raw_data["canonical_key_candidates"]

    def test_sentiment_included_when_enabled(self, collector, sample_message):
        """Sentiment data included when enabled."""
        # Enable sentiment analyzer
        collector.enable_sentiment = True
        collector._init_sentiment_analyzer()

        signal = collector._message_to_signal(sample_message)

        # Should have sentiment data
        if collector._sentiment_analyzer:
            assert "sentiment" in signal.raw_data


# =============================================================================
# MOCK COLLECTOR TESTS
# =============================================================================

class TestMockTelegramCollector:
    """Tests for MockTelegramCollector."""

    @pytest.mark.asyncio
    async def test_returns_mock_signals(self, mock_collector):
        """Mock collector returns sample signals."""
        signals = await mock_collector._collect_signals()

        assert len(signals) == 3
        assert all(s.source_api == "telegram" for s in signals)

    @pytest.mark.asyncio
    async def test_mock_signals_have_sentiment(self, mock_collector):
        """Mock signals have sentiment analysis."""
        signals = await mock_collector._collect_signals()

        # At least some signals should have sentiment
        with_sentiment = [s for s in signals if "sentiment" in s.raw_data]
        assert len(with_sentiment) > 0

    @pytest.mark.asyncio
    async def test_run_returns_result(self, mock_collector):
        """Run method returns CollectorResult."""
        result = await mock_collector.run(dry_run=True)

        assert result.collector == "telegram"
        assert result.signals_found == 3


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestTelegramCollectorIntegration:
    """Integration tests for the collector."""

    @pytest.mark.asyncio
    async def test_collect_without_credentials(self, collector):
        """Collector returns empty results without credentials."""
        signals = await collector._collect_signals()

        assert signals == []

    @pytest.mark.asyncio
    async def test_run_without_credentials(self, collector):
        """Run returns success with empty results without credentials."""
        result = await collector.run(dry_run=True)

        assert result.signals_found == 0
        assert result.error_message is None
