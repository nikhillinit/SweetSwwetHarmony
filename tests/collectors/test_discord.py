"""
Tests for Discord Server Collector

Tests the Discord collector without requiring actual bot credentials.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from collectors.discord import (
    DiscordCollector,
    MockDiscordCollector,
    DiscordMessage,
    SIGNAL_CONFIDENCE,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def collector():
    """Create a Discord collector without credentials (test mode)."""
    return DiscordCollector(store=None, guild_ids=[123456789])


@pytest.fixture
def mock_collector():
    """Create a mock Discord collector for testing."""
    return MockDiscordCollector(store=None)


@pytest.fixture
def sample_message():
    """Create a sample DiscordMessage."""
    return DiscordMessage(
        guild_id=123456789,
        guild_name="Startup Community",
        channel_id=111111111,
        channel_name="product-launch",
        message_id=999001,
        text="Just launched our amazing meal delivery service!",
        author_id=100001,
        author_name="founder_alex",
        created_at=datetime.now(timezone.utc),
        reaction_count=25,
        reply_count=5,
        urls=["https://healthymeals.com"],
        extracted_company="HealthyMeals",
    )


# =============================================================================
# DISCORD MESSAGE TESTS
# =============================================================================

class TestDiscordMessage:
    """Tests for DiscordMessage dataclass."""

    def test_message_url_construction(self):
        """Message URL is correctly constructed."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test Server",
            channel_id=456,
            channel_name="general",
            message_id=789,
            text="Test message",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert msg.message_url == "https://discord.com/channels/123/456/789"

    def test_engagement_score_calculation(self):
        """Engagement score combines reactions and replies."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Test",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
            reaction_count=10,
            reply_count=5,  # 5 * 2 = 10
        )

        # 10 + 10 = 20
        assert msg.engagement_score == 20

    def test_engagement_score_zero(self):
        """Engagement score is 0 for no engagement."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Test",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert msg.engagement_score == 0


# =============================================================================
# COLLECTOR INITIALIZATION TESTS
# =============================================================================

class TestDiscordCollectorInit:
    """Tests for collector initialization."""

    def test_init_without_credentials(self):
        """Collector initializes without credentials (logs warning)."""
        collector = DiscordCollector(store=None)

        assert collector._credentials_available is False

    def test_init_with_credentials(self):
        """Collector initializes with credentials."""
        collector = DiscordCollector(
            store=None,
            bot_token="test_token"
        )

        assert collector._credentials_available is True

    def test_init_with_env_credentials(self):
        """Collector picks up credentials from environment."""
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "env_token"}):
            collector = DiscordCollector(store=None)
            assert collector._credentials_available is True

    def test_init_with_custom_guilds(self):
        """Custom guild IDs are used."""
        guild_ids = [111, 222, 333]
        collector = DiscordCollector(store=None, guild_ids=guild_ids)

        assert collector.guild_ids == guild_ids

    def test_init_sentiment_enabled_by_default(self):
        """Sentiment analysis is enabled by default."""
        collector = DiscordCollector(store=None)

        assert collector.enable_sentiment is True

    def test_init_sentiment_can_be_disabled(self):
        """Sentiment can be disabled."""
        collector = DiscordCollector(store=None, enable_sentiment=False)

        assert collector.enable_sentiment is False

    def test_init_with_channel_patterns(self):
        """Custom channel patterns are used."""
        patterns = ["launch", "feedback", "showcase"]
        collector = DiscordCollector(store=None, channel_patterns=patterns)

        assert collector.channel_patterns == patterns


# =============================================================================
# CHANNEL MONITORING TESTS
# =============================================================================

class TestChannelMonitoring:
    """Tests for _should_monitor_channel method."""

    def test_monitors_startup_channel(self, collector):
        """Monitors channels with 'startup' in name."""
        assert collector._should_monitor_channel("startup-ideas") is True

    def test_monitors_launch_channel(self, collector):
        """Monitors channels with 'launch' in name."""
        assert collector._should_monitor_channel("product-launch") is True

    def test_monitors_feedback_channel(self, collector):
        """Monitors channels with 'feedback' in name."""
        assert collector._should_monitor_channel("feedback-request") is True

    def test_monitors_general_channel(self, collector):
        """Monitors general channels."""
        assert collector._should_monitor_channel("general") is True

    def test_skips_random_channel(self, collector):
        """Skips channels not matching patterns."""
        assert collector._should_monitor_channel("off-topic-memes") is False

    def test_case_insensitive(self, collector):
        """Channel matching is case insensitive."""
        assert collector._should_monitor_channel("STARTUP-CHAT") is True
        assert collector._should_monitor_channel("Product-Launch") is True


# =============================================================================
# CONSUMER RELEVANCE TESTS
# =============================================================================

class TestConsumerRelevance:
    """Tests for _is_consumer_relevant method."""

    def test_consumer_keyword_match(self, collector):
        """Messages with consumer keywords are relevant."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Check out our new fitness app for workout tracking",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is True

    def test_health_tech_keywords(self, collector):
        """Digital health keywords are relevant (thesis category)."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Building a digital health platform for mental wellness",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is True

    def test_no_consumer_keywords(self, collector):
        """Messages without consumer keywords are not relevant."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Enterprise SaaS platform for B2B data analytics",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is False

    def test_empty_text_not_relevant(self, collector):
        """Messages with no text are not relevant."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text=None,
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._is_consumer_relevant(msg) is False


