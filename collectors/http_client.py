"""
Centralized HTTP primitive for Discovery Engine collectors.

Provides:
- CollectorHttpClient: shared instrumented HTTP client wrapper
- RunContext: per-run correlation context
- TelemetryLogger: structured request telemetry

Architecture contracts:
1. CollectorHttpClient is the only allowed collector HTTP entry point.
2. Primitive is body-agnostic — no response body parsing for retry decisions.
3. Retry delegates to existing with_retry() from retry_strategy.py.
4. Ordering invariant: rate_limiter.acquire() -> semaphore.acquire() -> HTTP call.

Usage:
    from collectors.http_client import CollectorHttpClient, RunContext

    ctx = RunContext(execution_id="abc123")
    client = CollectorHttpClient(
        httpx_client,
        run_context=ctx,
        rate_limiter=limiter,
        collector_name="github",
    )
    response = await client.request("GET", "https://api.github.com/repos")
    data = await client.request_json("GET", "https://api.github.com/repos")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from collectors.retry_strategy import RetryConfig, with_retry
from utils.rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)


@dataclass
class RunContext:
    """Per-run correlation context for telemetry."""

    execution_id: str
    dry_run: bool = False


class TelemetryLogger:
    """Structured telemetry for HTTP requests.

    Emits:
    - DEBUG per-attempt events (before each HTTP call)
    - INFO per-request completion events (after successful response)

    All events include execution_id for correlation.
    """

    def __init__(self, execution_id: str):
        self.execution_id = execution_id
        self._logger = logging.getLogger(f"{__name__}.telemetry")

    def log_attempt(
        self,
        *,
        method: str,
        url: str,
        attempt: int,
        collector: str,
    ) -> None:
        """DEBUG-level per-attempt event."""
        self._logger.debug(
            "http_attempt collector=%s method=%s url=%s attempt=%d execution_id=%s",
            collector,
            method,
            url,
            attempt,
            self.execution_id,
        )

    def log_request(
        self,
        *,
        method: str,
        url: str,
        status_code: int,
        duration_ms: float,
        collector: str,
    ) -> None:
        """INFO-level per-request completion event."""
        self._logger.info(
            "http_request collector=%s method=%s url=%s status=%d duration_ms=%.2f execution_id=%s",
            collector,
            method,
            url,
            status_code,
            duration_ms,
            self.execution_id,
        )


class CollectorHttpClient:
    """
    Shared instrumented HTTP client for collectors.

    Wraps httpx.AsyncClient with:
    - Rate limiting (per-API via AsyncRateLimiter)
    - Concurrency control (asyncio.Semaphore)
    - Retry delegation through with_retry()
    - Telemetry logging with execution_id correlation

    Ordering invariant: rate_limiter.acquire() -> semaphore.acquire() -> HTTP call
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        run_context: RunContext,
        rate_limiter: Optional[AsyncRateLimiter] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
        retry_config: Optional[RetryConfig] = None,
        collector_name: str = "unknown",
    ):
        self._client = client
        self._run_context = run_context
        self._rate_limiter = rate_limiter
        self._semaphore = semaphore or asyncio.Semaphore(10)
        self._retry_config = retry_config or RetryConfig()
        self._collector_name = collector_name
        self._telemetry = TelemetryLogger(run_context.execution_id)

    @property
    def execution_id(self) -> str:
        return self._run_context.execution_id

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        data: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        """
        Make an HTTP request with rate limiting, concurrency control, and retry.

        Ordering invariant: rate_limiter.acquire() -> semaphore.acquire() -> HTTP call

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            headers: Optional request headers
            params: Optional query parameters
            json: Optional JSON body
            data: Optional form data
            timeout: Optional per-request timeout override

        Returns:
            httpx.Response on success

        Raises:
            httpx.HTTPStatusError: On non-retryable 4xx errors
            Exception: After all retries exhausted
        """
        start = time.monotonic()
        attempt_counter = [0]

        async def _do_request() -> httpx.Response:
            attempt_counter[0] += 1
            self._telemetry.log_attempt(
                method=method,
                url=url,
                attempt=attempt_counter[0],
                collector=self._collector_name,
            )

            # Ordering: rate_limiter -> semaphore -> HTTP
            if self._rate_limiter:
                await self._rate_limiter.acquire()

            async with self._semaphore:
                kwargs: Dict[str, Any] = {"method": method, "url": url}
                if headers:
                    kwargs["headers"] = headers
                if params:
                    kwargs["params"] = params
                if json is not None:
                    kwargs["json"] = json
                if data is not None:
                    kwargs["data"] = data
                if timeout is not None:
                    kwargs["timeout"] = timeout

                response = await self._client.request(**kwargs)
                response.raise_for_status()
                return response

        response = await with_retry(_do_request, self._retry_config)

        duration_ms = (time.monotonic() - start) * 1000
        self._telemetry.log_request(
            method=method,
            url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
            collector=self._collector_name,
        )

        return response

    async def request_json(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        """Make a request and return parsed JSON response."""
        response = await self.request(method, url, **kwargs)
        return response.json()

    async def request_text(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> str:
        """Make a request and return text response."""
        response = await self.request(method, url, **kwargs)
        return response.text
