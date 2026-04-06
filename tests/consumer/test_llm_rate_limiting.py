"""Tests for Phase 9: LLM rate limiter and circuit breaker."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from consumer.thesis_filter.llm_classifier import (
    CLASSIFIER_PROMPT_VERSION,
    CLASSIFIER_SYSTEM_PROMPT,
    ClassificationStatus,
    LLMClassifier,
    RateLimiter,
)
from utils.circuit_breaker import CircuitBreaker, CircuitOpenError


class TestRateLimiter:
    """Test RateLimiter for Gemini API."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_within_limits(self):
        """Verify rate limiter allows calls within RPM limit."""
        limiter = RateLimiter(rpm=10, rpd=1000)

        # Should allow first 10 calls without blocking
        for _ in range(10):
            await limiter.acquire()  # Should not raise

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_after_rpm_limit(self):
        """Verify rate limiter blocks calls exceeding RPM."""
        limiter = RateLimiter(rpm=2, rpd=1000)

        # First 2 calls should pass
        await limiter.acquire()
        await limiter.acquire()

        # Third call should sleep (we'll mock the sleep)
        with patch.object(limiter, '_sleep', new_callable=AsyncMock) as mock_sleep:
            await limiter.acquire()
            assert mock_sleep.called

    @pytest.mark.asyncio
    async def test_rate_limiter_daily_quota_exhaustion(self):
        """Verify rate limiter raises error when daily quota exhausted."""
        limiter = RateLimiter(rpm=100, rpd=5)

        # Fill daily quota
        for _ in range(5):
            await limiter.acquire()

        # Next call should raise RuntimeError
        with pytest.raises(RuntimeError, match="Daily quota exhausted"):
            await limiter.acquire()

    def test_rate_limiter_reset(self):
        """Verify rate limiter reset clears state."""
        limiter = RateLimiter(rpm=10, rpd=100)

        # Add some calls
        import asyncio
        asyncio.run(limiter.acquire())
        asyncio.run(limiter.acquire())

        assert len(limiter._minute_calls) == 2
        assert len(limiter._day_calls) == 2

        # Reset
        limiter.reset()

        assert len(limiter._minute_calls) == 0
        assert len(limiter._day_calls) == 0


