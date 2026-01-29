"""
Telegram Channel Collector for Discovery Engine

Monitors Telegram channels for consumer/startup mentions and signals.

Target channels:
- Consumer/wellness communities
- CPG/food-tech discussions
- Startup announcement channels

Signal Strength: MEDIUM (0.5-0.7)
API: Telethon (MTProto) or pyrogram
Cost: FREE (uses personal Telegram account)

PRIVACY COMPLIANCE:
- Only monitors PUBLIC channels (not private groups)
- Does NOT store message content (only metadata + links)
- Sentiment analyzed from titles/captions only

Environment Variables:
- TELEGRAM_API_ID: Telegram API ID (from my.telegram.org)
- TELEGRAM_API_HASH: Telegram API hash
- TELEGRAM_SESSION_STRING: Optional session string (avoids login prompt)

Usage:
    collector = TelegramCollector(
        store=signal_store,
        channels=["@startupnews", "@consumerbuzz"]
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

# Default Telegram channels to monitor (public consumer/startup channels)
DEFAULT_CHANNELS = [
    # These are placeholder examples - should be configured per use case
    # "@startupnews",
    # "@consumerbuzz",
    # "@wellfounded",
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
class TelegramMessage:
    """Parsed Telegram channel message."""

    channel_username: str
    message_id: int
    text: Optional[str]  # Caption or message text
    date: datetime

    # Engagement metrics (if available)
    views: int = 0
    forwards: int = 0
    replies: int = 0

    # Links/media
    urls: List[str] = field(default_factory=list)
    has_media: bool = False

    # Extracted company info
    extracted_company: Optional[str] = None

    @property
    def message_url(self) -> str:
        """Construct public URL to message."""
        channel = self.channel_username.lstrip("@")
        return f"https://t.me/{channel}/{self.message_id}"

    @property
    def engagement_score(self) -> int:
        """Calculate total engagement."""
        return self.views + (self.forwards * 5) + (self.replies * 2)


# =============================================================================
# TELEGRAM COLLECTOR
# =============================================================================

class TelegramCollector(BaseCollector):
    """
    Telegram channel collector for consumer startup signals.

    Monitors public Telegram channels for startup/company mentions.

    IMPORTANT: This collector requires Telegram API credentials.
    If credentials are not provided, it will run in demo mode
    and return no signals.

    Features:
    - Public channel monitoring only (privacy compliant)
    - Consumer keyword filtering
    - Sentiment analysis (title/caption only)
    - Engagement metrics (views, forwards, replies)
    - Link extraction for canonical key building

    Signal Types:
    - community_mention: General mention of a company/product
    - product_launch: Product/feature announcement
    - funding_news: Funding/investment news
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        channels: Optional[List[str]] = None,
        api_id: Optional[str] = None,
        api_hash: Optional[str] = None,
        session_string: Optional[str] = None,
        lookback_hours: int = 24,
        max_messages_per_channel: int = 50,
        enable_sentiment: bool = True,
    ):
        """
        Initialize Telegram collector.

        Args:
            store: SignalStore for persistence
            channels: List of channel usernames (e.g., ["@startupnews"])
            api_id: Telegram API ID (or TELEGRAM_API_ID env var)
            api_hash: Telegram API hash (or TELEGRAM_API_HASH env var)
            session_string: Session string (or TELEGRAM_SESSION_STRING env var)
            lookback_hours: How far back to fetch messages
            max_messages_per_channel: Max messages to fetch per channel
            enable_sentiment: Whether to analyze sentiment
        """
        super().__init__(
            store=store,
            collector_name="telegram",
            api_name="telegram",
            retry_config=RetryConfig(max_retries=3, backoff_base=2.0),
        )

        # Channel configuration
        self.channels = channels or DEFAULT_CHANNELS

        # Telegram credentials
        self.api_id = api_id or os.getenv("TELEGRAM_API_ID")
        self.api_hash = api_hash or os.getenv("TELEGRAM_API_HASH")
        self.session_string = session_string or os.getenv("TELEGRAM_SESSION_STRING")

        # Collection parameters
        self.lookback_hours = lookback_hours
        self.max_messages_per_channel = max_messages_per_channel

        # Sentiment analysis
        self.enable_sentiment = enable_sentiment
        self._sentiment_analyzer: Optional["CommunitySentimentAnalyzer"] = None

        if self.enable_sentiment:
            self._init_sentiment_analyzer()

        # Check if credentials are available
        self._credentials_available = bool(self.api_id and self.api_hash)
        if not self._credentials_available:
            logger.warning(
                "Telegram credentials not configured. Set TELEGRAM_API_ID and "
                "TELEGRAM_API_HASH environment variables, or pass api_id/api_hash."
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
        Collect signals from Telegram channels.

        Returns:
            List of Signal objects
        """
        if not self._credentials_available:
            logger.info("Telegram credentials not available, returning empty results")
            return []

        if not self.channels:
            logger.info("No Telegram channels configured")
            return []

        signals = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)

        try:
            # Try to use telethon
            messages = await self._fetch_messages_telethon(cutoff_time)

            for msg in messages:
                # Filter for consumer relevance
                if not self._is_consumer_relevant(msg):
                    continue

                signal = self._message_to_signal(msg)
                signals.append(signal)

            logger.info(f"Telegram: Collected {len(signals)} consumer signals")

        except ImportError:
            logger.error(
                "Telethon not installed. Install with: pip install telethon"
            )
        except Exception as e:
            logger.error(f"Telegram collection failed: {e}")

        return signals

    async def _fetch_messages_telethon(
        self,
        cutoff_time: datetime
    ) -> List[TelegramMessage]:
        """
        Fetch messages using Telethon library.

        Args:
            cutoff_time: Only fetch messages after this time

        Returns:
            List of TelegramMessage objects
        """
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        messages = []

        # Create client with session string if available
        if self.session_string:
            session = StringSession(self.session_string)
        else:
            session = StringSession()

        async with TelegramClient(
            session,
            int(self.api_id),
            self.api_hash
        ) as client:
            for channel in self.channels:
                try:
                    # Ensure channel starts with @
                    channel_username = channel if channel.startswith("@") else f"@{channel}"

                    entity = await client.get_entity(channel_username)

                    # Fetch recent messages
                    async for message in client.iter_messages(
                        entity,
                        limit=self.max_messages_per_channel
                    ):
                        # Skip messages older than cutoff
                        if message.date < cutoff_time:
                            break

                        # Parse message
                        parsed = self._parse_telegram_message(channel_username, message)
                        if parsed:
                            messages.append(parsed)

                    # Rate limit between channels
                    await asyncio.sleep(1.0)

                except Exception as e:
                    logger.error(f"Failed to fetch from {channel}: {e}")

        return messages

    def _parse_telegram_message(
        self,
        channel_username: str,
        message: Any
    ) -> Optional[TelegramMessage]:
        """
        Parse a Telethon message object into TelegramMessage.

        Args:
            channel_username: Channel username
            message: Telethon Message object

        Returns:
            TelegramMessage or None if unparseable
        """
        try:
            # Extract text (message or caption)
            text = message.message or ""

            # Skip empty messages without media
            if not text and not message.media:
                return None

            # Extract URLs from message
            urls = []
            if message.entities:
                for entity in message.entities:
                    if hasattr(entity, "url") and entity.url:
                        urls.append(entity.url)

            # Also extract URLs from text using regex
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
            text_urls = re.findall(url_pattern, text or "")
            urls.extend(text_urls)
            urls = list(set(urls))  # Dedupe

            return TelegramMessage(
                channel_username=channel_username,
                message_id=message.id,
                text=text[:500] if text else None,  # Truncate for compliance
                date=message.date.replace(tzinfo=timezone.utc) if message.date else datetime.now(timezone.utc),
                views=message.views or 0,
                forwards=message.forwards or 0,
                replies=message.replies.replies if message.replies else 0,
                urls=urls,
                has_media=bool(message.media),
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

        # Pattern: "Company X announced/launched/raised..."
        patterns = [
            r"([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)?)\s+(?:announced|launched|raised|secured)",
            r"(?:startup|company)\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)?)",
            r"@([a-zA-Z0-9_]+)",  # @mentions
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip()
                # Filter out common non-company words
                if name.lower() not in ["the", "this", "they", "we", "our", "new"]:
                    return name[:50]  # Limit length

        return None

    def _is_consumer_relevant(self, message: TelegramMessage) -> bool:
        """
        Check if message is consumer/startup relevant.

        Args:
            message: TelegramMessage to check

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

    def _message_to_signal(self, message: TelegramMessage) -> Signal:
        """
        Convert TelegramMessage to Signal.

        Args:
            message: TelegramMessage to convert

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
                f"name_loc:{message.extracted_company.lower()}_telegram"
            )

        # Use URLs for domain-based keys
        for url in message.urls[:3]:  # Limit to 3
            domain = self._extract_domain(url)
            if domain and not self._is_excluded_domain(domain):
                canonical_key_candidates.append(f"domain:{domain}")

        # Fallback to message ID
        if not canonical_key_candidates:
            canonical_key_candidates.append(
                f"telegram_msg:{message.channel_username}_{message.message_id}"
            )

        # Build raw_data
        raw_data: Dict[str, Any] = {
            "channel_username": message.channel_username,
            "message_id": message.message_id,
            "canonical_key_candidates": canonical_key_candidates,
            "canonical_key": canonical_key_candidates[0] if canonical_key_candidates else None,
            "company_name": message.extracted_company,
            "engagement": {
                "views": message.views,
                "forwards": message.forwards,
                "replies": message.replies,
                "total": message.engagement_score,
            },
            "urls": message.urls[:5],  # Limit stored URLs
            "has_media": message.has_media,
            # Do NOT store full message text (compliance)
        }

        # Add sentiment data if available
        if sentiment_data:
            raw_data["sentiment"] = sentiment_data

        return Signal(
            id=f"telegram_{message.channel_username}_{message.message_id}",
            signal_type=signal_type,
            confidence=confidence,
            source_api="telegram",
            source_url=message.message_url,
            raw_data=raw_data,
            detected_at=message.date,
        )

    def _classify_signal_type(self, message: TelegramMessage) -> str:
        """
        Classify the signal type based on message content.

        Args:
            message: TelegramMessage to classify

        Returns:
            Signal type string
        """
        if not message.text:
            return "community_mention"

        text_lower = message.text.lower()

        # Check for funding news
        funding_keywords = ["raised", "funding", "series", "investment", "valuation"]
        if any(kw in text_lower for kw in funding_keywords):
            return "funding_news"

        # Check for product launch
        launch_keywords = ["launched", "announcing", "introducing", "release", "new product"]
        if any(kw in text_lower for kw in launch_keywords):
            return "product_launch"

        return "community_mention"

    def _calculate_confidence(self, message: TelegramMessage) -> float:
        """
        Calculate signal confidence based on engagement and content.

        Args:
            message: TelegramMessage to score

        Returns:
            Confidence score (0.0 to 1.0)
        """
        base_confidence = SIGNAL_CONFIDENCE["medium"]

        # Boost for high engagement
        if message.views > 10000:
            base_confidence += 0.1
        elif message.views > 1000:
            base_confidence += 0.05

        # Boost for forwards (viral indicator)
        if message.forwards > 100:
            base_confidence += 0.1
        elif message.forwards > 10:
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
            "t.me", "telegram.org", "telegram.me",  # Telegram itself
            "twitter.com", "x.com", "facebook.com", "instagram.com",  # Social
            "youtube.com", "youtu.be", "tiktok.com",  # Video
            "bit.ly", "goo.gl", "shorturl.at",  # URL shorteners
            "medium.com", "substack.com",  # Blog platforms
        }
        return domain in excluded


# =============================================================================
# MOCK COLLECTOR FOR TESTING
# =============================================================================

class MockTelegramCollector(TelegramCollector):
    """
    Mock Telegram collector for testing without API credentials.

    Returns sample messages for testing the signal processing pipeline.
    """

    def __init__(self, *args, **kwargs):
        kwargs["api_id"] = "mock"
        kwargs["api_hash"] = "mock"
        super().__init__(*args, **kwargs)
        self._credentials_available = True  # Force available for testing

    async def _collect_signals(self) -> List[Signal]:
        """Return mock signals for testing."""
        mock_messages = [
            TelegramMessage(
                channel_username="@startupnews",
                message_id=12345,
                text="HealthyMeals just raised $5M Series A for their meal delivery startup! Great progress in the CPG space.",
                date=datetime.now(timezone.utc),
                views=5000,
                forwards=50,
                replies=10,
                urls=["https://healthymeals.com"],
                extracted_company="HealthyMeals",
            ),
            TelegramMessage(
                channel_username="@consumerbuzz",
                message_id=67890,
                text="New fitness app WellnessTrack launching today. Focus on meditation and sleep tracking.",
                date=datetime.now(timezone.utc),
                views=2000,
                forwards=20,
                replies=5,
                urls=["https://wellnesstrack.io"],
                extracted_company="WellnessTrack",
            ),
            TelegramMessage(
                channel_username="@startupnews",
                message_id=11111,
                text="Terrible news - FitFraud exposed as scam operation. Stay away!",
                date=datetime.now(timezone.utc),
                views=10000,
                forwards=200,
                replies=50,
                urls=[],
                extracted_company="FitFraud",
            ),
        ]

        signals = []
        for msg in mock_messages:
            signal = self._message_to_signal(msg)
            signals.append(signal)

        return signals
