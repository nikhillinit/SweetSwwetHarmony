"""
Tests for centralized retry strategy.

Phase C1: Ensure httpx transport failures are retryable.
"""

import asyncio
from unittest import mock

import httpx
import pytest

from collectors.retry_strategy import (
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