class TestLLMClassifierCircuitBreaker:
    """Test circuit breaker integration in LLMClassifier."""

    def test_llm_classifier_default_max_tokens(self, monkeypatch):
        """Default max_tokens should support richer structured output."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")
        assert classifier.max_tokens == 800

    def test_llm_classifier_defaults_keep_builtin_prompt_config(self, monkeypatch):
        """Runtime prompt injection should preserve the current defaults when unused."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")
        assert classifier.system_prompt == CLASSIFIER_SYSTEM_PROMPT
        assert classifier.prompt_version == CLASSIFIER_PROMPT_VERSION

    @pytest.mark.asyncio
    async def test_llm_classifier_uses_injected_prompt_and_version(self, monkeypatch):
        """Injected prompt text/version should flow into the request and result metadata."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(
            api_key="test_key",
            system_prompt="SYSTEM OVERRIDE",
            prompt_version="diag-v1",
        )
        mock_response = Mock()
        mock_response.text = """
        {
          "thesis_match": true,
          "thesis_fit_score": 0.91,
          "category": "travel_hospitality",
          "stage_estimate": "seed",
          "confidence": "high",
          "company_name": "DineDesk",
          "rationale": "Consumer reservation app for diners.",
          "key_signals": ["reservation", "diners"],
          "primary_end_user": "individual_consumer",
          "paying_customer": "individual_consumer",
          "sells_to_or_operates_in": "operates_in_industry_for_consumers"
        }
        """
        mock_response.usage_metadata = None

        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await classifier.classify({"title": "Test signal", "source_api": "test"})

        assert "SYSTEM OVERRIDE" in mock_call.await_args.args[0]
        assert result.prompt_version == "diag-v1"

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        """Verify circuit breaker opens after threshold failures."""
        # Create breaker with low threshold for testing
        breaker = CircuitBreaker(name="test_llm", failure_threshold=3, recovery_timeout=60)

        async def failing_func():
            raise Exception("Gemini API error")

        # Record failures
        for _ in range(3):
            try:
                await breaker.call(failing_func)
            except Exception:
                pass

        # Circuit should now be open
        assert breaker.state == "open"

    @pytest.mark.asyncio
    async def test_llm_classifier_skips_when_circuit_open(self, monkeypatch):
        """Verify LLM classifier returns gracefully when circuit is open."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")

        # Create classifier with open circuit
        breaker = CircuitBreaker(name="test_llm", failure_threshold=1, recovery_timeout=60)
        breaker._state = "open"  # Force open
        import time
        breaker._opened_at = time.monotonic()

        rate_limiter = RateLimiter(rpm=100, rpd=1000)
        classifier = LLMClassifier(
            api_key="test_key",
            rate_limiter=rate_limiter,
            circuit_breaker=breaker
        )

        # Should return graceful failure, not crash
        result = await classifier.classify({
            "title": "Test signal",
            "source_api": "test"
        })

        assert result.thesis_match is False
        assert "circuit breaker" in result.rationale.lower()
        assert result.classification_status == ClassificationStatus.ERROR_CIRCUIT_BREAKER.value

    @pytest.mark.asyncio
    async def test_llm_classifier_respects_rate_limits(self, monkeypatch):
        """Verify LLM classifier respects rate limiter."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")

        # Create rate limiter with exhausted daily quota
        rate_limiter = RateLimiter(rpm=100, rpd=0)  # 0 daily quota
        classifier = LLMClassifier(
            api_key="test_key",
            rate_limiter=rate_limiter,
            circuit_breaker=CircuitBreaker()
        )

        # Should return graceful failure for quota exceeded
        result = await classifier.classify({
            "title": "Test signal",
            "source_api": "test"
        })

        assert result.thesis_match is False
        assert "rate limit" in result.rationale.lower()
        assert result.classification_status == ClassificationStatus.ERROR_RATE_LIMIT.value

    @pytest.mark.asyncio
    async def test_llm_classifier_marks_api_errors(self, monkeypatch):
        """Unexpected Gemini API errors should surface as error_api."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")
        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("Gemini API error")
            result = await classifier.classify({"title": "Test signal", "source_api": "test"})

        assert result.thesis_match is False
        assert result.classification_status == ClassificationStatus.ERROR_API.value

    @pytest.mark.asyncio
    async def test_llm_classifier_marks_parse_errors(self, monkeypatch):
        """Malformed JSON responses should surface as error_parse."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")
        mock_response = Mock()
        mock_response.text = "not-json"
        mock_response.usage_metadata = None
        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await classifier.classify({"title": "Test signal", "source_api": "test"})

        assert result.thesis_match is False
        assert result.classification_status == ClassificationStatus.ERROR_PARSE.value

    @pytest.mark.asyncio
    async def test_llm_classifier_parses_minimal_step3_fields(self, monkeypatch):
        """Successful classifications should surface the narrowed Step 3 decomposition fields."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")

        mock_response = Mock()
        mock_response.text = """
        {
          "thesis_match": true,
          "thesis_fit_score": 0.91,
          "category": "travel_hospitality",
          "stage_estimate": "seed",
          "confidence": "high",
          "company_name": "DineDesk",
          "rationale": "Consumer reservation app for diners.",
          "key_signals": ["reservation", "diners"],
          "primary_end_user": "individual_consumer",
          "paying_customer": "individual_consumer",
          "sells_to_or_operates_in": "operates_in_industry_for_consumers"
        }
        """
        mock_response.usage_metadata = None

        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await classifier.classify({"title": "Test signal", "source_api": "test"})

        assert result.classification_status == ClassificationStatus.SUCCESS.value
        assert result.primary_end_user == "individual_consumer"
        assert result.paying_customer == "individual_consumer"
        assert result.sells_to_or_operates_in == "operates_in_industry_for_consumers"

    @pytest.mark.asyncio
    async def test_llm_classifier_normalizes_invalid_step3_field_values(self, monkeypatch):
        """Unexpected structured-field values should fall back to 'unclear'."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")

        mock_response = Mock()
        mock_response.text = """
        {
          "thesis_match": true,
          "thesis_fit_score": 0.91,
          "category": "travel_hospitality",
          "stage_estimate": "seed",
          "confidence": "high",
          "company_name": "DineDesk",
          "rationale": "Consumer reservation app for diners.",
          "key_signals": ["reservation", "diners"],
          "primary_end_user": "vip_consumer",
          "paying_customer": 7,
          "sells_to_or_operates_in": "???"
        }
        """
        mock_response.usage_metadata = None

        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await classifier.classify({"title": "Test signal", "source_api": "test"})

        assert result.primary_end_user == "unclear"
        assert result.paying_customer == "unclear"
        assert result.sells_to_or_operates_in == "unclear"

    def test_circuit_breaker_stats_exposed(self, monkeypatch):
        """Verify circuit breaker stats are accessible."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")

        classifier = LLMClassifier(api_key="test_key")

        stats = classifier.circuit_breaker_stats

        assert "name" in stats
        assert "state" in stats
        assert "failure_count" in stats
        assert stats["name"] == "gemini_llm"


