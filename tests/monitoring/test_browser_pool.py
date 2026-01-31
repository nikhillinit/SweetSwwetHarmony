"""Tests for BrowserPool with context lifecycle management.

Tests cover:
- Context acquisition and release
- TTL-based expiration
- Page count limits
- Lazy browser launch
- Graceful shutdown
- Semaphore integration
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestBrowserPoolExists:
    """Test that BrowserPool class exists and has correct interface."""

    def test_import_browser_pool(self):
        """BrowserPool should be importable."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        assert BrowserPool is not None

    def test_has_acquire_method(self):
        """BrowserPool should have async acquire method."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool()
        assert hasattr(pool, "acquire")
        import inspect
        assert inspect.iscoroutinefunction(pool.acquire)

    def test_has_release_method(self):
        """BrowserPool should have async release method."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool()
        assert hasattr(pool, "release")
        import inspect
        assert inspect.iscoroutinefunction(pool.release)

    def test_has_cleanup_method(self):
        """BrowserPool should have async cleanup method."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool()
        assert hasattr(pool, "cleanup")
        import inspect
        assert inspect.iscoroutinefunction(pool.cleanup)

    def test_default_configuration(self):
        """BrowserPool should have sensible defaults."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool()
        assert pool.max_contexts == 2
        assert pool.context_ttl_seconds == 300  # 5 minutes
        assert pool.max_pages_per_context == 50


class TestBrowserPoolConfiguration:
    """Test custom configuration options."""

    def test_custom_max_contexts(self):
        """BrowserPool should accept custom max_contexts."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool(max_contexts=4)
        assert pool.max_contexts == 4

    def test_custom_ttl(self):
        """BrowserPool should accept custom context_ttl_seconds."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool(context_ttl_seconds=600)
        assert pool.context_ttl_seconds == 600

    def test_custom_max_pages(self):
        """BrowserPool should accept custom max_pages_per_context."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool(max_pages_per_context=100)
        assert pool.max_pages_per_context == 100


class TestBrowserPoolAcquire:
    """Test context acquisition."""

    @pytest.mark.asyncio
    async def test_acquire_returns_context_info(self):
        """acquire() should return context info with id and context."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool()
            context_info = await pool.acquire()

            assert context_info is not None
            assert "context_id" in context_info
            assert "context" in context_info

    @pytest.mark.asyncio
    async def test_acquire_reuses_existing_context(self):
        """acquire() should reuse existing context if available and valid."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool()

            # First acquire
            info1 = await pool.acquire()
            await pool.release(info1["context_id"])

            # Second acquire should reuse
            info2 = await pool.acquire()

            # Should be same context (reused)
            assert info1["context_id"] == info2["context_id"]

    @pytest.mark.asyncio
    async def test_acquire_creates_new_when_all_busy(self):
        """acquire() should create new context when all existing are busy."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context1 = AsyncMock()
            mock_context2 = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(side_effect=[mock_context1, mock_context2])

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool(max_contexts=2)

            # Acquire first without releasing
            info1 = await pool.acquire()
            # Acquire second without releasing first
            info2 = await pool.acquire()

            # Should be different contexts
            assert info1["context_id"] != info2["context_id"]


class TestBrowserPoolRelease:
    """Test context release."""

    @pytest.mark.asyncio
    async def test_release_makes_context_available(self):
        """release() should make context available for reuse."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool()

            info = await pool.acquire()
            await pool.release(info["context_id"])

            # Check pool stats
            stats = pool.stats()
            assert stats["available"] >= 1

    @pytest.mark.asyncio
    async def test_release_increments_page_count(self):
        """release() should increment page count for the context."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool(max_pages_per_context=50)

            info = await pool.acquire()
            context_id = info["context_id"]

            # Release multiple times
            await pool.release(context_id)
            await pool.acquire()  # Reacquire same context
            await pool.release(context_id)

            # Page count should be tracked
            assert pool._page_counts.get(context_id, 0) >= 2


class TestBrowserPoolTTLExpiration:
    """Test TTL-based context expiration."""

    @pytest.mark.asyncio
    async def test_expired_context_not_reused(self):
        """Expired context should be closed and new one created."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context1 = AsyncMock()
            mock_context2 = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(side_effect=[mock_context1, mock_context2])

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            # Very short TTL for testing
            pool = BrowserPool(context_ttl_seconds=0)

            info1 = await pool.acquire()
            await pool.release(info1["context_id"])

            # Wait for expiration
            await asyncio.sleep(0.1)

            # Second acquire should get new context (old one expired)
            info2 = await pool.acquire()

            assert info1["context_id"] != info2["context_id"]


class TestBrowserPoolPageLimit:
    """Test page count limit enforcement."""

    @pytest.mark.asyncio
    async def test_context_recycled_after_max_pages(self):
        """Context should be recycled after max_pages_per_context reached."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context1 = AsyncMock()
            mock_context2 = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(side_effect=[mock_context1, mock_context2])

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            # Very low page limit for testing
            pool = BrowserPool(max_pages_per_context=2)

            # Use context twice
            info1 = await pool.acquire()
            ctx_id = info1["context_id"]
            await pool.release(ctx_id)

            info2 = await pool.acquire()
            await pool.release(ctx_id)

            # Third acquire should get new context (old one reached limit)
            info3 = await pool.acquire()

            assert info3["context_id"] != ctx_id


class TestBrowserPoolCleanup:
    """Test cleanup and shutdown."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_all_contexts(self):
        """cleanup() should close all contexts and browser."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)
            mock_browser.close = AsyncMock()

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool()

            # Acquire and release
            info = await pool.acquire()
            await pool.release(info["context_id"])

            # Cleanup
            await pool.cleanup()

            # Context should have been closed
            mock_context.close.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_is_idempotent(self):
        """cleanup() can be called multiple times safely."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        pool = BrowserPool()

        # Multiple cleanups should not raise
        await pool.cleanup()
        await pool.cleanup()
        await pool.cleanup()


class TestBrowserPoolStats:
    """Test pool statistics."""

    def test_stats_returns_dict(self):
        """stats() should return statistics dictionary."""
        from monitoring.content_pipeline.browser_pool import BrowserPool
        pool = BrowserPool()

        stats = pool.stats()

        assert isinstance(stats, dict)
        assert "total" in stats
        assert "available" in stats
        assert "in_use" in stats

    @pytest.mark.asyncio
    async def test_stats_tracks_usage(self):
        """stats() should track context usage."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool()

            # Before acquire
            stats_before = pool.stats()
            assert stats_before["total"] == 0

            # After acquire
            info = await pool.acquire()
            stats_during = pool.stats()
            assert stats_during["in_use"] == 1

            # After release
            await pool.release(info["context_id"])
            stats_after = pool.stats()
            assert stats_after["available"] == 1
            assert stats_after["in_use"] == 0


class TestBrowserPoolLazyInit:
    """Test lazy browser initialization."""

    def test_browser_not_launched_on_init(self):
        """Browser should not be launched on pool initialization."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            pool = BrowserPool()

            # Playwright should not be called yet
            mock_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_browser_launched_on_first_acquire(self):
        """Browser should be launched on first acquire."""
        from monitoring.content_pipeline.browser_pool import BrowserPool

        with patch("monitoring.content_pipeline.browser_pool.async_playwright") as mock_pw:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            pool = BrowserPool()

            # Not launched yet
            assert pool._browser is None

            # First acquire triggers launch
            await pool.acquire()

            # Now browser should be set
            assert pool._browser is not None
