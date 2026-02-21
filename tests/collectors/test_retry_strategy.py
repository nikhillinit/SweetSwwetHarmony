"""
Tests for centralized retry strategy.

Phase C1: Ensure httpx transport failures are retryable.
"""

import asyncio
from unittest import mock

import httpx
import pytest

from collectors.retry_strategy import (
    RateLimitError,
    RetryConfig,
    is_retryable_error,
    get_retry_after_seconds,
    with_retry,
)


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_default_values(self):
        """Default retry configuration is sensible."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.backoff_base == 2.0
        assert config.backoff_max == 30.0
        assert config.jitter is True

    def test_get_wait_seconds_exponential(self):
        """Wait time follows exponential backoff."""
        config = RetryConfig(jitter=False)

        assert config.get_wait_seconds(0) == 1.0   # 2^0
        assert config.get_wait_seconds(1) == 2.0   # 2^1
        assert config.get_wait_seconds(2) == 4.0   # 2^2
        assert config.get_wait_seconds(3) == 8.0   # 2^3

    def test_get_wait_seconds_capped(self):
        """Wait time is capped at backoff_max."""
        config = RetryConfig(jitter=False, backoff_max=10.0)

        assert config.get_wait_seconds(10) == 10.0  # 2^10 = 1024, capped at 10


class TestIsRetryableError:
    """Tests for is_retryable_error() function."""

    def test_connection_error_is_retryable(self):
        """ConnectionError is retryable."""
        error = ConnectionError("Connection refused")
        assert is_retryable_error(error) is True

    def test_timeout_error_is_retryable(self):
        """TimeoutError is retryable."""
        error = TimeoutError("Timed out")
        assert is_retryable_error(error) is True

    def test_asyncio_timeout_error_is_retryable(self):
        """asyncio.TimeoutError is retryable."""
        error = asyncio.TimeoutError()
        assert is_retryable_error(error) is True

    # Phase C1 tests: httpx transport failures
    def test_httpx_read_timeout_is_retryable(self):
        """C1.1: httpx.ReadTimeout should be retryable."""
        error = httpx.ReadTimeout("Read timed out")
        assert is_retryable_error(error) is True

    def test_httpx_connect_timeout_is_retryable(self):
        """C1.2: httpx.ConnectTimeout should be retryable."""
        error = httpx.ConnectTimeout("Connect timed out")
        assert is_retryable_error(error) is True

    def test_httpx_connect_error_is_retryable(self):
        """C1.3: httpx.ConnectError should be retryable (DNS failure, connection refused)."""
        error = httpx.ConnectError("Connection refused")
        assert is_retryable_error(error) is True

    def test_httpx_write_timeout_is_retryable(self):
        """httpx.WriteTimeout should be retryable."""
        error = httpx.WriteTimeout("Write timed out")
        assert is_retryable_error(error) is True

    def test_httpx_pool_timeout_is_retryable(self):
        """httpx.PoolTimeout should be retryable."""
        error = httpx.PoolTimeout("Pool timed out")
        assert is_retryable_error(error) is True

    def test_httpx_read_error_is_retryable(self):
        """httpx.ReadError should be retryable."""
        error = httpx.ReadError("Read error")
        assert is_retryable_error(error) is True

    def test_httpx_5xx_is_retryable(self):
        """HTTP 5xx errors are retryable."""
        response = httpx.Response(500, request=httpx.Request("GET", "http://test.com"))
        error = httpx.HTTPStatusError(
            "Server Error", request=response.request, response=response
        )
        assert is_retryable_error(error) is True

    def test_httpx_429_is_retryable(self):
        """HTTP 429 rate limit errors are retryable."""
        response = httpx.Response(429, request=httpx.Request("GET", "http://test.com"))
        error = httpx.HTTPStatusError(
            "Too Many Requests", request=response.request, response=response
        )
        assert is_retryable_error(error) is True

    def test_httpx_4xx_not_retryable(self):
        """HTTP 4xx errors (except 429) are NOT retryable."""
        response = httpx.Response(404, request=httpx.Request("GET", "http://test.com"))
        error = httpx.HTTPStatusError(
            "Not Found", request=response.request, response=response
        )
        assert is_retryable_error(error) is False

    def test_httpx_400_not_retryable(self):
        """HTTP 400 Bad Request is NOT retryable."""
        response = httpx.Response(400, request=httpx.Request("GET", "http://test.com"))
        error = httpx.HTTPStatusError(
            "Bad Request", request=response.request, response=response
        )
        assert is_retryable_error(error) is False

    def test_value_error_not_retryable(self):
        """ValueError is NOT retryable."""
        error = ValueError("Invalid value")
        assert is_retryable_error(error) is False


class TestGetRetryAfterSeconds:
    """Tests for get_retry_after_seconds() function."""

    def test_retry_after_header_numeric(self):
        """Numeric Retry-After header is parsed correctly."""
        response = httpx.Response(
            429,
            request=httpx.Request("GET", "http://test.com"),
            headers={"Retry-After": "30"},
        )
        error = httpx.HTTPStatusError(
            "Too Many Requests", request=response.request, response=response
        )
        assert get_retry_after_seconds(error) == 30.0

    def test_retry_after_header_missing(self):
        """Missing Retry-After header returns None."""
        response = httpx.Response(
            429, request=httpx.Request("GET", "http://test.com")
        )
        error = httpx.HTTPStatusError(
            "Too Many Requests", request=response.request, response=response
        )
        assert get_retry_after_seconds(error) is None

    def test_retry_after_non_http_error(self):
        """Non-HTTP errors return None."""
        error = ConnectionError("Connection refused")
        assert get_retry_after_seconds(error) is None


class TestWithRetry:
    """Tests for with_retry() async wrapper."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Successful call doesn't retry."""
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await with_retry(success_func, RetryConfig(max_retries=3))
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self):
        """Retries on ConnectionError."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            return "success"

        config = RetryConfig(max_retries=3, jitter=False, backoff_base=0.01)
        result = await with_retry(flaky_func, config)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """Raises last error when retries exhausted."""
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Connection refused")

        config = RetryConfig(max_retries=2, jitter=False, backoff_base=0.01)
        with pytest.raises(ConnectionError):
            await with_retry(always_fail, config)
        assert call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_no_retry_on_value_error(self):
        """Doesn't retry on non-retryable errors."""
        call_count = 0

        async def bad_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid input")

        config = RetryConfig(max_retries=3)
        with pytest.raises(ValueError):
            await with_retry(bad_func, config)
        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_retry_on_httpx_connect_error(self):
        """C1.3 integration: Retries on httpx.ConnectError."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection refused")
            return "success"

        config = RetryConfig(max_retries=3, jitter=False, backoff_base=0.01)
        result = await with_retry(flaky_func, config)
        assert result == "success"
        assert call_count == 2


class TestRateLimitError:
    """Tests for RateLimitError exception class."""

    def test_rate_limit_error_is_retryable(self):
        """RateLimitError should be classified as retryable."""
        error = RateLimitError(
            "Rate limited", wait_seconds=30, status_code=429, endpoint="/api/test"
        )
        assert is_retryable_error(error) is True

    def test_constructor_guard_rejects_none_wait_non_secondary(self):
        """Constructor must reject wait_seconds=None when is_secondary=False."""
        with pytest.raises(ValueError, match="wait_seconds is required"):
            RateLimitError(
                "Rate limited",
                wait_seconds=None,
                is_secondary=False,
            )

    def test_secondary_allows_none_wait(self):
        """Secondary rate limit may have wait_seconds=None."""
        error = RateLimitError(
            "Secondary rate limit",
            wait_seconds=None,
            is_secondary=True,
            endpoint="/api/test",
        )
        assert error.is_secondary is True
        assert error.wait_seconds is None

    def test_get_retry_after_from_rate_limit_error(self):
        """get_retry_after_seconds should return RateLimitError.wait_seconds."""
        error = RateLimitError(wait_seconds=42.0, status_code=429)
        assert get_retry_after_seconds(error) == 42.0

    def test_get_retry_after_from_secondary_returns_none(self):
        """get_retry_after_seconds on secondary returns None (wait_seconds=None)."""
        error = RateLimitError(wait_seconds=None, is_secondary=True)
        assert get_retry_after_seconds(error) is None

    def test_http_date_retry_after_parsing(self):
        """Retry-After HTTP-date header should be parsed to delta seconds."""
        from datetime import datetime, timezone, timedelta
        from email.utils import format_datetime

        # Create a date 120 seconds in the future
        future = datetime.now(timezone.utc) + timedelta(seconds=120)
        date_str = format_datetime(future)

        response = httpx.Response(
            429,
            request=httpx.Request("GET", "http://test.com"),
            headers={"Retry-After": date_str},
        )
        error = httpx.HTTPStatusError(
            "Too Many Requests", request=response.request, response=response
        )
        result = get_retry_after_seconds(error)
        assert result is not None
        # Should be approximately 120 seconds (allow margin for test execution)
        assert 115 < result < 125


class TestRetryPolicyModes:
    """Tests for RATE_LIMIT_RETRY_POLICY behavior in with_retry."""

    @pytest.mark.asyncio
    async def test_conservative_backoff_wins_when_larger(self, monkeypatch):
        """Conservative: backoff (120s equiv) wins when larger than retry_after (30s)."""
        monkeypatch.setenv("RATE_LIMIT_RETRY_POLICY", "conservative")
        wait_times = []

        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            wait_times.append(seconds)
            # Don't actually sleep
            return

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        call_count = 0

        async def rate_limited_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    wait_seconds=30.0, status_code=429, endpoint="/test"
                )
            return "ok"

        # backoff_base=120 -> attempt 0 = 120^0 = 1... but we want backoff > 30
        # Use backoff_base=120, attempt 0 = min(120^0, max) = 1 — not useful
        # Better: set backoff_base big enough: 2^0=1, so use attempt>0
        # Let's use backoff_base=2.0, backoff_max=120 with jitter=False
        # attempt 0 -> 2^0 = 1 which is < 30. Conservative: max(30, 1) = 30
        # Actually, the plan says "backoff (120s) wins when larger than retry_after (30s)"
        # So we need a config where backoff at attempt 0 > 30.
        # Use backoff_base=120, jitter=False -> attempt 0 = min(120^0, max)=1. Hmm.
        # Let's just use a high backoff_max and a base that gives us 120 at attempt 0.
        # backoff_base=120, jitter=False -> 120^0=1. That's not right.
        # The formula is base^attempt. So we need base^0=1. We can't get 120 at attempt 0.
        # Use attempt 6: 2^6=64... still not 120. 2^7=128.
        # Simpler: just have func fail enough times to reach a high backoff.
        # Actually, let's just set backoff_max=120 and ensure attempt hits max.
        # 2^7 = 128 -> capped at 120. That's 8 attempts. Too many.
        #
        # Simpler approach: just verify max(retry_after, backoff) behavior directly.
        # With backoff_base=2, jitter=False, attempt=0 -> backoff=1.
        # retry_after=30 -> conservative: max(30, 1) = 30. retry_after wins.
        # That tests "retry_after wins when larger than backoff" which is also conservative.
        #
        # For "backoff wins" we need backoff > retry_after.
        # Use backoff_max=200, backoff_base=200, jitter=False -> attempt 0 = min(200^0, 200) = 1. NO.
        # base^attempt: 200^0 = 1. The base is the exponential base, not a multiplier.
        #
        # OK let me re-read the code. get_wait_seconds: wait = min(base ** attempt, max)
        # So attempt 0 = base^0 = 1 always. We need higher attempt.
        # Make it fail twice: attempt 0 -> backoff=1 (retry_after=30 wins)
        #                     attempt 1 -> backoff=2 (retry_after=30 still wins)
        # Not useful. Let me use a LARGE base: backoff_base=50, jitter=False
        # attempt 0 -> 50^0 = 1, attempt 1 -> 50^1 = 50.
        # For the first failure at attempt 0, backoff=1 < 30, so retry_after wins.
        # But we want backoff to win. So let's have it fail at attempt 1:
        # Two failures needed, retry_after=30 on second one.
        # attempt 1 -> 50^1 = 50 > 30 -> conservative: max(30, 50) = 50.
        call_count = 0

        async def rate_limited_func_v2():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RateLimitError(
                    wait_seconds=30.0, status_code=429, endpoint="/test"
                )
            return "ok"

        wait_times.clear()
        call_count = 0
        config = RetryConfig(max_retries=3, backoff_base=50.0, backoff_max=200.0, jitter=False)
        result = await with_retry(rate_limited_func_v2, config)
        assert result == "ok"
        # attempt 0: backoff=1, retry_after=30 -> max(30, 1) = 30
        # attempt 1: backoff=50, retry_after=30 -> max(30, 50) = 50 (backoff wins)
        assert len(wait_times) == 2
        assert wait_times[0] == 30.0  # retry_after wins at attempt 0
        assert wait_times[1] == 50.0  # backoff wins at attempt 1

    @pytest.mark.asyncio
    async def test_conservative_retry_after_wins_when_larger(self, monkeypatch):
        """Conservative: retry_after (90s) wins when larger than backoff (1s)."""
        monkeypatch.setenv("RATE_LIMIT_RETRY_POLICY", "conservative")
        wait_times = []

        async def mock_sleep(seconds):
            wait_times.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    wait_seconds=90.0, status_code=429, endpoint="/test"
                )
            return "ok"

        config = RetryConfig(max_retries=3, backoff_base=2.0, jitter=False)
        result = await with_retry(func, config)
        assert result == "ok"
        # attempt 0: backoff=1, retry_after=90 -> max(90, 1) = 90
        assert wait_times[0] == 90.0

    @pytest.mark.asyncio
    async def test_exact_retry_after_always_wins(self, monkeypatch):
        """Exact: retry_after always wins for non-secondary RateLimitError."""
        monkeypatch.setenv("RATE_LIMIT_RETRY_POLICY", "exact")
        wait_times = []

        async def mock_sleep(seconds):
            wait_times.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RateLimitError(
                    wait_seconds=15.0, status_code=429, endpoint="/test"
                )
            return "ok"

        # backoff at attempt 1 = 50 > 15, but exact mode should use 15
        config = RetryConfig(max_retries=3, backoff_base=50.0, backoff_max=200.0, jitter=False)
        result = await with_retry(func, config)
        assert result == "ok"
        # Both attempts should use retry_after (15) regardless of backoff
        assert wait_times[0] == 15.0
        assert wait_times[1] == 15.0

    @pytest.mark.asyncio
    async def test_secondary_enforces_60s_floor_conservative(self, monkeypatch):
        """Secondary always enforces 60s floor in conservative mode."""
        monkeypatch.setenv("RATE_LIMIT_RETRY_POLICY", "conservative")
        wait_times = []

        async def mock_sleep(seconds):
            wait_times.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    wait_seconds=None, is_secondary=True, endpoint="/test"
                )
            return "ok"

        # backoff at attempt 0 = 1, but secondary floor is 60
        config = RetryConfig(max_retries=3, backoff_base=2.0, jitter=False)
        result = await with_retry(func, config)
        assert result == "ok"
        assert wait_times[0] == 60.0

    @pytest.mark.asyncio
    async def test_secondary_enforces_60s_floor_exact(self, monkeypatch):
        """Secondary always enforces 60s floor in exact mode."""
        monkeypatch.setenv("RATE_LIMIT_RETRY_POLICY", "exact")
        wait_times = []

        async def mock_sleep(seconds):
            wait_times.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    wait_seconds=None, is_secondary=True, endpoint="/test"
                )
            return "ok"

        config = RetryConfig(max_retries=3, backoff_base=2.0, jitter=False)
        result = await with_retry(func, config)
        assert result == "ok"
        assert wait_times[0] == 60.0