class TestThesisFilterErrorHandling:
    """Test ThesisFilter's error handling and fallback to keyword-only classification."""

    @pytest.fixture
    def thesis_filter(self):
        """Create ThesisFilter instance for testing."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig
        config = ThesisFilterConfig()
        return ThesisFilter(config=config, signal_store=None)

    @pytest.mark.asyncio
    async def test_rate_limit_fallback_to_keyword_only(self, thesis_filter):
        """
        When LLM classifier raises rate limit error, ThesisFilter should:
        - Fall back to keyword-only classification
        - Set llm_skipped=True
        - Return valid routing based on keyword score alone
        """
        # Mock keyword matcher to return high score
        with patch.object(thesis_filter._keyword_matcher, 'score') as mock_score:
            mock_keyword_result = Mock()
            mock_keyword_result.score = 0.8
            mock_keyword_result.thesis.value = "Consumer Health Tech"
            mock_keyword_result.matched_keywords = ["health", "fitness"]
            mock_keyword_result.negative_keywords = []
            mock_keyword_result.intent_phrases_matched = []
            mock_keyword_result.domain_match = None
            mock_keyword_result.domain_blacklisted = False
            mock_keyword_result.trace = None
            mock_score.return_value = mock_keyword_result

            # Mock LLM classifier to return rate limit error result
            mock_llm = AsyncMock()
            mock_llm_result = Mock()
            mock_llm_result.thesis_match = False
            mock_llm_result.thesis_fit_score = None
            mock_llm_result.category = None
            mock_llm_result.rationale = "Rate limit exceeded: 1500 requests per day"
            mock_llm.classify.return_value = mock_llm_result
            thesis_filter._llm_classifier = mock_llm

            # Classify text (skip_llm=False to trigger LLM call)
            from utils.thesis_filter import RoutingDecision
            result = await thesis_filter.classify(
                text="AI-powered fitness tracking app for consumers",
                company_name="FitAI",
                skip_llm=False
            )

            # Assertions
            assert result.keyword_score == 0.8, "Should preserve keyword score"
            assert result.routing == RoutingDecision.QUALIFIED, "Should route based on keyword score"
            assert result.keyword_category == "Consumer Health Tech"

    @pytest.mark.asyncio
    async def test_llm_exception_fallback(self, thesis_filter):
        """
        When LLM classifier raises exception, should gracefully fall back to keyword-only.
        """
        # Mock keyword matcher
        with patch.object(thesis_filter._keyword_matcher, 'score') as mock_score:
            mock_keyword_result = Mock()
            mock_keyword_result.score = 0.6
            mock_keyword_result.thesis.value = "Consumer CPG"
            mock_keyword_result.matched_keywords = ["meal", "food"]
            mock_keyword_result.negative_keywords = []
            mock_keyword_result.intent_phrases_matched = []
            mock_keyword_result.domain_match = None
            mock_keyword_result.domain_blacklisted = False
            mock_keyword_result.trace = None
            mock_score.return_value = mock_keyword_result

            # Mock LLM to raise exception
            mock_llm = AsyncMock()
            mock_llm.classify.side_effect = Exception("Gemini API error")
            thesis_filter._llm_classifier = mock_llm

            from utils.thesis_filter import RoutingDecision
            result = await thesis_filter.classify(
                text="Meal kit delivery service",
                company_name="MealBox",
                skip_llm=False
            )

            # Should fall back to keyword-only
            assert result.keyword_score == 0.6
            assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_low_keyword_score_with_llm_failure_holds(self, thesis_filter):
        """
        When LLM fails AND keyword score is low, should route to HELD.
        """
        # Mock keyword matcher with LOW score
        with patch.object(thesis_filter._keyword_matcher, 'score') as mock_score:
            mock_keyword_result = Mock()
            mock_keyword_result.score = 0.25  # Below hold_threshold (0.3)
            mock_keyword_result.thesis.value = "Unknown"
            mock_keyword_result.matched_keywords = []
            mock_keyword_result.negative_keywords = []
            mock_keyword_result.intent_phrases_matched = []
            mock_keyword_result.domain_match = None
            mock_keyword_result.domain_blacklisted = False
            mock_keyword_result.trace = None
            mock_score.return_value = mock_keyword_result

            # Mock LLM to raise exception
            mock_llm = AsyncMock()
            mock_llm.classify.side_effect = Exception("LLM unavailable")
            thesis_filter._llm_classifier = mock_llm

            from utils.thesis_filter import RoutingDecision
            result = await thesis_filter.classify(
                text="Some ambiguous business description",
                company_name="BusinessCo",
                skip_llm=False
            )

            # Should route to HELD due to low keyword score
            assert result.routing == RoutingDecision.HELD
            assert result.keyword_score == 0.25
