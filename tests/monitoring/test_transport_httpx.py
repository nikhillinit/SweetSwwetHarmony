"""Tests for HttpxTransport with conditional request support.

Tests cover:
- Basic fetch functionality
- Conditional request headers (ETag, Last-Modified)
- 304 Not Modified response handling
- Error handling (timeout, network errors)
- Content size limits
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from monitoring.content_pipeline.transport_httpx import HttpxTransport
from monitoring.content_pipeline.models import FetchArtifact


def make_mock_response(
    status_code: int = 200,
    content: str = "content",
    headers: dict = None,
    encoding: str = "utf-8",
):
    """Helper to create a mock streaming response."""
    headers = headers or {}
    content_bytes = content.encode(encoding)

    async def mock_stream():
        yield content_bytes

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers = headers
    mock_response.encoding = encoding
    mock_response.aiter_bytes = mock_stream

    return mock_response


def setup_mock_client(mock_client_class, mock_response):
    """Helper to setup the mock client with streaming context."""
    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=AsyncMock())
    mock_client.stream.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client_class.return_value = mock_client
    return mock_client


class TestBasicFetch:
    """Test basic HTTP fetch functionality."""

    @pytest.mark.asyncio
    async def test_fetch_returns_fetch_artifact(self):
        """fetch() should return a FetchArtifact on success."""
        mock_response = make_mock_response(
            status_code=200,
            content="<html><body>Hello</body></html>",
            headers={"content-type": "text/html"},
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert isinstance(result, FetchArtifact)
            assert result.url == "https://example.com"
            assert result.status_code == 200
            assert result.content == "<html><body>Hello</body></html>"

    @pytest.mark.asyncio
    async def test_fetch_extracts_headers(self):
        """fetch() should extract and lowercase header names."""
        mock_response = make_mock_response(
            status_code=200,
            content="content",
            headers={
                "Content-Type": "text/html",
                "X-Custom-Header": "value",
            },
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            # Headers should be lowercase
            assert "content-type" in result.headers
            assert "x-custom-header" in result.headers

    @pytest.mark.asyncio
    async def test_fetch_records_timing(self):
        """fetch() should record fetch_time_ms."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert result.fetch_time_ms >= 0
            assert result.transport_used == "httpx"


class TestConditionalRequests:
    """Test conditional request header support."""

    @pytest.mark.asyncio
    async def test_fetch_sends_if_none_match_header(self):
        """fetch() with etag should send If-None-Match header."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            await transport.fetch("https://example.com", etag='"abc123"')

            # Verify If-None-Match was sent via stream call
            call_args = mock_client.stream.call_args
            headers = call_args.kwargs.get("headers", {})
            assert headers.get("If-None-Match") == '"abc123"'

    @pytest.mark.asyncio
    async def test_fetch_sends_if_modified_since_header(self):
        """fetch() with last_modified should send If-Modified-Since header."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            await transport.fetch(
                "https://example.com",
                last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
            )

            # Verify If-Modified-Since was sent
            call_args = mock_client.stream.call_args
            headers = call_args.kwargs.get("headers", {})
            assert headers.get("If-Modified-Since") == "Wed, 21 Oct 2025 07:28:00 GMT"

    @pytest.mark.asyncio
    async def test_fetch_sends_both_conditional_headers(self):
        """fetch() with both etag and last_modified sends both headers."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            await transport.fetch(
                "https://example.com",
                etag='"xyz789"',
                last_modified="Thu, 22 Oct 2025 08:00:00 GMT",
            )

            call_args = mock_client.stream.call_args
            headers = call_args.kwargs.get("headers", {})
            assert headers.get("If-None-Match") == '"xyz789"'
            assert headers.get("If-Modified-Since") == "Thu, 22 Oct 2025 08:00:00 GMT"

    @pytest.mark.asyncio
    async def test_fetch_extracts_etag_from_response(self):
        """fetch() should extract ETag from response headers."""
        mock_response = make_mock_response(
            status_code=200,
            content="content",
            headers={"ETag": '"new-etag-value"'},
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert result.etag == '"new-etag-value"'

    @pytest.mark.asyncio
    async def test_fetch_extracts_last_modified_from_response(self):
        """fetch() should extract Last-Modified from response headers."""
        mock_response = make_mock_response(
            status_code=200,
            content="content",
            headers={"Last-Modified": "Fri, 23 Oct 2025 09:00:00 GMT"},
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert result.last_modified == "Fri, 23 Oct 2025 09:00:00 GMT"


class TestNotModifiedResponse:
    """Test 304 Not Modified response handling."""

    @pytest.mark.asyncio
    async def test_fetch_handles_304_response(self):
        """fetch() should handle 304 Not Modified correctly."""
        mock_response = make_mock_response(
            status_code=304,
            content="",  # 304 has no body
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com", etag='"existing"')

            assert result.status_code == 304
            assert result.content == ""
            assert result.is_not_modified is True

    @pytest.mark.asyncio
    async def test_fetch_304_preserves_etag_from_response(self):
        """304 response may include new ETag, which should be captured."""
        mock_response = make_mock_response(
            status_code=304,
            content="",
            headers={"ETag": '"updated-etag"'},
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com", etag='"old-etag"')

            # Should capture updated etag from 304 response
            assert result.etag == '"updated-etag"'

    @pytest.mark.asyncio
    async def test_200_response_is_not_marked_as_not_modified(self):
        """200 response should have is_not_modified=False."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert result.is_not_modified is False


class TestErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_fetch_handles_timeout(self):
        """fetch() should handle timeout errors gracefully."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            # Make stream raise timeout exception
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()

            with pytest.raises(httpx.TimeoutException):
                await transport.fetch("https://example.com", timeout=1.0)

    @pytest.mark.asyncio
    async def test_fetch_handles_connection_error(self):
        """fetch() should propagate connection errors."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            # Make stream raise connect error
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()

            with pytest.raises(httpx.ConnectError):
                await transport.fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_fetch_handles_http_status_errors(self):
        """fetch() should return FetchArtifact for HTTP errors (4xx, 5xx)."""
        mock_response = make_mock_response(status_code=404, content="Not Found")

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert result.status_code == 404
            assert result.content == "Not Found"


class TestContentSizeLimits:
    """Test content size limiting functionality."""

    @pytest.mark.asyncio
    async def test_fetch_raises_on_oversized_content(self):
        """fetch() should raise ContentSizeExceededError for oversized content."""
        from monitoring.content_pipeline.exceptions import ContentSizeExceededError

        # Create content that exceeds custom limit
        large_content = "x" * 2000
        mock_response = make_mock_response(
            status_code=200,
            content=large_content,
            headers={"content-type": "text/html"},
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError):
                await transport.fetch(
                    "https://example.com",
                    max_html_bytes=1000,
                )

    @pytest.mark.asyncio
    async def test_fetch_default_max_size_is_5mb(self):
        """fetch() default max_size should be 5MB."""
        transport = HttpxTransport()
        # 5MB = 5 * 1024 * 1024 = 5242880
        assert transport.default_max_size == 5_242_880


class TestCustomTimeout:
    """Test custom timeout configuration."""

    @pytest.mark.asyncio
    async def test_fetch_uses_custom_timeout(self):
        """fetch() should pass custom timeout to httpx."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            await transport.fetch("https://example.com", timeout=15.0)

            # Verify timeout was passed to stream
            call_args = mock_client.stream.call_args
            assert call_args.kwargs.get("timeout") == 15.0


class TestUserAgent:
    """Test User-Agent header configuration."""

    @pytest.mark.asyncio
    async def test_fetch_sends_user_agent(self):
        """fetch() should send a reasonable User-Agent header."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            await transport.fetch("https://example.com")

            # Verify User-Agent was sent
            call_args = mock_client.stream.call_args
            headers = call_args.kwargs.get("headers", {})
            assert "User-Agent" in headers
            assert "DiscoveryEngine" in headers["User-Agent"]


class TestHTTP2Support:
    """Test HTTP/2 support configuration."""

    @pytest.mark.asyncio
    async def test_fetch_uses_http2_by_default(self):
        """fetch() should use HTTP/2 by default."""
        mock_response = make_mock_response(status_code=200, content="content")

        with patch("httpx.AsyncClient") as mock_client_class:
            setup_mock_client(mock_client_class, mock_response)

            transport = HttpxTransport()
            await transport.fetch("https://example.com")

            # Verify http2=True was passed to AsyncClient
            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("http2") is True

    def test_http2_enabled_attribute(self):
        """HttpxTransport should have http2_enabled=True by default."""
        transport = HttpxTransport()
        assert hasattr(transport, "http2_enabled")
        assert transport.http2_enabled is True
