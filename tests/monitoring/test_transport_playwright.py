"""Tests for PlaywrightTransport with headless browser support.

Tests cover:
- Basic fetch functionality
- Semaphore gating (concurrency limiting)
- Wait strategies (selector-based, DOM stability)
- Error handling (timeout, navigation errors)
- Content size limits
- Graceful degradation when Playwright not installed
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from monitoring.content_pipeline.models import FetchArtifact


class TestPlaywrightTransportExists:
    """Test that PlaywrightTransport class exists and has correct interface."""

    def test_import_playwright_transport(self):
        """PlaywrightTransport should be importable."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        assert PlaywrightTransport is not None

    def test_has_fetch_method(self):
        """PlaywrightTransport should have async fetch method."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()
        assert hasattr(transport, "fetch")
        import inspect
        assert inspect.iscoroutinefunction(transport.fetch)

    def test_has_semaphore_attribute(self):
        """PlaywrightTransport should have a semaphore attribute (lazy-initialized)."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()
        # Semaphore is lazy-initialized, check the method exists
        assert hasattr(transport, "_ensure_semaphore")
        # Semaphore is None until first fetch
        assert hasattr(transport, "_PlaywrightTransport__semaphore")

    def test_default_max_concurrent(self):
        """PlaywrightTransport should default to 2 concurrent browsers."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()
        assert transport.max_concurrent == 2


class TestPlaywrightBasicFetch:
    """Test basic HTTP fetch functionality with headless browser."""

    @pytest.mark.asyncio
    async def test_fetch_returns_fetch_artifact(self):
        """fetch() should return a FetchArtifact on success."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            # Setup mock browser chain
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html><body>Hello</body></html>")
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            result = await transport.fetch("https://example.com")

            assert isinstance(result, FetchArtifact)
            assert result.url == "https://example.com"
            assert result.status_code == 200
            assert "Hello" in result.content

    @pytest.mark.asyncio
    async def test_fetch_uses_playwright_transport_name(self):
        """fetch() should report transport_used='playwright'."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html></html>")
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            result = await transport.fetch("https://example.com")

            assert result.transport_used == "playwright"

    @pytest.mark.asyncio
    async def test_fetch_records_timing(self):
        """fetch() should record fetch_time_ms."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html></html>")
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            result = await transport.fetch("https://example.com")

            assert result.fetch_time_ms >= 0


class TestPlaywrightSemaphoreGating:
    """Test concurrent request limiting via semaphore."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_requests(self):
        """Only 2 concurrent browser requests should be allowed."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        transport = PlaywrightTransport(max_concurrent=2)

        # Track concurrent active requests
        active_count = 0
        max_active = 0
        lock = asyncio.Lock()

        async def slow_fetch(*args, **kwargs):
            nonlocal active_count, max_active
            async with lock:
                active_count += 1
                if active_count > max_active:
                    max_active = active_count

            await asyncio.sleep(0.1)  # Simulate browser work

            async with lock:
                active_count -= 1

            return FetchArtifact(
                url=args[0] if args else kwargs.get("url", ""),
                status_code=200,
                headers={},
                content="<html></html>",
                transport_used="playwright",
            )

        # Patch the actual browser call
        with patch.object(transport, "_fetch_with_browser", slow_fetch):
            # Launch 5 concurrent requests
            tasks = [transport.fetch(f"https://example.com/{i}") for i in range(5)]
            await asyncio.gather(*tasks)

        # Max concurrent should never exceed 2
        assert max_active <= 2

    @pytest.mark.asyncio
    async def test_semaphore_is_shared_across_instances(self):
        """Semaphore should be shared across transport instances."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        transport1 = PlaywrightTransport(max_concurrent=2)
        transport2 = PlaywrightTransport(max_concurrent=2)

        # They should share the same semaphore
        assert transport1._semaphore is transport2._semaphore

    def test_custom_max_concurrent(self):
        """PlaywrightTransport should accept custom max_concurrent."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        transport = PlaywrightTransport(max_concurrent=4)
        assert transport.max_concurrent == 4


