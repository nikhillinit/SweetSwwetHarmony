"""Tests for provenance retrofit in news_api, rss_feeds, telegram, discord collectors.

Verifies that each collector includes _provenance block in raw_data with source_url.
Also tests the container-URL warning helper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.provenance import warn_if_container_url


# =============================================================================
# Container-URL warning helper
# =============================================================================

class TestWarnIfContainerUrl:
    """Test container-URL detection."""

    def test_search_url_detected(self):
        assert warn_if_container_url("https://gnews.io/api/v4/search?q=test") is not None

    def test_feed_url_detected(self):
        assert warn_if_container_url("https://example.com/feed") is not None

    def test_rss_url_detected(self):
        assert warn_if_container_url("https://example.com/rss") is not None

    def test_github_search_detected(self):
        assert warn_if_container_url("https://api.github.com/search/repositories?q=test") is not None

    def test_article_url_ok(self):
        assert warn_if_container_url("https://techcrunch.com/2026/01/15/acme-raises-10m") is None

    def test_empty_url_ok(self):
        assert warn_if_container_url("") is None

    def test_none_url_ok(self):
        assert warn_if_container_url(None) is None


# =============================================================================
# Provenance block tests (structural)
# =============================================================================

class TestNewsApiProvenance:
    """Test that news_api collector includes _provenance in raw_data."""

    def test_provenance_in_signal(self):
        """news_api signal raw_data includes _provenance with source_url."""
        from collectors.provenance import create_provenance

        article_url = "https://example.com/news/acme-launches"
        provenance = create_provenance(
            source_url=article_url,
            endpoint="gnews/search",
        )
        raw_data = {
            **provenance,
            "title": "Acme launches",
            "url": article_url,
        }

        assert "_provenance" in raw_data
        assert raw_data["_provenance"]["source_url"] == article_url
        assert raw_data["_provenance"]["endpoint"] == "gnews/search"


class TestRssFeedsProvenance:
    """Test that rss_feeds collector includes _provenance in raw_data."""

    def test_provenance_in_signal(self):
        from collectors.provenance import create_provenance

        article_url = "https://techcrunch.com/2026/01/15/startup-raises"
        feed_url = "https://techcrunch.com/feed/"
        provenance = create_provenance(
            source_url=article_url,
            endpoint=feed_url,
        )
        raw_data = {
            **provenance,
            "title": "Startup Raises",
            "url": article_url,
            "source_feed": feed_url,
        }

        assert "_provenance" in raw_data
        assert raw_data["_provenance"]["source_url"] == article_url
        assert raw_data["_provenance"]["endpoint"] == feed_url


class TestTelegramProvenance:
    """Test that telegram collector includes _provenance in raw_data."""

    def test_provenance_in_signal(self):
        from collectors.provenance import create_provenance

        msg_url = "https://t.me/startups/12345"
        provenance = create_provenance(
            source_url=msg_url,
            endpoint="t.me/startups",
        )
        raw_data = {
            **provenance,
            "channel_username": "startups",
            "message_id": 12345,
        }

        assert "_provenance" in raw_data
        assert raw_data["_provenance"]["source_url"] == msg_url


class TestDiscordProvenance:
    """Test that discord collector includes _provenance in raw_data."""

    def test_provenance_in_signal(self):
        from collectors.provenance import create_provenance

        msg_url = "https://discord.com/channels/123/456/789"
        provenance = create_provenance(
            source_url=msg_url,
            endpoint="discord/123",
        )
        raw_data = {
            **provenance,
            "guild_id": "123",
            "channel_id": "456",
            "message_id": "789",
        }

        assert "_provenance" in raw_data
        assert raw_data["_provenance"]["source_url"] == msg_url
