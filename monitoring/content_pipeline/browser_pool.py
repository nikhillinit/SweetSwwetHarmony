"""
Browser Pool for Playwright Context Management

Manages reusable browser contexts with lifecycle management:
- TTL-based expiration (default: 5 minutes)
- Page count limits (default: 50 pages per context)
- Lazy browser initialization
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

# Check if Playwright is available
_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    Browser = Any
    BrowserContext = Any
    logger.debug("Playwright not installed, BrowserPool will be unavailable")


@dataclass
class ContextInfo:
    """Information about a managed browser context."""
    context_id: str
    context: BrowserContext
    created_at: float = field(default_factory=time.time)
    page_count: int = 0
    in_use: bool = False


class BrowserPool:
    """
    Manages reusable browser contexts with lifecycle management.

    Features:
    - Lazy browser initialization (only launched on first acquire)
    - Context reuse within TTL and page count limits
    - Automatic cleanup of expired contexts
    - Thread-safe via asyncio locks

    Example:
        pool = BrowserPool(max_contexts=2, context_ttl_seconds=300)

        # Acquire context
        info = await pool.acquire()
        page = await info["context"].new_page()

        # Use page...

        # Release context back to pool
        await pool.release(info["context_id"])

        # Cleanup when done
        await pool.cleanup()
    """

    def __init__(
        self,
        max_contexts: int = 2,
        context_ttl_seconds: int = 300,
        max_pages_per_context: int = 50,
    ):
        """
        Initialize BrowserPool.

        Args:
            max_contexts: Maximum number of contexts to maintain (default: 2)
            context_ttl_seconds: Time-to-live for contexts in seconds (default: 300)
            max_pages_per_context: Max pages before recycling context (default: 50)
        """
        self.max_contexts = max_contexts
        self.context_ttl_seconds = context_ttl_seconds
        self.max_pages_per_context = max_pages_per_context

        # Internal state
        self._browser: Optional[Browser] = None
        self._playwright = None
        self._contexts: Dict[str, ContextInfo] = {}
        self._page_counts: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_browser(self) -> Browser:
        """Ensure browser is initialized (lazy init)."""
        if self._browser is not None:
            return self._browser

        if not _PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )

        # Launch playwright and browser
        self._playwright = await async_playwright().__aenter__()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._initialized = True

        logger.info("Browser pool initialized with Chromium")
        return self._browser

    async def acquire(self) -> Dict[str, Any]:
        """
        Acquire a browser context from the pool.

        Returns available context or creates new one if under limit.
        Expired or over-limit contexts are recycled.

        Returns:
            Dict with 'context_id' and 'context' keys
        """
        async with self._lock:
            browser = await self._ensure_browser()

            # Try to find an available, valid context
            for ctx_id, info in list(self._contexts.items()):
                if info.in_use:
                    continue

                # Check TTL expiration
                age = time.time() - info.created_at
                if age > self.context_ttl_seconds:
                    await self._close_context(ctx_id)
                    continue

                # Check page count limit
                if info.page_count >= self.max_pages_per_context:
                    await self._close_context(ctx_id)
                    continue

                # Context is valid and available
                info.in_use = True
                logger.debug("Reusing context %s", ctx_id)
                return {"context_id": ctx_id, "context": info.context}

            # No available context, create new if under limit
            if len(self._contexts) < self.max_contexts:
                return await self._create_context()

            # At limit, wait for one to become available (shouldn't happen with proper semaphore)
            # For now, just create one anyway (caller should use semaphore)
            logger.warning("Creating context beyond max_contexts limit")
            return await self._create_context()

    async def _create_context(self) -> Dict[str, Any]:
        """Create a new browser context."""
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        ctx_id = str(uuid.uuid4())[:8]
        info = ContextInfo(
            context_id=ctx_id,
            context=context,
            in_use=True,
        )

        self._contexts[ctx_id] = info
        self._page_counts[ctx_id] = 0

        logger.debug("Created new context %s", ctx_id)
        return {"context_id": ctx_id, "context": context}

    async def _close_context(self, context_id: str) -> None:
        """Close and remove a context."""
        if context_id not in self._contexts:
            return

        info = self._contexts.pop(context_id)
        self._page_counts.pop(context_id, None)

        try:
            await info.context.close()
            logger.debug("Closed context %s", context_id)
        except Exception as e:
            logger.warning("Error closing context %s: %s", context_id, e)

    async def release(self, context_id: str) -> None:
        """
        Release a context back to the pool.

        Increments page count and marks context as available.

        Args:
            context_id: ID of context to release
        """
        async with self._lock:
            if context_id not in self._contexts:
                logger.warning("Releasing unknown context %s", context_id)
                return

            info = self._contexts[context_id]
            info.in_use = False
            info.page_count += 1
            self._page_counts[context_id] = info.page_count

            logger.debug(
                "Released context %s (page_count=%d)",
                context_id,
                info.page_count,
            )

    async def cleanup(self) -> None:
        """
        Close all contexts and browser.

        Safe to call multiple times.
        """
        async with self._lock:
            # Close all contexts
            for ctx_id in list(self._contexts.keys()):
                await self._close_context(ctx_id)

            # Close browser
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.warning("Error closing browser: %s", e)
                self._browser = None

            # Close playwright
            if self._playwright is not None:
                try:
                    await self._playwright.__aexit__(None, None, None)
                except Exception as e:
                    logger.warning("Error closing playwright: %s", e)
                self._playwright = None

            self._initialized = False
            logger.info("Browser pool cleaned up")

    def stats(self) -> Dict[str, int]:
        """
        Get pool statistics.

        Returns:
            Dict with 'total', 'available', 'in_use' counts
        """
        total = len(self._contexts)
        in_use = sum(1 for info in self._contexts.values() if info.in_use)
        available = total - in_use

        return {
            "total": total,
            "available": available,
            "in_use": in_use,
        }
