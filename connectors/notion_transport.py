"""
Shared Notion transport for pooled HTTP, rate limiting, and retries.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Optional

import httpx

from utils.rate_limiter import get_rate_limiter, AsyncRateLimiter


class NotionTransport:
    """
    Shared Notion transport for consistent retries, rate limiting, and pooling.

    Call start() and shutdown() in long-running processes to reuse the client.
    """

    def __init__(
        self,
        api_key: str,
        notion_version: str = "2022-06-28",
        base_url: str = "https://api.notion.com/v1",
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 10.0,
        limiter: Optional[AsyncRateLimiter] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        limits: Optional[httpx.Limits] = None,
    ):
        self.api_key = api_key
        self.notion_version = notion_version
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._limiter = limiter or get_rate_limiter("notion")
        self._transport = transport
        self._limits = limits or httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize the shared HTTP client."""
        if self._client and not self._client.is_closed:
            return

        async with self._start_lock:
            if self._client and not self._client.is_closed:
                return
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._build_headers(),
                timeout=self.timeout,
                limits=self._limits,
                transport=self._transport,
            )

    async def shutdown(self) -> None:
        """Close the shared HTTP client."""
        if not self._client:
            return
        await self._client.aclose()
        self._client = None

    async def request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a request to Notion and return the parsed JSON response."""
        if not self._client or self._client.is_closed:
            await self.start()

        if not self._client:
            raise RuntimeError("NotionTransport client not initialized")

        url = path if path.startswith("/") else f"/{path}"
        attempt = 0

        while True:
            attempt += 1
            await self._limiter.acquire()

            try:
                response = await self._client.request(
                    method=method.upper(),
                    url=url,
                    json=json,
                    params=params,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt > self.max_retries:
                    raise
                await self._sleep_with_backoff(attempt)
                continue

            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt > self.max_retries:
                    response.raise_for_status()
                await self._sleep_for_retry(response, attempt)
                continue

            if response.status_code >= 400:
                response.raise_for_status()

            if response.content:
                return response.json()
            return {}

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convenience wrapper for GET."""
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convenience wrapper for POST."""
        return await self.request("POST", path, json=json)

    async def patch(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convenience wrapper for PATCH."""
        return await self.request("PATCH", path, json=json)

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }

    async def _sleep_for_retry(self, response: httpx.Response, attempt: int) -> None:
        retry_after = self._parse_retry_after(response)
        if retry_after is not None:
            await asyncio.sleep(retry_after)
            return
        await self._sleep_with_backoff(attempt)

    def _parse_retry_after(self, response: httpx.Response) -> Optional[float]:
        if response.status_code != 429:
            return None
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            value = float(header)
        except ValueError:
            return None
        return value if value >= 0 else None

    async def _sleep_with_backoff(self, attempt: int) -> None:
        delay = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)
        delay += random.uniform(0, 0.25)
        await asyncio.sleep(delay)
