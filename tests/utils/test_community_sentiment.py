"""
Tests for Community Sentiment Scoring Module

TDD: Write failing tests first, then implement.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from utils.community_sentiment import (
    CommunitySentiment,
    SentimentResult,
    SentimentConfig,
    CommunitySentimentAnalyzer,
    HeuristicSentimentAnalyzer,
    OllamaSentimentAnalyzer,
)


# =============================================================================
# SENTIMENT RESULT TESTS
# =============================================================================

class TestSentimentResult:
    """Tests for SentimentResult dataclass."""

    def test_positive_sentiment(self):
        """Positive sentiment has correct properties."""
        result = SentimentResult(
            score=0.8,
            label="positive",
            confidence=0.9,
            method="heuristic"
        )
        assert result.score == 0.8
        assert result.label == "positive"
        assert result.is_positive
        assert not result.is_negative
        assert not result.is_neutral

    def test_negative_sentiment(self):
        """Negative sentiment has correct properties."""
        result = SentimentResult(
            score=-0.6,
            label="negative",
            confidence=0.85,
            method="ollama"
        )
        assert result.score == -0.6
        assert result.label == "negative"
        assert not result.is_positive
        assert result.is_negative
        assert not result.is_neutral

    def test_neutral_sentiment(self):
        """Neutral sentiment has correct properties."""
        result = SentimentResult(
            score=0.1,
            label="neutral",
            confidence=0.7,
            method="heuristic"
        )
        assert result.score == 0.1
        assert result.label == "neutral"
        assert not result.is_positive
        assert not result.is_negative
        assert result.is_neutral

    def test_to_dict(self):
        """to_dict serializes correctly."""
        result = SentimentResult(
            score=0.5,
            label="positive",
            confidence=0.8,
            method="ollama",
            keywords_found=["love", "amazing"]
        )
        data = result.to_dict()
        assert data["score"] == 0.5
        assert data["label"] == "positive"
        assert data["confidence"] == 0.8
        assert data["method"] == "ollama"
        assert "love" in data["keywords_found"]


# =============================================================================
# SENTIMENT CONFIG TESTS
# =============================================================================

class TestSentimentConfig:
    """Tests for SentimentConfig dataclass."""

    def test_default_config(self):
        """Default config has expected values."""
        config = SentimentConfig()
        assert config.ollama_url == "http://localhost:11434"
        assert config.model_name == "gemma:2b"
        assert config.timeout_seconds == 30
        assert config.use_ollama_if_available is True
        assert len(config.positive_keywords) > 0
        assert len(config.negative_keywords) > 0

    def test_custom_config(self):
        """Custom config overrides defaults."""
        config = SentimentConfig(
            ollama_url="http://custom:8080",
            model_name="custom-model",
            timeout_seconds=60,
            use_ollama_if_available=False
        )
        assert config.ollama_url == "http://custom:8080"
        assert config.model_name == "custom-model"
        assert config.timeout_seconds == 60
        assert config.use_ollama_if_available is False


# =============================================================================
# HEURISTIC ANALYZER TESTS
# =============================================================================

class TestHeuristicSentimentAnalyzer:
    """Tests for heuristic (keyword-based) sentiment analyzer."""

    def test_positive_text(self):
        """Strongly positive text returns positive sentiment."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("This product is amazing! I love it! Best ever! Excellent!")
        assert result.label == "positive"
        assert result.score >= 0.3
        assert result.method == "heuristic"
        assert len(result.keywords_found) > 0

    def test_negative_text(self):
        """Strongly negative text returns negative sentiment."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("This is terrible. I hate it. Total scam!")
        assert result.label == "negative"
        assert result.score < -0.3
        assert result.method == "heuristic"

    def test_neutral_text(self):
        """Neutral text returns neutral sentiment."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("The company announced a new product today.")
        assert result.label == "neutral"
        assert -0.3 <= result.score <= 0.3
        assert result.method == "heuristic"

    def test_mixed_sentiment(self):
        """Mixed sentiment gets balanced score."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("I love the concept but hate the execution")
        # Should be closer to neutral due to mixed signals
        assert -0.5 <= result.score <= 0.5
        assert result.method == "heuristic"

    def test_empty_text(self):
        """Empty text returns neutral sentiment."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("")
        assert result.label == "neutral"
        assert result.score == 0.0

    def test_case_insensitive(self):
        """Analysis is case insensitive."""
        analyzer = HeuristicSentimentAnalyzer()
        result1 = analyzer.analyze("AMAZING product!")
        result2 = analyzer.analyze("amazing product!")
        assert result1.score == result2.score

    def test_startup_specific_keywords(self):
        """Startup-specific positive keywords detected."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("This startup just raised funding and is growing fast!")
        assert result.score > 0
        # Funding and growth are positive signals for startups

    def test_scam_keywords(self):
        """Scam/fraud keywords heavily penalized."""
        analyzer = HeuristicSentimentAnalyzer()
        result = analyzer.analyze("This looks like a scam. Fraudulent company.")
        assert result.label == "negative"
        assert result.score < -0.5

    def test_batch_analysis(self):
        """Batch analysis works correctly."""
        analyzer = HeuristicSentimentAnalyzer()
        texts = [
            "Great amazing excellent product!",
            "Terrible awful horrible service",
            "The company announced a meeting today"
        ]
        results = analyzer.analyze_batch(texts)
        assert len(results) == 3
        assert results[0].label == "positive"
        assert results[1].label == "negative"
        assert results[2].label == "neutral"

    def test_custom_keywords(self):
        """Custom positive/negative keywords work."""
        config = SentimentConfig(
            positive_keywords=["unicorn", "moonshot"],
            negative_keywords=["pivot", "layoffs"]
        )
        analyzer = HeuristicSentimentAnalyzer(config)

        result1 = analyzer.analyze("This could be a unicorn!")
        assert result1.score > 0

        result2 = analyzer.analyze("Company announced layoffs")
        assert result2.score < 0


# =============================================================================
# OLLAMA ANALYZER TESTS
# =============================================================================

class TestOllamaSentimentAnalyzer:
    """Tests for Ollama-based sentiment analyzer."""

    @pytest.mark.asyncio
    async def test_ollama_available(self):
        """Test when Ollama is available and returns valid response."""
        analyzer = OllamaSentimentAnalyzer()
        # Force availability check to True
        analyzer._available = True

        # Mock httpx at module level where it's imported
        with patch.dict("sys.modules", {"httpx": MagicMock()}):
            import sys
            mock_httpx = sys.modules["httpx"]

            # Mock successful response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "response": "POSITIVE 0.8"
            }

            # Create mock async context manager
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_httpx.AsyncClient.return_value.__aenter__.return_value = mock_client_instance

            result = await analyzer.analyze("Great product!")
            # When Ollama is available and returns valid response, method should be "ollama"
            assert result.method == "ollama"
            assert result.score == 0.8
            assert result.label == "positive"

    @pytest.mark.asyncio
    async def test_ollama_unavailable_fallback(self):
        """Falls back to heuristic when Ollama unavailable."""
        analyzer = OllamaSentimentAnalyzer()

        with patch("httpx.AsyncClient") as mock_client:
            # Mock connection error
            mock_client.return_value.__aenter__.return_value.post.side_effect = Exception("Connection refused")

            result = await analyzer.analyze("Great product!")
            # Should fall back to heuristic
            assert result.method == "heuristic"
            assert result.score > 0

    @pytest.mark.asyncio
    async def test_ollama_timeout_fallback(self):
        """Falls back to heuristic on timeout."""
        import asyncio
        analyzer = OllamaSentimentAnalyzer(
            config=SentimentConfig(timeout_seconds=1)
        )

        with patch("httpx.AsyncClient") as mock_client:
            # Mock timeout
            mock_client.return_value.__aenter__.return_value.post.side_effect = asyncio.TimeoutError()

            result = await analyzer.analyze("Great product!")
            assert result.method == "heuristic"

    @pytest.mark.asyncio
    async def test_check_ollama_available(self):
        """check_available correctly detects Ollama status."""
        analyzer = OllamaSentimentAnalyzer()

        with patch("httpx.AsyncClient") as mock_client:
            # Mock Ollama responding
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            available = await analyzer.check_available()
            assert available is True

    @pytest.mark.asyncio
    async def test_batch_analysis_ollama(self):
        """Batch analysis uses Ollama efficiently."""
        analyzer = OllamaSentimentAnalyzer()
        texts = ["Great!", "Terrible!", "Neutral text"]

        with patch.object(analyzer, "analyze") as mock_analyze:
            mock_analyze.return_value = SentimentResult(
                score=0.5, label="positive", confidence=0.8, method="ollama"
            )

            results = await analyzer.analyze_batch(texts)
            assert len(results) == 3
            assert mock_analyze.call_count == 3


# =============================================================================
# MAIN ANALYZER TESTS
# =============================================================================

class TestCommunitySentimentAnalyzer:
    """Tests for main CommunitySentimentAnalyzer orchestrator."""

    @pytest.mark.asyncio
    async def test_auto_selects_ollama_when_available(self):
        """Uses Ollama when available and configured."""
        config = SentimentConfig(use_ollama_if_available=True)
        analyzer = CommunitySentimentAnalyzer(config)

        with patch.object(analyzer._ollama, "check_available", return_value=True):
            with patch.object(analyzer._ollama, "analyze") as mock_ollama:
                mock_ollama.return_value = SentimentResult(
                    score=0.7, label="positive", confidence=0.9, method="ollama"
                )
                result = await analyzer.analyze("Great product!")
                assert result.method == "ollama"
                mock_ollama.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_heuristic_when_ollama_disabled(self):
        """Uses heuristic when Ollama is disabled in config."""
        config = SentimentConfig(use_ollama_if_available=False)
        analyzer = CommunitySentimentAnalyzer(config)

        result = await analyzer.analyze("Great product!")
        assert result.method == "heuristic"

    @pytest.mark.asyncio
    async def test_uses_heuristic_when_ollama_unavailable(self):
        """Falls back to heuristic when Ollama unavailable."""
        config = SentimentConfig(use_ollama_if_available=True)
        analyzer = CommunitySentimentAnalyzer(config)

        with patch.object(analyzer._ollama, "check_available", return_value=False):
            result = await analyzer.analyze("Great product!")
            assert result.method == "heuristic"

    def test_sync_analyze(self):
        """Synchronous analyze method works."""
        config = SentimentConfig(use_ollama_if_available=False)
        analyzer = CommunitySentimentAnalyzer(config)

        result = analyzer.analyze_sync("Amazing excellent brilliant startup!")
        assert result.label == "positive"
        assert result.method == "heuristic"

    def test_batch_sync_analyze(self):
        """Synchronous batch analysis works."""
        config = SentimentConfig(use_ollama_if_available=False)
        analyzer = CommunitySentimentAnalyzer(config)

        texts = ["Great!", "Bad!", "Okay"]
        results = analyzer.analyze_batch_sync(texts)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_confidence_boost_calculation(self):
        """Calculates confidence boost for verification gate."""
        config = SentimentConfig(use_ollama_if_available=False)
        analyzer = CommunitySentimentAnalyzer(config)

        # Strongly positive sentiment (need many keywords to get score > 0.3)
        result = await analyzer.analyze("Everyone loves this! Amazing product! Best ever! Excellent wonderful brilliant!")
        boost = analyzer.calculate_confidence_boost(result)
        assert boost > 0  # Positive sentiment = positive boost
        assert boost <= 0.10  # Max boost is 0.10

    @pytest.mark.asyncio
    async def test_negative_sentiment_penalty(self):
        """Negative sentiment results in confidence penalty."""
        config = SentimentConfig(use_ollama_if_available=False)
        analyzer = CommunitySentimentAnalyzer(config)

        result = await analyzer.analyze("This is a scam! Terrible fraud!")
        boost = analyzer.calculate_confidence_boost(result)
        assert boost < 0  # Negative sentiment = penalty
        assert boost >= -0.15  # Max penalty is -0.15


# =============================================================================
# COMMUNITY SENTIMENT DATACLASS TESTS
# =============================================================================

class TestCommunitySentiment:
    """Tests for CommunitySentiment aggregate dataclass."""

    def test_creation(self):
        """CommunitySentiment creates correctly."""
        sentiment = CommunitySentiment(
            source="reddit",
            mention_count=10,
            unique_authors=5,
            avg_sentiment_score=0.6,
            sentiment_label="positive",
            positive_ratio=0.7,
            negative_ratio=0.1,
            neutral_ratio=0.2,
            confidence_boost=0.05
        )
        assert sentiment.source == "reddit"
        assert sentiment.mention_count == 10
        assert sentiment.unique_authors == 5

    def test_from_results(self):
        """Creates CommunitySentiment from list of SentimentResults."""
        results = [
            SentimentResult(score=0.8, label="positive", confidence=0.9, method="heuristic"),
            SentimentResult(score=0.5, label="positive", confidence=0.8, method="heuristic"),
            SentimentResult(score=-0.3, label="negative", confidence=0.7, method="heuristic"),
            SentimentResult(score=0.1, label="neutral", confidence=0.6, method="heuristic"),
        ]

        sentiment = CommunitySentiment.from_results(
            results=results,
            source="telegram",
            unique_authors=3
        )

        assert sentiment.source == "telegram"
        assert sentiment.mention_count == 4
        assert sentiment.unique_authors == 3
        assert sentiment.positive_ratio == 0.5  # 2/4
        assert sentiment.negative_ratio == 0.25  # 1/4
        assert sentiment.neutral_ratio == 0.25  # 1/4

    def test_to_dict(self):
        """to_dict serializes correctly."""
        sentiment = CommunitySentiment(
            source="discord",
            mention_count=5,
            unique_authors=3,
            avg_sentiment_score=0.4,
            sentiment_label="positive",
            positive_ratio=0.6,
            negative_ratio=0.2,
            neutral_ratio=0.2,
            confidence_boost=0.03
        )

        data = sentiment.to_dict()
        assert data["source"] == "discord"
        assert data["mention_count"] == 5
        assert data["confidence_boost"] == 0.03

    def test_empty_results(self):
        """Handles empty results gracefully."""
        sentiment = CommunitySentiment.from_results(
            results=[],
            source="telegram",
            unique_authors=0
        )

        assert sentiment.mention_count == 0
        assert sentiment.avg_sentiment_score == 0.0
        assert sentiment.sentiment_label == "neutral"