# =============================================================================
# SIGNAL CLASSIFICATION TESTS
# =============================================================================

class TestSignalClassification:
    """Tests for _classify_signal_type method."""

    def test_funding_news_detection(self, collector):
        """Funding announcements are classified correctly."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="We just raised $5M Series A!",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "funding_news"

    def test_product_launch_detection(self, collector):
        """Product launches are classified correctly."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Just launched our new product today!",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "product_launch"

    def test_feedback_request_detection(self, collector):
        """Feedback requests are classified correctly."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Looking for feedback on my MVP, what do you think?",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "feedback_request"

    def test_general_mention(self, collector):
        """General mentions are classified as community_mention."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Has anyone used that new fitness tracker?",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        assert collector._classify_signal_type(msg) == "community_mention"


# =============================================================================
# CONFIDENCE CALCULATION TESTS
# =============================================================================

class TestConfidenceCalculation:
    """Tests for _calculate_confidence method."""

    def test_base_confidence(self, collector):
        """Base confidence for minimal message."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Check out this new app",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence >= SIGNAL_CONFIDENCE["low"]

    def test_high_reactions_boost(self, collector):
        """High reactions boost confidence."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Check out this new app",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
            reaction_count=100,
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_funding_news_boost(self, collector):
        """Funding news gets confidence boost."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Company raised $10M Series A",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
        )

        confidence = collector._calculate_confidence(msg)
        assert confidence > SIGNAL_CONFIDENCE["medium"]

    def test_confidence_cap(self, collector):
        """Confidence is capped at 0.95."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Company raised $100M Series B!",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
            reaction_count=200,
            reply_count=50,
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

    def test_extract_launched_pattern(self, collector):
        """Extracts company from 'X launched' pattern."""
        text = "HealthyMeals launched their new service today"
        company = collector._extract_company_from_text(text)

        assert company == "HealthyMeals"

    def test_extract_my_startup_pattern(self, collector):
        """Extracts company from 'my startup X' pattern."""
        text = "Check out my startup WellnessTrack for fitness"
        company = collector._extract_company_from_text(text)

        assert company == "WellnessTrack"

    def test_empty_text_returns_none(self, collector):
        """Empty text returns None."""
        company = collector._extract_company_from_text("")
        assert company is None

    def test_filters_common_words(self, collector):
        """Common words are not extracted as companies."""
        text = "The new product launched today"
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

    def test_excluded_domains(self, collector):
        """Discord domains are excluded."""
        assert collector._is_excluded_domain("discord.com") is True
        assert collector._is_excluded_domain("discord.gg") is True

    def test_social_domains_excluded(self, collector):
        """Social media domains are excluded."""
        assert collector._is_excluded_domain("twitter.com") is True
        assert collector._is_excluded_domain("facebook.com") is True

    def test_github_excluded(self, collector):
        """Code hosting domains are excluded."""
        assert collector._is_excluded_domain("github.com") is True
        assert collector._is_excluded_domain("gitlab.com") is True

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

        assert signal.id == "discord_123456789_999001"
        assert signal.source_api == "discord"
        assert signal.confidence > 0

    def test_raw_data_structure(self, collector, sample_message):
        """Raw data contains expected fields."""
        signal = collector._message_to_signal(sample_message)

        assert "guild_id" in signal.raw_data
        assert "guild_name" in signal.raw_data
        assert "channel_name" in signal.raw_data
        assert "canonical_key_candidates" in signal.raw_data
        assert "engagement" in signal.raw_data

    def test_canonical_key_from_url(self, collector):
        """Canonical key uses domain from URL."""
        msg = DiscordMessage(
            guild_id=123,
            guild_name="Test",
            channel_id=456,
            channel_name="test",
            message_id=789,
            text="Check out this app",
            author_id=100,
            author_name="user",
            created_at=datetime.now(timezone.utc),
            urls=["https://myapp.com"],
        )

        signal = collector._message_to_signal(msg)

        assert "domain:myapp.com" in signal.raw_data["canonical_key_candidates"]


# =============================================================================
# MOCK COLLECTOR TESTS
# =============================================================================

class TestMockDiscordCollector:
    """Tests for MockDiscordCollector."""

    @pytest.mark.asyncio
    async def test_returns_mock_signals(self, mock_collector):
        """Mock collector returns sample signals."""
        signals = await mock_collector._collect_signals()

        assert len(signals) == 3
        assert all(s.source_api == "discord" for s in signals)

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

        assert result.collector == "discord"
        assert result.signals_found == 3


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestDiscordCollectorIntegration:
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

    @pytest.mark.asyncio
    async def test_run_without_guild_ids(self):
        """Collector returns empty results without guild IDs."""
        collector = DiscordCollector(
            store=None,
            bot_token="test_token",
            guild_ids=[]  # Empty guild list
        )
        signals = await collector._collect_signals()

        assert signals == []
