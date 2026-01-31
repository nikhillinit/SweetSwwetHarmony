"""
Playwright Transport for Headless Browser Rendering

Third-tier transport fallback for sites requiring JavaScript execution.
Uses Playwright with Chromium for full browser rendering.

Features:
- Semaphore gating for concurrency control (default: 2)
- Intelligent wait strategies (selector-based, DOM stability)
- Content size limiting
- Graceful degradation when Playwright not installed
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, TYPE_CHECKING

from monitoring.content_pipeline.exceptions import ContentSizeExceededError
from monitoring.content_pipeline.models import FetchArtifact

logger = logging.getLogger(__name__)

# Check if Playwright is available
_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PlaywrightTimeout = TimeoutError  # Fallback for type hints
    logger.debug("Playwright not installed, transport will be unavailable")

# Global semaphore shared across all instances
_global_semaphore: Optional[asyncio.Semaphore] = None
_semaphore_lock = asyncio.Lock()


async def _get_global_semaphore(max_concurrent: int) -> asyncio.Semaphore:
    """Get or create the global semaphore for browser concurrency."""
    global _global_semaphore
    async with _semaphore_lock:
        if _global_semaphore is None:
            _global_semaphore = asyncio.Semaphore(max_concurrent)
        return _global_semaphore


# Common content selectors to auto-detect
AUTO_DETECT_SELECTORS = [
    "main",
    "article",
    "#content",
    "#main",
    "#app",
    "[data-content]",
    "[role='main']",
    ".content",
    ".main-content",
]


class PlaywrightTransport:
    """
    Headless browser transport using Playwright.

    Provides full JavaScript rendering for sites that require it.
    Uses semaphore gating to limit concurrent browser instances.

    Example:
        transport = PlaywrightTransport(max_concurrent=2)
        result = await transport.fetch("https://spa-site.com")
    """

    # Class-level defaults
    default_timeout_ms: int = 30000  # 30 seconds
    default_max_html_bytes: int = 5_242_880  # 5MB

    def __init__(self, max_concurrent: int = 2):
        """
        Initialize PlaywrightTransport.

        Args:
            max_concurrent: Maximum concurrent browser operations (default: 2)
        """
        self._max_concurrent = max_concurrent
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._initialized = False

    @property
    def max_concurrent(self) -> int:
        """Get max concurrent browser operations."""
        return self._max_concurrent

    @property
    def _semaphore(self) -> asyncio.Semaphore:
        """Get the shared semaphore (lazy initialization)."""
        return self.__semaphore

    @_semaphore.setter
    def _semaphore(self, value: Optional[asyncio.Semaphore]) -> None:
        """Set the semaphore."""
        self.__semaphore = value

    async def _ensure_semaphore(self) -> asyncio.Semaphore:
        """Ensure semaphore is initialized."""
        if self.__semaphore is None:
            self.__semaphore = await _get_global_semaphore(self._max_concurrent)
        return self.__semaphore

    def is_available(self) -> bool:
        """
        Check if Playwright is installed and available.

        Returns:
            True if Playwright can be used, False otherwise
        """
        return _PLAYWRIGHT_AVAILABLE

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
        wait_for_selector: Optional[str] = None,
        **kwargs,
    ) -> FetchArtifact:
        """
        Fetch URL using headless browser with semaphore gating.

        Args:
            url: URL to fetch
            etag: Optional ETag (not used for browser, but accepted for interface)
            last_modified: Optional Last-Modified (not used for browser)
            timeout: Timeout in milliseconds (default: 30000)
            max_html_bytes: Maximum HTML content size (default: 5MB)
            max_json_bytes: Maximum JSON content size (unused)
            wait_for_selector: Explicit CSS selector to wait for
            **kwargs: Additional arguments (ignored)

        Returns:
            FetchArtifact with rendered content

        Raises:
            ImportError: If Playwright is not installed
            ContentSizeExceededError: If content exceeds size limit
            TimeoutError: If navigation or wait times out
        """
        if not _PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )

        # Acquire semaphore before browser operations
        semaphore = await self._ensure_semaphore()
        async with semaphore:
            return await self._fetch_with_browser(
                url=url,
                timeout_ms=int(timeout) if timeout else self.default_timeout_ms,
                max_html_bytes=max_html_bytes or self.default_max_html_bytes,
                wait_for_selector=wait_for_selector,
            )

    async def _fetch_with_browser(
        self,
        url: str,
        timeout_ms: int,
        max_html_bytes: int,
        wait_for_selector: Optional[str] = None,
    ) -> FetchArtifact:
        """
        Internal method to fetch using Playwright browser.

        Args:
            url: URL to fetch
            timeout_ms: Navigation timeout in milliseconds
            max_html_bytes: Maximum content size
            wait_for_selector: Optional selector to wait for

        Returns:
            FetchArtifact with response data
        """
        start_time = time.perf_counter()

        async with async_playwright() as playwright:
            # Launch Chromium in headless mode
            browser = await playwright.chromium.launch(headless=True)

            try:
                # Create new context and page
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )

                try:
                    page = await context.new_page()

                    # Navigate to URL
                    response = await page.goto(
                        url,
                        timeout=timeout_ms,
                        wait_until="domcontentloaded",
                    )

                    # Wait for content
                    await self._wait_for_content(
                        page,
                        wait_for_selector=wait_for_selector,
                        timeout_ms=timeout_ms,
                    )

                    # Get rendered content
                    content = await page.content()

                    # Check content size
                    content_bytes = len(content.encode("utf-8"))
                    if content_bytes > max_html_bytes:
                        raise ContentSizeExceededError(
                            url=url,
                            max_size=max_html_bytes,
                            actual_size=content_bytes,
                        )

                    # Build FetchArtifact
                    fetch_time_ms = int((time.perf_counter() - start_time) * 1000)

                    # Extract headers (convert to dict with lowercase keys)
                    headers: Dict[str, str] = {}
                    if response:
                        for key, value in response.headers.items():
                            headers[key.lower()] = value

                    return FetchArtifact(
                        url=page.url,  # May be different due to redirects
                        status_code=response.status if response else 200,
                        headers=headers,
                        content=content,
                        transport_used="playwright",
                        fetch_time_ms=fetch_time_ms,
                        fetched_at=datetime.now(timezone.utc),
                    )

                finally:
                    await context.close()

            finally:
                await browser.close()

    async def _wait_for_content(
        self,
        page,
        wait_for_selector: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> None:
        """
        Wait for page content to be ready.

        Uses explicit selector if provided, otherwise tries common content selectors.

        Args:
            page: Playwright page object
            wait_for_selector: Explicit selector to wait for
            timeout_ms: Maximum wait time
        """
        # If explicit selector provided, wait for it
        if wait_for_selector:
            try:
                await page.wait_for_selector(
                    wait_for_selector,
                    timeout=min(timeout_ms, 10000),  # Max 10s for selector wait
                    state="visible",
                )
                return
            except PlaywrightTimeout:
                logger.debug(
                    "Explicit selector %s not found, continuing anyway",
                    wait_for_selector,
                )
                return

        # Try auto-detecting main content
        for selector in AUTO_DETECT_SELECTORS:
            try:
                element = await page.query_selector(selector)
                if element:
                    await page.wait_for_selector(
                        selector,
                        timeout=5000,  # 5s max per selector
                        state="visible",
                    )
                    logger.debug("Found content via selector: %s", selector)
                    return
            except PlaywrightTimeout:
                continue

        # Fallback: short wait for DOM stability
        await asyncio.sleep(0.5)
