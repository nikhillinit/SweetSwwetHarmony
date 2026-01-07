"""
Tests for BaseCollector retry and rate limiting integration.

TDD Phase: RED - These tests should FAIL initially.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from verification.verification_gate_v2 import Signal


class TestBaseCollectorRetryConfig:
    """Test BaseCollector accepts retry configuration"""

    def test_base_collector_accepts_retry_config(self):
        """BaseCollector should accept retry_config parameter"""
        from collectors.base import BaseCollector
        from collectors.retry_strategy import RetryConfig

        # Create a concrete implementation for testing
        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        config = RetryConfig(max_retries=5)
        collector = TestCollector(
            collector_name="test",
            retry_config=config,
        )

        assert collector.retry_config is config
        assert collector.retry_config.max_retries == 5

    def test_base_collector_default_retry_config(self):
        """BaseCollector should have default RetryConfig if not provided"""
        from collectors.base import BaseCollector
        from collectors.retry_strategy import RetryConfig

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(collector_name="test")

        assert collector.retry_config is not None
        assert isinstance(collector.retry_config, RetryConfig)
        assert collector.retry_config.max_retries == 3  # Default


class TestBaseCollectorRateLimiter:
    """Test BaseCollector accepts rate limiter configuration"""

    def test_base_collector_accepts_api_name(self):
        """BaseCollector should accept api_name for rate limiting"""
        from collectors.base import BaseCollector

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(
            collector_name="test",
            api_name="github",
        )

        assert collector.api_name == "github"

    def test_base_collector_has_rate_limiter(self):
        """BaseCollector should expose rate_limiter property"""
        from collectors.base import BaseCollector
        from utils.rate_limiter import AsyncRateLimiter

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(
            collector_name="test",
            api_name="github",
        )

        assert collector.rate_limiter is not None
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)
        assert collector.rate_limiter.rate == 5000  # GitHub rate

    def test_base_collector_no_api_name_unlimited(self):
        """BaseCollector without api_name should have unlimited rate limiter"""
        from collectors.base import BaseCollector

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(collector_name="test")

        assert collector.rate_limiter.rate is None  # Unlimited


class TestBaseCollectorFetchWithRetry:
    """Test BaseCollector helper method for fetch with retry"""

    @pytest.mark.asyncio
    async def test_fetch_with_retry_method_exists(self):
        """BaseCollector should have _fetch_with_retry method"""
        from collectors.base import BaseCollector

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(collector_name="test")

        assert hasattr(collector, '_fetch_with_retry')
        assert callable(collector._fetch_with_retry)

    @pytest.mark.asyncio
    async def test_fetch_with_retry_returns_result(self):
        """_fetch_with_retry should return result on success"""
        from collectors.base import BaseCollector

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(collector_name="test")

        async def success_func():
            return {"data": "test"}

        result = await collector._fetch_with_retry(success_func)
        assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_fetch_with_retry_retries_on_error(self):
        """_fetch_with_retry should retry on transient errors"""
        from collectors.base import BaseCollector
        from collectors.retry_strategy import RetryConfig

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)
        collector = TestCollector(
            collector_name="test",
            retry_config=config,
        )

        attempts = []

        async def flaky_func():
            attempts.append(1)
            if len(attempts) < 2:
                raise ConnectionError("Transient")
            return "success"

        result = await collector._fetch_with_retry(flaky_func)
        assert result == "success"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_fetch_with_retry_acquires_rate_limit(self):
        """_fetch_with_retry should acquire rate limiter before calling func"""
        from collectors.base import BaseCollector
        from utils.rate_limiter import AsyncRateLimiter

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(
            collector_name="test",
            api_name="sec_edgar",  # 10/second limit
        )

        # Mock the rate limiter to track calls
        mock_limiter = AsyncMock(spec=AsyncRateLimiter)
        mock_limiter.rate = 10
        collector._rate_limiter = mock_limiter

        async def test_func():
            return "result"

        await collector._fetch_with_retry(test_func)

        # Rate limiter should have been acquired
        mock_limiter.acquire.assert_called_once()


class TestBaseCollectorHttpFetch:
    """Test BaseCollector HTTP fetch convenience method"""

    @pytest.mark.asyncio
    async def test_http_get_method_exists(self):
        """BaseCollector should have _http_get convenience method"""
        from collectors.base import BaseCollector

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(collector_name="test")

        assert hasattr(collector, '_http_get')
        assert callable(collector._http_get)

    @pytest.mark.asyncio
    async def test_http_get_uses_retry(self):
        """_http_get should use retry logic"""
        from collectors.base import BaseCollector
        from collectors.retry_strategy import RetryConfig

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)
        collector = TestCollector(
            collector_name="test",
            retry_config=config,
        )

        attempts = []

        # Mock httpx client
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            async def mock_get(url, **kwargs):
                attempts.append(1)
                if len(attempts) < 2:
                    request = httpx.Request("GET", url)
                    response = httpx.Response(500, request=request)
                    raise httpx.HTTPStatusError("Server Error", request=request, response=response)
                # Return successful response
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"data": "test"}
                mock_response.raise_for_status = MagicMock()
                return mock_response

            mock_client.get = mock_get

            result = await collector._http_get("https://api.example.com/test")

        assert result == {"data": "test"}
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_http_get_acquires_rate_limit(self):
        """_http_get should acquire rate limiter"""
        from collectors.base import BaseCollector
        from utils.rate_limiter import AsyncRateLimiter

        class TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []

        collector = TestCollector(
            collector_name="test",
            api_name="github",
        )

        # Mock rate limiter
        mock_limiter = AsyncMock(spec=AsyncRateLimiter)
        mock_limiter.rate = 5000
        collector._rate_limiter = mock_limiter

        # Mock httpx
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"ok": True}
            mock_response.raise_for_status = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_response)

            await collector._http_get("https://api.github.com/test")

        mock_limiter.acquire.assert_called()


class TestBaseCollectorIntegration:
    """Integration tests for retry + rate limiting in collectors"""

    @pytest.mark.asyncio
    async def test_collector_run_with_retry_on_api_failure(self):
        """Collector should retry when API calls fail transiently"""
        from collectors.base import BaseCollector
        from collectors.retry_strategy import RetryConfig

        attempts = []

        class FlakeyCollector(BaseCollector):
            async def _collect_signals(self):
                # Use the retry helper
                data = await self._fetch_with_retry(self._fetch_data)
                return data

            async def _fetch_data(self):
                attempts.append(1)
                if len(attempts) < 2:
                    raise ConnectionError("Network hiccup")
                return []  # Return empty list of signals

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)
        collector = FlakeyCollector(
            collector_name="flakey",
            retry_config=config,
        )

        result = await collector.run(dry_run=True)

        # Should succeed after retry
        assert result.status.value != "error"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_collector_respects_rate_limit(self):
        """Collector should respect rate limits on rapid API calls"""
        from collectors.base import BaseCollector
        import time

        call_times = []

        class RateLimitedCollector(BaseCollector):
            async def _collect_signals(self):
                # Make 3 rapid API calls
                for _ in range(3):
                    await self._fetch_with_retry(self._make_call)
                return []

            async def _make_call(self):
                call_times.append(time.monotonic())
                return {}

        # Use very slow rate limit to test throttling
        from utils.rate_limiter import AsyncRateLimiter
        collector = RateLimitedCollector(
            collector_name="rate_test",
            api_name="sec_edgar",  # 10/second
        )

        # Override with stricter limit for testing
        collector._rate_limiter = AsyncRateLimiter(rate=2, period=1)

        result = await collector.run(dry_run=True)

        # Third call should have been delayed
        assert len(call_times) == 3
        # Time between first and third call should be >= 0.5s (for 2/sec limit)
        # Adding some tolerance for test flakiness
        time_span = call_times[2] - call_times[0]
        assert time_span >= 0.3  # Allow margin


class TestRetryStatisticsTracking:
    """Test that retry attempts are tracked in collector stats"""

    @pytest.mark.asyncio
    async def test_retry_count_tracked(self):
        """BaseCollector should track retry attempts in result"""
        from collectors.base import BaseCollector
        from collectors.retry_strategy import RetryConfig

        attempts = []

        class RetryingCollector(BaseCollector):
            async def _collect_signals(self):
                await self._fetch_with_retry(self._fetch)
                return []

            async def _fetch(self):
                attempts.append(1)
                if len(attempts) < 3:
                    raise ConnectionError("Fail")
                return {}

        config = RetryConfig(max_retries=5, backoff_base=0.01, jitter=False)
        collector = RetryingCollector(
            collector_name="retry_track",
            retry_config=config,
        )

        result = await collector.run(dry_run=True)

        # Check retry count is accessible
        assert hasattr(collector, '_retry_count')
        assert collector._retry_count == 2  # 2 retries before success
