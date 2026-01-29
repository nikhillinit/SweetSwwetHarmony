"""
Discord Server Collector for Discovery Engine

Monitors Discord servers for consumer/startup mentions and signals.

Target servers:
- Startup communities
- Product feedback channels
- Consumer/wellness/digital health communities

Signal Strength: MEDIUM (0.5-0.7)
API: discord.py library
Cost: FREE (uses Discord bot account)

PRIVACY COMPLIANCE:
- Only monitors PUBLIC servers/channels where bot is added
- Does NOT store message content (only metadata + links)
- Sentiment analyzed from titles/text only

Environment Variables:
- DISCORD_BOT_TOKEN: Discord bot token (from Discord Developer Portal)

Usage:
    collector = DiscordCollector(
        store=signal_store,
        guild_ids=[123456789, 987654321]  # Server IDs to monitor
    )
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal

if TYPE_CHECKING:
    from utils.community_sentiment import CommunitySentimentAnalyzer, SentimentResult

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Default Discord channels/categories to monitor (by name pattern)
DEFAULT_CHANNEL_PATTERNS = [
    "startup",
    "launch",
    "feedback",
    "product",
    "announcement",
    "general",
    "showcase",
]

# Import thesis-aligned consumer keywords
from collectors.community_keywords import ALL_CONSUMER_KEYWORDS, HIGH_VALUE_KEYWORDS

# Use thesis-aligned keywords for filtering
CONSUMER_KEYWORDS = ALL_CONSUMER_KEYWORDS

# Message signal strength tiers
SIGNAL_CONFIDENCE = {
    "high": 0.7,      # Funding announcement, product launch
    "medium": 0.6,    # Company mention with multiple keywords
    "low": 0.5,       # Single keyword match
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DiscordMessage:
    """Parsed Discord message."""

    guild_id: int
    guild_name: str
    channel_id: int
    channel_name: str
    message_id: int
    text: Optional[str]
    author_id: int
    author_name: str
    created_at: datetime

    # Engagement metrics
    reaction_count: int = 0
    reply_count: int = 0

    # Links/media
    urls: List[str] = field(default_factory=list)
    has_attachments: bool = False

    # Extracted company info
    extracted_company: Optional[str] = None

    @property
    def message_url(self) -> str:
        """Construct public URL to message (may not be accessible if server is private)."""
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"

    @property
    def engagement_score(self) -> int:
        """Calculate total engagement."""
        return self.reaction_count + (self.reply_count * 2)


# =============================================================================
# DISCORD COLLECTOR
# =============================================================================

class DiscordCollector(BaseCollector):
    """
    Discord server collector for consumer startup signals.

    Monitors Discord servers for startup/company mentions.

    IMPORTANT: This collector requires a Discord bot token.
    If token is not provided, it will run in demo mode and return no signals.

    Features:
    - Server/channel monitoring (bot must be added to servers)
    - Consumer keyword filtering
    - Sentiment analysis (message text only)
    - Engagement metrics (reactions, replies)
    - Link extraction for canonical key building

    Signal Types:
    - community_mention: General mention of a company/product
    - product_launch: Product/feature announcement
    - funding_news: Funding/investment news
    - feedback_request: Request for product feedback
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        bot_token: Optional[str] = None,
        guild_ids: Optional[List[int]] = None,
        channel_patterns: Optional[List[str]] = None,
        lookback_hours: int = 24,
        max_messages_per_channel: int = 50,
        enable_sentiment: bool = True,
    ):
        """
        Initialize Discord collector.

        Args:
            store: SignalStore for persistence
            bot_token: Discord bot token (or DISCORD_BOT_TOKEN env var)
            guild_ids: List of guild/server IDs to monitor
            channel_patterns: Channel name patterns to monitor (e.g., ["startup", "feedback"])
            lookback_hours: How far back to fetch messages
            max_messages_per_channel: Max messages to fetch per channel
            enable_sentiment: Whether to analyze sentiment
        """
        super().__init__(
            store=store,
            collector_name="discord",
            api_name="discord",
            retry_config=RetryConfig(max_retries=3, backoff_base=2.0),
        )

        # Discord configuration
        self.bot_token = bot_token or os.getenv("DISCORD_BOT_TOKEN")
        self.guild_ids = guild_ids or []
        self.channel_patterns = channel_patterns or DEFAULT_CHANNEL_PATTERNS

        # Collection parameters
        self.lookback_hours = lookback_hours
        self.max_messages_per_channel = max_messages_per_channel

        # Sentiment analysis
        self.enable_sentiment = enable_sentiment
        self._sentiment_analyzer: Optional["CommunitySentimentAnalyzer"] = None

        if self.enable_sentiment:
            self._init_sentiment_analyzer()

        # Check if credentials are available
        self._credentials_available = bool(self.bot_token)
        if not self._credentials_available:
            logger.warning(
                "Discord bot token not configured. Set DISCORD_BOT_TOKEN environment "
                "variable, or pass bot_token parameter."
            )

    def _init_sentiment_analyzer(self) -> None:
        """Initialize sentiment analyzer."""
        try:
            from utils.community_sentiment import CommunitySentimentAnalyzer, SentimentConfig

            config = SentimentConfig(use_ollama_if_available=False)
            self._sentiment_analyzer = CommunitySentimentAnalyzer(config)
        except ImportError:
            logger.warning("Sentiment analyzer not available")
            self.enable_sentiment = False

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from Discord servers.

        Returns:
            List of Signal objects
        """
        if not self._credentials_available:
            logger.info("Discord bot token not available, returning empty results")
            return []

        if not self.guild_ids:
            logger.info("No Discord guild IDs configured")
            return []

        signals = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)

        try:
            # Try to use discord.py
            messages = await self._fetch_messages_discord_py(cutoff_time)

            for msg in messages:
                # Filter for consumer relevance
                if not self._is_consumer_relevant(msg):
                    continue

                signal = self._message_to_signal(msg)
                signals.append(signal)

            logger.info(f"Discord: Collected {len(signals)} consumer signals")

        except ImportError:
            logger.error(
                "discord.py not installed. Install with: pip install discord.py"
            )
        except Exception as e:
            logger.error(f"Discord collection failed: {e}")

        return signals

    async def _fetch_messages_discord_py(
        self,
        cutoff_time: datetime
    ) -> List[DiscordMessage]:
        """
        Fetch messages using discord.py library.

        Args:
            cutoff_time: Only fetch messages after this time

        Returns:
            List of DiscordMessage objects
        """
        import discord

        messages = []

        # Create client with message content intent
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            logger.info(f"Discord bot connected as {client.user}")

            try:
                for guild_id in self.guild_ids:
                    guild = client.get_guild(guild_id)
                    if not guild:
                        logger.warning(f"Could not find guild {guild_id}")
                        continue

                    # Find relevant channels
                    for channel in guild.text_channels:
                        if not self._should_monitor_channel(channel.name):
                            continue

                        try:
                            # Fetch recent messages
                            async for message in channel.history(
                                limit=self.max_messages_per_channel,
                                after=cutoff_time
                            ):
                                parsed = self._parse_discord_message(guild, channel, message)
                                if parsed:
                                    messages.append(parsed)

                            # Rate limit between channels
                            await asyncio.sleep(0.5)

                        except discord.Forbidden:
                            logger.warning(f"No access to channel {channel.name}")
                        except Exception as e:
                            logger.error(f"Error fetching from {channel.name}: {e}")

            finally:
                await client.close()

        # Run the client
        await client.start(self.bot_token)

        return messages

    def _should_monitor_channel(self, channel_name: str) -> bool:
        """
        Check if channel should be monitored based on name patterns.

        Args:
            channel_name: Discord channel name

        Returns:
            True if channel should be monitored
        """
        name_lower = channel_name.lower()
        return any(pattern.lower() in name_lower for pattern in self.channel_patterns)

    def _parse_discord_message(
        self,
        guild: Any,
        channel: Any,
        message: Any
    ) -> Optional[DiscordMessage]:
        """
        Parse a discord.py message object into DiscordMessage.

        Args:
            guild: Discord Guild object
            channel: Discord TextChannel object
            message: Discord Message object

        Returns:
            DiscordMessage or None if unparseable
        """
        try:
            # Extract text
            text = message.content or ""

            # Skip empty messages without attachments
            if not text and not message.attachments:
                return None

            # Skip bot messages
            if message.author.bot:
                return None

            # Extract URLs from message
            urls = []
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
            text_urls = re.findall(url_pattern, text)
            urls.extend(text_urls)
            urls = list(set(urls))  # Dedupe

            # Count reactions
            reaction_count = sum(r.count for r in message.reactions) if message.reactions else 0

            # Count replies (thread messages)
            reply_count = message.reference is not None

            return DiscordMessage(
                guild_id=guild.id,
                guild_name=guild.name,
                channel_id=channel.id,
                channel_name=channel.name,
                message_id=message.id,
                text=text[:500] if text else None,  # Truncate for compliance
                author_id=message.author.id,
                author_name=message.author.display_name,
                created_at=message.created_at.replace(tzinfo=timezone.utc),
                reaction_count=reaction_count,
                reply_count=1 if reply_count else 0,
                urls=urls,
                has_attachments=bool(message.attachments),
                extracted_company=self._extract_company_from_text(text) if text else None,
            )

        except Exception as e:
            logger.debug(f"Failed to parse message: {e}")
            return None

    def _extract_company_from_text(self, text: str) -> Optional[str]:
        """
        Extract company/product name from message text.

        Args:
            text: Message text

        Returns:
            Extracted company name or None
        """
        if not text:
            return None

        # Pattern: "Company X announced/launched/raised/built..."
        patterns = [
            r"([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)?)\s+(?:announced|launched|raised|secured|built|shipped)",
            r"(?:my|our)\s+(?:startup|app|product|company)\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)?)",
            r"(?:called|named)\s+([A-Z][a-zA-Z0-9]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip()
                # Filter out common non-company words
                if name.lower() not in ["the", "this", "they", "we", "our", "new", "my", "i"]:
                    return name[:50]  # Limit length

        return None

    def _is_consumer_relevant(self, message: DiscordMessage) -> bool:
        """
        Check if message is consumer/startup relevant.

        Args:
            message: DiscordMessage to check

        Returns:
            True if relevant
        """
        if not message.text:
            return False

        text_lower = message.text.lower()

        # Check for consumer keywords
        keyword_count = sum(1 for kw in CONSUMER_KEYWORDS if kw in text_lower)

        # Require at least one keyword match
        return keyword_count >= 1

    def _message_to_signal(self, message: DiscordMessage) -> Signal:
        """
        Convert DiscordMessage to Signal.

        Args:
            message: DiscordMessage to convert

        Returns:
            Signal object
        """
        # Analyze sentiment (text only - compliance safe)
        sentiment_data = None
        if self.enable_sentiment and self._sentiment_analyzer and message.text:
            try:
                result = self._sentiment_analyzer.analyze_sync(message.text)
                sentiment_data = {
                    "sentiment_score": result.score,
                    "sentiment_label": result.label,
                    "sentiment_method": result.method,
                    "sentiment_keywords": result.keywords_found,
                }
            except Exception as e:
                logger.debug(f"Sentiment analysis failed: {e}")

        # Determine signal type based on content
        signal_type = self._classify_signal_type(message)

        # Calculate confidence based on engagement and content
        confidence = self._calculate_confidence(message)

        # Build canonical key candidates
        canonical_key_candidates = []

        # Use extracted company name
        if message.extracted_company:
            canonical_key_candidates.append(
                f"name_loc:{message.extracted_company.lower()}_discord"
            )

        # Use URLs for domain-based keys
        for url in message.urls[:3]:  # Limit to 3
            domain = self._extract_domain(url)
            if domain and not self._is_excluded_domain(domain):
                canonical_key_candidates.append(f"domain:{domain}")

        # Fallback to message ID
        if not canonical_key_candidates:
            canonical_key_candidates.append(
                f"discord_msg:{message.guild_id}_{message.channel_id}_{message.message_id}"
            )

        # Build raw_data
        raw_data: Dict[str, Any] = {
            "guild_id": message.guild_id,
            "guild_name": message.guild_name,
            "channel_id": message.channel_id,
            "channel_name": message.channel_name,
            "message_id": message.message_id,
            "canonical_key_candidates": canonical_key_candidates,
            "canonical_key": canonical_key_candidates[0] if canonical_key_candidates else None,
            "company_name": message.extracted_company,
            "engagement": {
                "reactions": message.reaction_count,
                "replies": message.reply_count,
                "total": message.engagement_score,
            },
            "urls": message.urls[:5],  # Limit stored URLs
            "has_attachments": message.has_attachments,
            # Do NOT store full message text (compliance)
        }

        # Add sentiment data if available
        if sentiment_data:
            raw_data["sentiment"] = sentiment_data

        return Signal(
            id=f"discord_{message.guild_id}_{message.message_id}",
            signal_type=signal_type,
            confidence=confidence,
            source_api="discord",
            source_url=message.message_url,
            raw_data=raw_data,
            detected_at=message.created_at,
        )

    def _classify_signal_type(self, message: DiscordMessage) -> str:
        """
        Classify the signal type based on message content.

        Args:
            message: DiscordMessage to classify

        Returns:
            Signal type string
        """
        if not message.text:
            return "community_mention"

        text_lower = message.text.lower()

        # Check for funding news
        funding_keywords = ["raised", "funding", "series", "investment", "valuation", "round"]
        if any(kw in text_lower for kw in funding_keywords):
            return "funding_news"

        # Check for product launch
        launch_keywords = ["launched", "announcing", "introducing", "release", "shipped", "live now"]
        if any(kw in text_lower for kw in launch_keywords):
            return "product_launch"

        # Check for feedback request
        feedback_keywords = ["feedback", "what do you think", "roast my", "review", "opinions"]
        if any(kw in text_lower for kw in feedback_keywords):
            return "feedback_request"

        return "community_mention"

    def _calculate_confidence(self, message: DiscordMessage) -> float:
        """
        Calculate signal confidence based on engagement and content.

        Args:
            message: DiscordMessage to score

        Returns:
            Confidence score (0.0 to 1.0)
        """
        base_confidence = SIGNAL_CONFIDENCE["medium"]

        # Boost for high reactions
        if message.reaction_count > 50:
            base_confidence += 0.1
        elif message.reaction_count > 10:
            base_confidence += 0.05

        # Boost for replies (indicates discussion)
        if message.reply_count > 0:
            base_confidence += 0.05

        # Boost for extracted company name
        if message.extracted_company:
            base_confidence += 0.05

        # Boost for URLs (actionable)
        if message.urls:
            base_confidence += 0.05

        # Check signal type boost
        signal_type = self._classify_signal_type(message)
        if signal_type == "funding_news":
            base_confidence += 0.1
        elif signal_type == "product_launch":
            base_confidence += 0.05
        elif signal_type == "feedback_request":
            base_confidence += 0.03  # Slight boost - indicates active development

        return min(base_confidence, 0.95)  # Cap at 0.95

    def _extract_domain(self, url: str) -> Optional[str]:
        """
        Extract domain from URL.

        Args:
            url: URL string

        Returns:
            Domain or None
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove www prefix
            if domain.startswith("www."):
                domain = domain[4:]

            return domain if domain else None
        except Exception:
            return None

    def _is_excluded_domain(self, domain: str) -> bool:
        """
        Check if domain should be excluded from canonical keys.

        Args:
            domain: Domain to check

        Returns:
            True if excluded
        """
        excluded = {
            "discord.com", "discord.gg", "discordapp.com",  # Discord itself
            "twitter.com", "x.com", "facebook.com", "instagram.com",  # Social
            "youtube.com", "youtu.be", "tiktok.com",  # Video
            "bit.ly", "goo.gl", "shorturl.at", "t.co",  # URL shorteners
            "medium.com", "substack.com",  # Blog platforms
            "github.com", "gitlab.com",  # Code hosting
            "imgur.com", "giphy.com",  # Image hosting
        }
        return domain in excluded


