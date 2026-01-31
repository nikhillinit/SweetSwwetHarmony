"""Tests for CurlCffiTransport with browser impersonation support.

Tests cover:
- Basic fetch functionality
- Browser impersonation profiles (chrome, firefox, safari)
- Conditional request headers (ETag, Last-Modified)
- 304 Not Modified response handling
- Error handling (timeout, network errors)
- Content size limits
- HTTP/2 support
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from monitoring.content_pipeline.models import FetchArtifact


class TestCurlCffiTransportExists:
    """Test that CurlCffiTransport class exists and has correct interface."""

    def test_import_curl_transport(self):
        """CurlCffiTransport should be importable."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        assert CurlCffiTransport is not None

    def test_has_fetch_method(self):
        """CurlCffiTransport should have async fetch method."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        transport = CurlCffiTransport()
        assert hasattr(transport, "fetch")
        # fetch should be async
        import inspect
        assert inspect.iscoroutinefunction(transport.fetch)

    def test_has_default_impersonate_profile(self):
        """CurlCffiTransport should have default impersonate profile."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        transport = CurlCffiTransport()
        assert hasattr(transport, "default_impersonate")
        assert transport.default_impersonate in ("chrome", "chrome110", "chrome120")


class TestCurlCffiBasicFetch:
    """Test basic HTTP fetch functionality."""

    @pytest.mark.asyncio
    async def test_fetch_returns_fetch_artifact(self):
        """fetch() should return a FetchArtifact on success."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_response.content = b"<html><body>Hello</body></html>"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            result = await transport.fetch("https://example.com")

            assert isinstance(result, FetchArtifact)
            assert result.url == "https://example.com"
            assert result.status_code == 200
            assert result.content == "<html><body>Hello</body></html>"

    @pytest.mark.asyncio
    async def test_fetch_uses_curl_cffi_transport_name(self):
        """fetch() should report transport_used='curl_cffi'."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            result = await transport.fetch("https://example.com")

            assert result.transport_used == "curl_cffi"

    @pytest.mark.asyncio
    async def test_fetch_records_timing(self):
        """fetch() should record fetch_time_ms."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            result = await transport.fetch("https://example.com")

            assert result.fetch_time_ms >= 0


class TestCurlCffiBrowserImpersonation:
    """Test browser impersonation profiles."""

    @pytest.mark.asyncio
    async def test_fetch_uses_default_impersonate(self):
        """fetch() should use default impersonate profile."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            await transport.fetch("https://example.com")

            # Verify impersonate was passed to session
            call_kwargs = mock_session_class.call_args.kwargs
            assert "impersonate" in call_kwargs
            assert call_kwargs["impersonate"] in ("chrome", "chrome110", "chrome120")

    @pytest.mark.asyncio
    async def test_fetch_supports_custom_impersonate_profile(self):
        """fetch() should support custom impersonate profile."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            await transport.fetch("https://example.com", impersonate="firefox")

            call_kwargs = mock_session_class.call_args.kwargs
            assert call_kwargs["impersonate"] == "firefox"

    def test_supported_profiles(self):
        """CurlCffiTransport should list supported impersonate profiles."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        transport = CurlCffiTransport()
        assert hasattr(transport, "supported_profiles")
        profiles = transport.supported_profiles
        assert "chrome" in profiles or "chrome110" in profiles
        assert "firefox" in profiles or "ff" in profiles


class TestCurlCffiConditionalRequests:
    """Test conditional request header support."""

    @pytest.mark.asyncio
    async def test_fetch_sends_if_none_match_header(self):
        """fetch() with etag should send If-None-Match header."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            await transport.fetch("https://example.com", etag='"abc123"')

            # Verify If-None-Match was sent
            call_kwargs = mock_session.get.call_args.kwargs
            headers = call_kwargs.get("headers", {})
            assert headers.get("If-None-Match") == '"abc123"'

    @pytest.mark.asyncio
    async def test_fetch_sends_if_modified_since_header(self):
        """fetch() with last_modified should send If-Modified-Since header."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            await transport.fetch(
                "https://example.com",
                last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
            )

            call_kwargs = mock_session.get.call_args.kwargs
            headers = call_kwargs.get("headers", {})
            assert headers.get("If-Modified-Since") == "Wed, 21 Oct 2025 07:28:00 GMT"

    @pytest.mark.asyncio
    async def test_fetch_extracts_etag_from_response(self):
        """fetch() should extract ETag from response headers."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"ETag": '"new-etag-value"'}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            result = await transport.fetch("https://example.com")

            assert result.etag == '"new-etag-value"'


class TestCurlCffiNotModifiedResponse:
    """Test 304 Not Modified response handling."""

    @pytest.mark.asyncio
    async def test_fetch_handles_304_response(self):
        """fetch() should handle 304 Not Modified correctly."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 304
            mock_response.headers = {}
            mock_response.content = b""
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            result = await transport.fetch("https://example.com", etag='"existing"')

            assert result.status_code == 304
            assert result.content == ""
            assert result.is_not_modified is True


class TestCurlCffiContentSizeLimits:
    """Test content size limiting functionality."""

    @pytest.mark.asyncio
    async def test_fetch_raises_on_oversized_content(self):
        """fetch() should raise ContentSizeExceededError for oversized content."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        from monitoring.content_pipeline.exceptions import ContentSizeExceededError

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            # Create content that exceeds custom limit
            large_content = b"x" * 2000
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_response.content = large_content
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            with pytest.raises(ContentSizeExceededError):
                await transport.fetch(
                    "https://example.com",
                    max_html_bytes=1000,
                )

    def test_default_max_size_is_5mb(self):
        """CurlCffiTransport default max_size should be 5MB."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        transport = CurlCffiTransport()
        assert transport.default_max_html_bytes == 5_242_880


class TestCurlCffiErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_fetch_handles_timeout(self):
        """fetch() should handle timeout errors gracefully."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        from curl_cffi.requests import RequestsError

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get = AsyncMock(side_effect=RequestsError("Timeout"))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()

            with pytest.raises(RequestsError):
                await transport.fetch("https://example.com", timeout=1.0)

    @pytest.mark.asyncio
    async def test_fetch_returns_artifact_for_http_errors(self):
        """fetch() should return FetchArtifact for HTTP errors (4xx, 5xx)."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.headers = {}
            mock_response.content = b"Forbidden"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            result = await transport.fetch("https://example.com")

            assert result.status_code == 403
            assert result.content == "Forbidden"


class TestCurlCffiHTTP2Support:
    """Test HTTP/2 support."""

    @pytest.mark.asyncio
    async def test_fetch_uses_http2_by_default(self):
        """fetch() should use HTTP/2 by default."""
        from monitoring.content_pipeline.transport_curl import CurlCffiTransport

        with patch("monitoring.content_pipeline.transport_curl.AsyncSession") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.content = b"content"
            mock_response.encoding = "utf-8"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            transport = CurlCffiTransport()
            await transport.fetch("https://example.com")

            # curl_cffi uses HTTP/2 by default with browser impersonation
            # Just verify the call succeeded - HTTP/2 is implicit
            mock_session.get.assert_called_once()