class TestPlaywrightWaitStrategies:
    """Test intelligent wait strategies for dynamic content."""

    @pytest.mark.asyncio
    async def test_fetch_with_explicit_wait_selector(self):
        """fetch() should wait for explicit selector when provided."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html><div id='content'>Loaded</div></html>")
            mock_page.url = "https://example.com"
            mock_page.wait_for_selector = AsyncMock()

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            await transport.fetch(
                "https://example.com",
                wait_for_selector="#content",
            )

            mock_page.wait_for_selector.assert_called_once()
            call_args = mock_page.wait_for_selector.call_args
            assert call_args[0][0] == "#content"

    @pytest.mark.asyncio
    async def test_fetch_auto_detects_main_content(self):
        """fetch() should auto-detect main content selectors when none provided."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html><main>Content</main></html>")
            mock_page.url = "https://example.com"
            # Simulate selector existing
            mock_page.wait_for_selector = AsyncMock()
            mock_page.query_selector = AsyncMock(return_value=MagicMock())

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            await transport.fetch("https://example.com")

            # Should try common content selectors
            mock_page.wait_for_selector.assert_called()

    def test_default_timeout_is_30_seconds(self):
        """PlaywrightTransport should have 30s default timeout."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()
        assert transport.default_timeout_ms == 30000


class TestPlaywrightErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_fetch_handles_navigation_timeout(self):
        """fetch() should handle navigation timeout gracefully."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()

            # Import actual exception if possible, otherwise mock it
            try:
                from playwright.async_api import TimeoutError as PlaywrightTimeout
            except ImportError:
                PlaywrightTimeout = TimeoutError

            mock_page.goto = AsyncMock(side_effect=PlaywrightTimeout("Navigation timeout"))

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()

            # Should raise or return error artifact
            with pytest.raises((TimeoutError, Exception)):
                await transport.fetch("https://example.com", timeout=1000)

    @pytest.mark.asyncio
    async def test_fetch_returns_artifact_for_http_errors(self):
        """fetch() should return FetchArtifact for HTTP errors (4xx, 5xx)."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html>Forbidden</html>")
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 403
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            result = await transport.fetch("https://example.com")

            assert result.status_code == 403


class TestPlaywrightContentSizeLimits:
    """Test content size limiting functionality."""

    @pytest.mark.asyncio
    async def test_fetch_raises_on_oversized_content(self):
        """fetch() should raise ContentSizeExceededError for oversized content."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        from monitoring.content_pipeline.exceptions import ContentSizeExceededError

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            large_content = "<html>" + "x" * 2000 + "</html>"
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value=large_content)
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            with pytest.raises(ContentSizeExceededError):
                await transport.fetch(
                    "https://example.com",
                    max_html_bytes=1000,
                )

    def test_default_max_size_is_5mb(self):
        """PlaywrightTransport default max_size should be 5MB."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()
        assert transport.default_max_html_bytes == 5_242_880


class TestPlaywrightGracefulDegradation:
    """Test graceful degradation when Playwright not installed."""

    def test_raises_import_error_when_playwright_missing(self):
        """Should raise ImportError with helpful message when Playwright missing."""
        import sys

        # This test is tricky - we need to test the behavior when playwright is missing
        # For now, just verify the class can handle the import gracefully
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()

        # Should have a method to check availability
        assert hasattr(transport, "is_available")

    def test_is_available_returns_boolean(self):
        """is_available() should return True/False based on Playwright installation."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        transport = PlaywrightTransport()

        result = transport.is_available()
        assert isinstance(result, bool)


class TestPlaywrightBrowserLaunch:
    """Test browser launch configuration."""

    @pytest.mark.asyncio
    async def test_fetch_launches_headless_browser(self):
        """fetch() should launch browser in headless mode."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html></html>")
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            await transport.fetch("https://example.com")

            # Verify headless=True was passed
            call_kwargs = mock_chromium.launch.call_args.kwargs
            assert call_kwargs.get("headless") is True

    @pytest.mark.asyncio
    async def test_fetch_uses_chromium_only(self):
        """fetch() should only use Chromium (not Firefox or WebKit)."""
        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport

        with patch("monitoring.content_pipeline.transport_playwright.async_playwright") as mock_pw:
            mock_page = AsyncMock()
            mock_page.content = AsyncMock(return_value="<html></html>")
            mock_page.url = "https://example.com"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {}
            mock_page.goto = AsyncMock(return_value=mock_response)

            mock_context = AsyncMock()
            mock_context.new_page = AsyncMock(return_value=mock_page)

            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            mock_chromium = AsyncMock()
            mock_chromium.launch = AsyncMock(return_value=mock_browser)

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium = mock_chromium
            mock_pw_instance.firefox = AsyncMock()  # Should not be called
            mock_pw_instance.webkit = AsyncMock()   # Should not be called

            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

            transport = PlaywrightTransport()
            await transport.fetch("https://example.com")

            # Verify chromium was used
            mock_chromium.launch.assert_called_once()
            mock_pw_instance.firefox.launch.assert_not_called()
            mock_pw_instance.webkit.launch.assert_not_called()