# =============================================================================
# MOCK COLLECTOR FOR TESTING
# =============================================================================

class MockDiscordCollector(DiscordCollector):
    """
    Mock Discord collector for testing without bot credentials.

    Returns sample messages for testing the signal processing pipeline.
    """

    def __init__(self, *args, **kwargs):
        kwargs["bot_token"] = "mock_token"
        super().__init__(*args, **kwargs)
        self._credentials_available = True  # Force available for testing

    async def _collect_signals(self) -> List[Signal]:
        """Return mock signals for testing."""
        mock_messages = [
            DiscordMessage(
                guild_id=123456789,
                guild_name="Startup Community",
                channel_id=111111111,
                channel_name="product-launch",
                message_id=999001,
                text="Just launched our digital health app for mental wellness tracking! Check it out at https://mindwell.io",
                author_id=100001,
                author_name="founder_alex",
                created_at=datetime.now(timezone.utc),
                reaction_count=25,
                reply_count=5,
                urls=["https://mindwell.io"],
                extracted_company="MindWell",
            ),
            DiscordMessage(
                guild_id=123456789,
                guild_name="Startup Community",
                channel_id=222222222,
                channel_name="feedback",
                message_id=999002,
                text="Looking for feedback on our meal delivery service. We raised $2M seed round last month!",
                author_id=100002,
                author_name="ceo_sarah",
                created_at=datetime.now(timezone.utc),
                reaction_count=10,
                reply_count=3,
                urls=["https://freshmeals.co"],
                extracted_company="FreshMeals",
            ),
            DiscordMessage(
                guild_id=987654321,
                guild_name="Consumer Tech",
                channel_id=333333333,
                channel_name="general",
                message_id=999003,
                text="Stay away from SketchyApp - it's a total scam! Terrible experience.",
                author_id=100003,
                author_name="warning_user",
                created_at=datetime.now(timezone.utc),
                reaction_count=50,
                reply_count=20,
                urls=[],
                extracted_company="SketchyApp",
            ),
        ]

        signals = []
        for msg in mock_messages:
            signal = self._message_to_signal(msg)
            signals.append(signal)

        return signals
