"""
Per-API Rate Limiter for Discovery Engine Collectors.

Provides token-bucket style rate limiting with:
- Per-API rate limits (GitHub, SEC EDGAR, Companies House, etc.)
- Async-safe implementation using asyncio.Lock
- Global pool for shared limiters across collectors

Usage:
    from utils.rate_limiter import get_rate_limiter

    # Get rate limiter for an API
    limiter = get_rate_limiter("github")

    # Before making an API call
    await limiter.acquire()
    response = await client.get(url)

API Limits (from CLAUDE.md):
    - GitHub: 5000/hour
    - SEC EDGAR: 10/second
    - Companies House: 600/5min
    - Product Hunt: 100/hour (conservative)
    - ArXiv, USPTO, Domain WHOIS: unlimited
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """
    Async rate limiter using token bucket algorithm.

    Tokens are refilled over time based on the configured rate.
    Callers wait if no tokens are available.

    Args:
        rate: Maximum requests per period (None = unlimited)
        period: Time period in seconds
    """

    def __init__(self, rate: Optional[int] = None, period: int = 1):
        self.rate = rate
        self.period = period
        self._lock = asyncio.Lock()
        self._tokens: float = float(rate) if rate else float("inf")
        self._last_refill: Optional[float] = None

    async def acquire(self) -> None:
        """
        Acquire permission to make a request.

        Blocks until a token is available (rate limit allows).
        For unlimited limiters (rate=None), returns immediately.
        """
        if self.rate is None:
            return

        async with self._lock:
            now = time.monotonic()

            # Initialize on first call
            if self._last_refill is None:
                self._last_refill = now
                self._tokens = float(self.rate)

            # Refill tokens based on elapsed time
            elapsed = now - self._last_refill
            refill_amount = elapsed * (self.rate / self.period)
            self._tokens = min(self.rate, self._tokens + refill_amount)
            self._last_refill = now

            # Wait if we don't have tokens
            if self._tokens < 1:
                # Calculate how long to wait for 1 token
                wait_time = (1 - self._tokens) * (self.period / self.rate)
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self._tokens = 1

            self._tokens -= 1


class RateLimiterPool:
    """
    Factory for per-API rate limiters.

    Manages a pool of rate limiters, creating them on demand with
    the correct limits for each API.
    """

    # API limits from CLAUDE.md
    API_LIMITS: Dict[str, Dict[str, Optional[int]]] = {
        "github": {"rate": 5000, "period": 3600},           # 5000/hour
        "github_activity": {"rate": 5000, "period": 3600},  # Same as GitHub
        "sec_edgar": {"rate": 10, "period": 1},             # 10/second
        "companies_house": {"rate": 600, "period": 300},    # 600/5min
        "domain_whois": {"rate": None, "period": 1},        # Unlimited
        "job_postings": {"rate": None, "period": 1},        # Unlimited
        "product_hunt": {"rate": 100, "period": 3600},      # 100/hour (conservative)
        "arxiv": {"rate": None, "period": 1},               # Unlimited
        "uspto": {"rate": None, "period": 1},               # Unlimited
        "hacker_news": {"rate": 100, "period": 60},         # 100/min (conservative)
    }

    def __init__(self):
        self._limiters: Dict[str, AsyncRateLimiter] = {}

    def get(self, api_name: str) -> AsyncRateLimiter:
        """
        Get or create rate limiter for an API.

        Args:
            api_name: Name of the API (e.g., "github", "sec_edgar")

        Returns:
            AsyncRateLimiter configured for the API
        """
        if api_name not in self._limiters:
            limits = self.API_LIMITS.get(api_name, {"rate": None, "period": 1})
            self._limiters[api_name] = AsyncRateLimiter(
                rate=limits["rate"],
                period=limits["period"],
            )
            if limits["rate"]:
                logger.info(
                    f"Created rate limiter for {api_name}: "
                    f"{limits['rate']} requests per {limits['period']}s"
                )
            else:
                logger.debug(f"Created unlimited rate limiter for {api_name}")

        return self._limiters[api_name]

    def reset(self) -> None:
        """Reset all limiters (for testing)."""
        self._limiters.clear()


# Global pool instance
_global_pool = RateLimiterPool()


def get_rate_limiter(api_name: str) -> AsyncRateLimiter:
    """
    Get rate limiter from global pool.

    Convenience function for getting API-specific rate limiters.

    Args:
        api_name: Name of the API

    Returns:
        AsyncRateLimiter for the API
    """
    return _global_pool.get(api_name)


def reset_limiters() -> None:
    """
    Reset all global rate limiters.

    Primarily for testing purposes.
    """
    _global_pool.reset()
