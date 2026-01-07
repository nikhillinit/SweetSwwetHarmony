"""
Tests for per-API rate limiter.

TDD Phase: RED - These tests should FAIL initially.
"""

import pytest
import asyncio
import time


class TestAsyncRateLimiter:
    """Test AsyncRateLimiter class"""

    def test_rate_limiter_exists(self):
        """AsyncRateLimiter should be importable"""
        from utils.rate_limiter import AsyncRateLimiter
        assert AsyncRateLimiter is not None

    def test_rate_limiter_init(self):
        """AsyncRateLimiter should accept rate and period"""
        from utils.rate_limiter import AsyncRateLimiter
        limiter = AsyncRateLimiter(rate=10, period=1)
        assert limiter.rate == 10
        assert limiter.period == 1

    def test_rate_limiter_unlimited(self):
        """AsyncRateLimiter with rate=None should be unlimited"""
        from utils.rate_limiter import AsyncRateLimiter
        limiter = AsyncRateLimiter(rate=None, period=1)
        assert limiter.rate is None

    @pytest.mark.asyncio
    async def test_acquire_returns_quickly_under_limit(self):
        """acquire() should return immediately when under rate limit"""
        from utils.rate_limiter import AsyncRateLimiter
        limiter = AsyncRateLimiter(rate=100, period=1)

        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start

        # Should complete in well under 1 second
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_acquire_throttles_when_exceeded(self):
        """acquire() should throttle when rate limit exceeded"""
        from utils.rate_limiter import AsyncRateLimiter
        # 2 requests per second
        limiter = AsyncRateLimiter(rate=2, period=1)

        start = time.monotonic()
        # Request 3 times - should take at least 0.5 seconds for the 3rd
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.monotonic() - start

        # Third request should wait ~0.5 seconds
        assert elapsed >= 0.3  # Allow some margin

    @pytest.mark.asyncio
    async def test_unlimited_never_throttles(self):
        """Unlimited limiter should never throttle"""
        from utils.rate_limiter import AsyncRateLimiter
        limiter = AsyncRateLimiter(rate=None, period=1)

        start = time.monotonic()
        for _ in range(100):
            await limiter.acquire()
        elapsed = time.monotonic() - start

        # Should be nearly instant
        assert elapsed < 0.1


class TestRateLimiterPool:
    """Test RateLimiterPool factory"""

    def test_pool_exists(self):
        """RateLimiterPool should be importable"""
        from utils.rate_limiter import RateLimiterPool
        assert RateLimiterPool is not None

    def test_pool_get_creates_limiter(self):
        """get() should create and return a rate limiter"""
        from utils.rate_limiter import RateLimiterPool, AsyncRateLimiter
        pool = RateLimiterPool()

        limiter = pool.get("github")
        assert isinstance(limiter, AsyncRateLimiter)

    def test_pool_get_returns_same_limiter(self):
        """get() should return the same limiter for same API"""
        from utils.rate_limiter import RateLimiterPool
        pool = RateLimiterPool()

        limiter1 = pool.get("github")
        limiter2 = pool.get("github")
        assert limiter1 is limiter2

    def test_pool_get_different_apis(self):
        """get() should return different limiters for different APIs"""
        from utils.rate_limiter import RateLimiterPool
        pool = RateLimiterPool()

        github_limiter = pool.get("github")
        sec_limiter = pool.get("sec_edgar")
        assert github_limiter is not sec_limiter

    def test_pool_has_api_limits(self):
        """Pool should have predefined API limits"""
        from utils.rate_limiter import RateLimiterPool
        pool = RateLimiterPool()

        # GitHub: 5000/hour
        github = pool.get("github")
        assert github.rate == 5000
        assert github.period == 3600

        # SEC EDGAR: 10/second
        sec = pool.get("sec_edgar")
        assert sec.rate == 10
        assert sec.period == 1

        # Companies House: 600/5min
        ch = pool.get("companies_house")
        assert ch.rate == 600
        assert ch.period == 300

    def test_pool_unknown_api_unlimited(self):
        """Unknown API should get unlimited limiter"""
        from utils.rate_limiter import RateLimiterPool
        pool = RateLimiterPool()

        unknown = pool.get("unknown_api")
        assert unknown.rate is None

    def test_pool_reset(self):
        """reset() should clear all limiters"""
        from utils.rate_limiter import RateLimiterPool
        pool = RateLimiterPool()

        limiter1 = pool.get("github")
        pool.reset()
        limiter2 = pool.get("github")

        assert limiter1 is not limiter2


class TestGlobalRateLimiter:
    """Test global convenience functions"""

    def test_get_rate_limiter_function(self):
        """get_rate_limiter() should return limiter from global pool"""
        from utils.rate_limiter import get_rate_limiter, AsyncRateLimiter

        limiter = get_rate_limiter("github")
        assert isinstance(limiter, AsyncRateLimiter)

    def test_reset_limiters_function(self):
        """reset_limiters() should reset global pool"""
        from utils.rate_limiter import get_rate_limiter, reset_limiters

        limiter1 = get_rate_limiter("github")
        reset_limiters()
        limiter2 = get_rate_limiter("github")

        assert limiter1 is not limiter2


class TestConcurrentAccess:
    """Test thread-safety and concurrent access"""

    @pytest.mark.asyncio
    async def test_concurrent_acquire(self):
        """Multiple concurrent acquires should be safe"""
        from utils.rate_limiter import AsyncRateLimiter

        limiter = AsyncRateLimiter(rate=10, period=1)
        results = []

        async def acquire_and_record(id: int):
            await limiter.acquire()
            results.append(id)

        # Launch 5 concurrent tasks
        tasks = [acquire_and_record(i) for i in range(5)]
        await asyncio.gather(*tasks)

        assert len(results) == 5
        assert sorted(results) == [0, 1, 2, 3, 4]
