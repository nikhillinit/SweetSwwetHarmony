"""
Tests for centralized retry strategy.

TDD Phase: RED - These tests should FAIL initially.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
import httpx


class TestRetryConfig:
    """Test RetryConfig dataclass"""

    def test_retry_config_exists(self):
        """RetryConfig should be importable"""
        from collectors.retry_strategy import RetryConfig
        assert RetryConfig is not None

    def test_retry_config_defaults(self):
        """RetryConfig should have sensible defaults"""
        from collectors.retry_strategy import RetryConfig
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.backoff_base == 2.0
        assert config.backoff_max == 30.0
        assert config.jitter is True

    def test_retry_config_custom_values(self):
        """RetryConfig should accept custom values"""
        from collectors.retry_strategy import RetryConfig
        config = RetryConfig(max_retries=5, backoff_base=1.5, jitter=False)
        assert config.max_retries == 5
        assert config.backoff_base == 1.5
        assert config.jitter is False

    def test_get_wait_seconds_exponential(self):
        """get_wait_seconds should implement exponential backoff"""
        from collectors.retry_strategy import RetryConfig
        config = RetryConfig(backoff_base=2.0, jitter=False)

        # 2^0, 2^1, 2^2 = 1, 2, 4
        assert config.get_wait_seconds(0) == 1.0
        assert config.get_wait_seconds(1) == 2.0
        assert config.get_wait_seconds(2) == 4.0

    def test_get_wait_seconds_respects_max(self):
        """get_wait_seconds should cap at backoff_max"""
        from collectors.retry_strategy import RetryConfig
        config = RetryConfig(backoff_base=2.0, backoff_max=5.0, jitter=False)

        # 2^10 = 1024, but should cap at 5.0
        assert config.get_wait_seconds(10) == 5.0


class TestWithRetry:
    """Test with_retry async wrapper"""

    @pytest.mark.asyncio
    async def test_with_retry_success_first_try(self):
        """with_retry should return result on first success"""
        from collectors.retry_strategy import with_retry, RetryConfig

        async def success_func():
            return "success"

        config = RetryConfig(max_retries=3)
        result = await with_retry(success_func, config)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_with_retry_retries_on_error(self):
        """with_retry should retry on transient errors"""
        from collectors.retry_strategy import with_retry, RetryConfig

        attempts = []

        async def flaky_func():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Transient failure")
            return "success"

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)
        result = await with_retry(flaky_func, config)

        assert result == "success"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_with_retry_raises_after_max_retries(self):
        """with_retry should raise after exhausting retries"""
        from collectors.retry_strategy import with_retry, RetryConfig

        async def always_fails():
            raise ConnectionError("Permanent failure")

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)

        with pytest.raises(ConnectionError):
            await with_retry(always_fails, config)

    @pytest.mark.asyncio
    async def test_with_retry_custom_error_types(self):
        """with_retry should only retry specified error types"""
        from collectors.retry_strategy import with_retry, RetryConfig

        async def raises_value_error():
            raise ValueError("Not retryable")

        config = RetryConfig(max_retries=3, backoff_base=0.01)

        # ValueError is not in default retryable errors, should raise immediately
        with pytest.raises(ValueError):
            await with_retry(
                raises_value_error,
                config,
                retry_on=(ConnectionError,)  # Only retry ConnectionError
            )

    @pytest.mark.asyncio
    async def test_with_retry_http_status_error(self):
        """with_retry should handle HTTP 5xx errors"""
        from collectors.retry_strategy import with_retry, RetryConfig

        attempts = []

        async def http_error_func():
            attempts.append(1)
            if len(attempts) < 2:
                # Simulate HTTP 500 error
                request = httpx.Request("GET", "https://example.com")
                response = httpx.Response(500, request=request)
                raise httpx.HTTPStatusError("Server Error", request=request, response=response)
            return "recovered"

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)
        result = await with_retry(http_error_func, config)

        assert result == "recovered"
        assert len(attempts) == 2


class TestRetryableErrors:
    """Test error classification for retry logic"""

    def test_is_retryable_connection_error(self):
        """Connection errors should be retryable"""
        from collectors.retry_strategy import is_retryable_error
        assert is_retryable_error(ConnectionError("timeout")) is True

    def test_is_retryable_timeout_error(self):
        """Timeout errors should be retryable"""
        from collectors.retry_strategy import is_retryable_error
        assert is_retryable_error(asyncio.TimeoutError()) is True

    def test_is_retryable_http_5xx(self):
        """HTTP 5xx errors should be retryable"""
        from collectors.retry_strategy import is_retryable_error
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(500, request=request)
        error = httpx.HTTPStatusError("Server Error", request=request, response=response)
        assert is_retryable_error(error) is True

    def test_is_retryable_http_429(self):
        """HTTP 429 rate limit should be retryable"""
        from collectors.retry_strategy import is_retryable_error
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("Rate Limited", request=request, response=response)
        assert is_retryable_error(error) is True

    def test_not_retryable_http_4xx(self):
        """HTTP 4xx client errors (except 429) should NOT be retryable"""
        from collectors.retry_strategy import is_retryable_error
        request = httpx.Request("GET", "https://example.com")

        for status in [400, 401, 403, 404]:
            response = httpx.Response(status, request=request)
            error = httpx.HTTPStatusError("Client Error", request=request, response=response)
            assert is_retryable_error(error) is False, f"Status {status} should not be retryable"

    def test_not_retryable_value_error(self):
        """ValueError should NOT be retryable"""
        from collectors.retry_strategy import is_retryable_error
        assert is_retryable_error(ValueError("bad input")) is False


class TestRetryAfterHeader:
    """Test Retry-After header handling"""

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self):
        """with_retry should respect Retry-After header on 429"""
        from collectors.retry_strategy import get_retry_after_seconds

        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(
            429,
            request=request,
            headers={"Retry-After": "5"}
        )
        error = httpx.HTTPStatusError("Rate Limited", request=request, response=response)

        wait_time = get_retry_after_seconds(error)
        assert wait_time == 5.0

    def test_retry_after_missing(self):
        """get_retry_after_seconds should return None if header missing"""
        from collectors.retry_strategy import get_retry_after_seconds

        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("Rate Limited", request=request, response=response)

        wait_time = get_retry_after_seconds(error)
        assert wait_time is None
