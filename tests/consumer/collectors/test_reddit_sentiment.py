"""
Tests for Reddit Collector Sentiment Integration

Tests the sentiment analysis features added to the Reddit collector.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from consumer.collectors.reddit_collector import RedditCollector
from consumer.collectors.base import Signal


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def collector():
    """Create a Reddit collector with sentiment enabled."""
    return RedditCollector(store=None, enable_sentiment=True)


@pytest.fixture
def collector_no_sentiment():
    """Create a Reddit collector with sentiment disabled."""
    return RedditCollector(store=None, enable_sentiment=False)


# =============================================================================
# INITIALIZATION TESTS
# =============================================================================

class TestRedditCollectorInit:
    """Tests for collector initialization."""

    def test_sentiment_enabled_by_default(self):
        """Sentiment analysis is enabled by default."""
        collector = RedditCollector()
        assert collector.enable_sentiment is True

    def test_sentiment_can_be_disabled(self):
        """Sentiment can be disabled via parameter."""
        collector = RedditCollector(enable_sentiment=False)
        assert collector.enable_sentiment is False

    def test_sentiment_analyzer_created_when_enabled(self, collector):
        """Sentiment analyzer is created when enabled."""
        assert collector._sentiment_analyzer is not None

    def test_no_sentiment_analyzer_when_disabled(self, collector_no_sentiment):
        """No sentiment analyzer when disabled."""
        assert collector_no_sentiment._sentiment_analyzer is None


# =============================================================================
# SENTIMENT ANALYSIS TESTS
# =============================================================================

class TestTitleSentimentAnalysis:
    """Tests for title sentiment analysis."""

    def test_positive_title_sentiment(self, collector):
        """Positive titles return positive sentiment."""
        result = collector._analyze_title_sentiment(
            "Just launched my amazing new fitness app! Love the feedback!"
        )
        assert result is not None
        assert result["sentiment_label"] == "positive"
        assert result["sentiment_score"] > 0

    def test_negative_title_sentiment(self, collector):
        """Negative titles return negative sentiment."""
        result = collector._analyze_title_sentiment(
            "This startup is terrible. Total scam, stay away!"
        )
        assert result is not None
        assert result["sentiment_label"] == "negative"
        assert result["sentiment_score"] < 0

    def test_neutral_title_sentiment(self, collector):
        """Neutral titles return neutral sentiment."""
        result = collector._analyze_title_sentiment(
            "Company announces new feature update"
        )
        assert result is not None
        assert result["sentiment_label"] == "neutral"

    def test_sentiment_returns_keywords(self, collector):
        """Sentiment analysis returns detected keywords."""
        result = collector._analyze_title_sentiment(
            "Amazing product! I love it!"
        )
        assert result is not None
        assert "sentiment_keywords" in result
        assert len(result["sentiment_keywords"]) > 0

    def test_sentiment_method_is_heuristic(self, collector):
        """Default method is heuristic (no Ollama dependency)."""
        result = collector._analyze_title_sentiment("Test title")
        assert result is not None
        assert result["sentiment_method"] == "heuristic"

    def test_sentiment_disabled_returns_none(self, collector_no_sentiment):
        """Returns None when sentiment is disabled."""
        result = collector_no_sentiment._analyze_title_sentiment("Great product!")
        assert result is None


# =============================================================================
# POST TO SIGNAL TESTS
# =============================================================================

class TestPostToSignal:
    """Tests for _post_to_signal with sentiment data."""

    def test_signal_includes_sentiment_data(self, collector):
        """Converted signal includes sentiment in raw_metadata."""
        post = {
            "id": "test123",
            "title": "Just launched my amazing fitness app!",
            "url": "https://example.com/app",
            "author": "founder",
            "score": 50,
            "created_utc": 1700000000,
            "permalink": "/r/startups/test123",
            "num_comments": 10,
            "is_self": True,
        }

        signal = collector._post_to_signal("startups", post)

        assert signal.raw_metadata is not None
        assert "sentiment_score" in signal.raw_metadata
        assert "sentiment_label" in signal.raw_metadata
        assert "sentiment_method" in signal.raw_metadata
        assert "sentiment_keywords" in signal.raw_metadata

    def test_signal_without_sentiment_when_disabled(self, collector_no_sentiment):
        """Signal has no sentiment data when disabled."""
        post = {
            "id": "test456",
            "title": "Great product announcement!",
            "url": "https://example.com/product",
            "author": "user",
            "score": 100,
            "created_utc": 1700000000,
            "permalink": "/r/entrepreneur/test456",
            "num_comments": 5,
            "is_self": False,
        }

        signal = collector_no_sentiment._post_to_signal("entrepreneur", post)

        assert "sentiment_score" not in signal.raw_metadata
        assert "sentiment_label" not in signal.raw_metadata

    def test_signal_preserves_standard_metadata(self, collector):
        """Standard metadata is preserved alongside sentiment."""
        post = {
            "id": "abc789",
            "title": "Launched new meal delivery service",
            "url": "",
            "author": "ceo",
            "score": 200,
            "created_utc": 1700000000,
            "permalink": "/r/startups/abc789",
            "num_comments": 25,
            "is_self": True,
        }

        signal = collector._post_to_signal("startups", post)

        # Standard fields preserved
        assert signal.raw_metadata["subreddit"] == "startups"
        assert signal.raw_metadata["author"] == "ceo"
        assert signal.raw_metadata["score"] == 200
        assert signal.raw_metadata["num_comments"] == 25

    def test_signal_no_selftext_stored(self, collector):
        """Selftext (body) is NOT stored - compliance requirement."""
        post = {
            "id": "def000",
            "title": "Check out my app!",
            "url": "",
            "author": "builder",
            "score": 10,
            "created_utc": 1700000000,
            "permalink": "/r/SideProject/def000",
            "num_comments": 2,
            "is_self": True,
            "selftext": "This is the body content that should NOT be stored.",
        }

        signal = collector._post_to_signal("SideProject", post)

        # Selftext should NOT appear anywhere
        assert "selftext" not in signal.raw_metadata
        assert signal.source_context is not None
        assert "body content" not in signal.source_context


# =============================================================================
# SENTIMENT SUMMARY TESTS
# =============================================================================

class TestSentimentSummary:
    """Tests for get_sentiment_summary method."""

    def test_summary_with_mixed_sentiment(self, collector):
        """Summary aggregates mixed sentiment correctly."""
        signals = [
            Signal(
                source_api="reddit",
                source_id="1",
                raw_metadata={"sentiment_label": "positive", "sentiment_score": 0.8}
            ),
            Signal(
                source_api="reddit",
                source_id="2",
                raw_metadata={"sentiment_label": "positive", "sentiment_score": 0.6}
            ),
            Signal(
                source_api="reddit",
                source_id="3",
                raw_metadata={"sentiment_label": "negative", "sentiment_score": -0.5}
            ),
            Signal(
                source_api="reddit",
                source_id="4",
                raw_metadata={"sentiment_label": "neutral", "sentiment_score": 0.1}
            ),
        ]

        summary = collector.get_sentiment_summary(signals)

        assert summary["total"] == 4
        assert summary["with_sentiment"] == 4
        assert summary["positive"] == 2
        assert summary["negative"] == 1
        assert summary["neutral"] == 1
        # Avg: (0.8 + 0.6 - 0.5 + 0.1) / 4 = 0.25
        assert abs(summary["avg_score"] - 0.25) < 0.01

    def test_summary_empty_signals(self, collector):
        """Summary handles empty signal list."""
        summary = collector.get_sentiment_summary([])

        assert summary["total"] == 0
        assert summary["with_sentiment"] == 0
        assert summary["avg_score"] == 0.0

    def test_summary_signals_without_sentiment(self, collector):
        """Summary handles signals without sentiment data."""
        signals = [
            Signal(
                source_api="reddit",
                source_id="1",
                raw_metadata={"subreddit": "startups"}  # No sentiment
            ),
            Signal(
                source_api="reddit",
                source_id="2",
                raw_metadata={"sentiment_label": "positive", "sentiment_score": 0.7}
            ),
        ]

        summary = collector.get_sentiment_summary(signals)

        assert summary["total"] == 2
        assert summary["with_sentiment"] == 1
        assert summary["positive"] == 1


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestRedditCollectorIntegration:
    """Integration tests for the collector with sentiment."""

    @pytest.mark.asyncio
    async def test_collect_with_sentiment_analysis(self, collector):
        """Full collection includes sentiment analysis."""
        mock_response_data = {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "int_test1",
                            "title": "Just launched an amazing wellness app! Love the feedback!",
                            "url": "https://myapp.com",
                            "author": "founder1",
                            "score": 100,
                            "created_utc": 1700000000,
                            "permalink": "/r/startups/int_test1",
                            "num_comments": 20,
                            "is_self": True,
                            "selftext": "wellness fitness app",
                        }
                    }
                ]
            }
        }

        with patch.object(collector, '_session') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_session.get.return_value.__aenter__.return_value = mock_response

            signals = await collector._collect_from_subreddit("startups")

            # Should have collected the signal with sentiment
            assert len(signals) == 1
            assert "sentiment_label" in signals[0].raw_metadata
            assert signals[0].raw_metadata["sentiment_label"] == "positive"
