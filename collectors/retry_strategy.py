"""
Centralized Retry Strategy for Discovery Engine Collectors.

Provides:
- RetryConfig: Configuration for retry behavior
- with_retry: Async wrapper with exponential backoff
- is_retryable_error: Error classification
- get_retry_after_seconds: Retry-After header parsing

Usage:
    from collectors.retry_strategy import with_retry, RetryConfig

    config = RetryConfig(max_retries=3, backoff_base=2.0)

    async def fetch_data():
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    result = await with_retry(fetch_data, config)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimitError(Exception):
    """
    Raised when an API returns a rate-limit response.

    Attributes:
        wait_seconds: Server-requested wait time (None only when is_secondary=True).
        status_code: HTTP status code (e.g. 429, 403).
        endpoint: The API endpoint that was rate-limited.
        is_secondary: True for secondary/abuse-detection rate limits.
        response_summary: Short summary of the response for logging.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        wait_seconds: Optional[float] = None,
        status_code: int = 429,
        endpoint: str = "",
        is_secondary: bool = False,
        response_summary: str = "",
    ):
        if wait_seconds is None and not is_secondary:
            raise ValueError(
                "wait_seconds is required for non-secondary RateLimitError"
            )
        super().__init__(message)
        self.wait_seconds = wait_seconds
        self.status_code = status_code
        self.endpoint = endpoint
        self.is_secondary = is_secondary
        self.response_summary = response_summary


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    backoff_base: float = 2.0  # Exponential base (2^attempt)
    backoff_max: float = 30.0  # Maximum wait time in seconds
    jitter: bool = True  # Add randomness to prevent thundering herd

    def get_wait_seconds(self, attempt: int) -> float:
        """
        Calculate wait time for a given attempt (0-indexed).

        Uses exponential backoff: base^attempt, capped at backoff_max.
        Optionally adds jitter (±25%) to prevent synchronized retries.

        Args:
            attempt: Zero-indexed attempt number (0 = first retry)

        Returns:
            Wait time in seconds
        """
        # Exponential backoff: 2^0=1, 2^1=2, 2^2=4, etc.
        wait = min(self.backoff_base ** attempt, self.backoff_max)

        if self.jitter:
            # Add ±25% jitter
            jitter_factor = 0.75 + (random.random() * 0.5)
            wait *= jitter_factor

        return wait


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if an error is retryable.

    Retryable errors:
    - ConnectionError (network issues)
    - TimeoutError / asyncio.TimeoutError
    - httpx.RequestError (all transport-level failures)
    - HTTP 5xx (server errors)
    - HTTP 429 (rate limited)

    Non-retryable errors:
    - HTTP 4xx (except 429) - client errors
    - ValueError, TypeError, etc. - programming errors

    Args:
        error: The exception to classify

    Returns:
        True if the error should be retried
    """
    # Rate limit errors are always retryable
    if isinstance(error, RateLimitError):
        return True

    # Network errors (Python built-ins)
    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True

    # httpx transport failures (covers TimeoutException, ConnectError, DNS issues, protocol errors)
    # Note: httpx.RequestError is the base class for all transport-level failures:
    # - httpx.TimeoutException (ReadTimeout, ConnectTimeout, WriteTimeout, PoolTimeout)
    # - httpx.ConnectError (connection refused, DNS failure)
    # - httpx.ReadError, httpx.WriteError
    if isinstance(error, httpx.RequestError):
        return True

    # HTTP status errors (4xx/5xx responses)
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code

        # 5xx server errors are retryable
        if 500 <= status < 600:
            return True

        # 429 rate limited is retryable
        if status == 429:
            return True

        # 4xx client errors are NOT retryable (except 429)
        return False

    # Other errors are not retryable by default
    return False


def get_retry_after_seconds(error: Exception) -> Optional[float]:
    """
    Extract Retry-After value from an error.

    Supports:
    - RateLimitError.wait_seconds (direct)
    - httpx.HTTPStatusError Retry-After header (numeric or HTTP-date)

    Args:
        error: The exception

    Returns:
        Wait time in seconds, or None if not available
    """
    # RateLimitError carries wait_seconds directly
    if isinstance(error, RateLimitError):
        return error.wait_seconds

    if not isinstance(error, httpx.HTTPStatusError):
        return None

    retry_after = error.response.headers.get("Retry-After")
    if retry_after is None:
        return None

    # Try numeric first
    try:
        return float(retry_after)
    except ValueError:
        pass

    # Try HTTP-date format (e.g. "Fri, 21 Feb 2026 12:00:00 GMT")
    try:
        from datetime import datetime, timezone

        target = parsedate_to_datetime(retry_after)
        delta = (target - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (ValueError, TypeError):
        return None


async def with_retry(
    func: Callable[[], T],
    config: RetryConfig,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
) -> T:
    """
    Execute an async function with retry logic.

    Uses exponential backoff with optional jitter. Respects Retry-After
    headers on 429 responses and RateLimitError wait times.

    Rate-limit retry policy (RATE_LIMIT_RETRY_POLICY env var):
    - "conservative" (default): wait = max(retry_after, backoff)
    - "exact": wait = retry_after (trust server timing)

    Secondary rate limits always enforce a 60s floor regardless of policy.

    Args:
        func: Async function to execute (no arguments)
        config: Retry configuration
        retry_on: Tuple of exception types to retry on (default: use is_retryable_error)

    Returns:
        Result of func() on success

    Raises:
        The last exception if all retries exhausted
    """
    # Read policy once per call (not per attempt)
    policy = os.environ.get("RATE_LIMIT_RETRY_POLICY", "conservative").lower()

    last_error: Optional[Exception] = None

    for attempt in range(config.max_retries + 1):  # +1 for initial attempt
        try:
            return await func()

        except Exception as e:
            # Check if we should retry this error type
            if retry_on is not None:
                should_retry = isinstance(e, retry_on)
            else:
                should_retry = is_retryable_error(e)

            if not should_retry:
                raise

            last_error = e

            # Check if we have retries left
            if attempt >= config.max_retries:
                logger.error(
                    f"All {config.max_retries} retries exhausted. "
                    f"Last error: {e}"
                )
                raise

            # Calculate wait time
            backoff = config.get_wait_seconds(attempt)
            retry_after = get_retry_after_seconds(e)

            if isinstance(e, RateLimitError) and e.is_secondary:
                # Secondary rate limits: always 60s floor
                wait_time = max(60.0, backoff)
            elif isinstance(e, RateLimitError) and retry_after is not None:
                # Primary RateLimitError with known wait
                if policy == "exact":
                    wait_time = retry_after
                else:
                    # conservative: take the longer of the two
                    wait_time = max(retry_after, backoff)
            elif retry_after is not None:
                # Normal HTTPStatusError with Retry-After header
                wait_time = retry_after
            else:
                wait_time = backoff

            logger.warning(
                f"Attempt {attempt + 1}/{config.max_retries + 1} failed: {e}. "
                f"Retrying in {wait_time:.2f}s..."
            )

            await asyncio.sleep(wait_time)

    # Should never reach here, but just in case
    if last_error:
        raise last_error
    raise RuntimeError("Unexpected state in with_retry")
