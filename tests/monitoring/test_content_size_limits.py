"""Tests for content size limits in HttpxTransport.

Tests cover:
- ContentSizeExceededError exception
- Content-Type based size limits (HTML vs JSON)
- Streaming size enforcement
- Content-Length header early rejection
- Truncation handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from monitoring.content_pipeline.exceptions import ContentSizeExceededError
from monitoring.content_pipeline.transport_httpx import HttpxTransport
from monitoring.content_pipeline.config import TransportConfig


class TestContentSizeExceededError:
    """Tests for the ContentSizeExceededError exception."""

    def test_exception_stores_url(self):
        """Exception should store the URL that caused the error."""
        exc = ContentSizeExceededError(
            url="https://example.com/large",
            max_size=1000,
            actual_size=5000,
        )
        assert exc.url == "https://example.com/large"

    def test_exception_stores_max_size(self):
        """Exception should store the maximum allowed size."""
        exc = ContentSizeExceededError(
            url="https://example.com/large",
            max_size=1000,
            actual_size=5000,
        )
        assert exc.max_size == 1000

    def test_exception_stores_actual_size(self):
        """Exception should store the actual size (if known)."""
        exc = ContentSizeExceededError(
            url="https://example.com/large",
            max_size=1000,
            actual_size=5000,
        )
        assert exc.actual_size == 5000

    def test_exception_allows_none_actual_size(self):
        """Exception should allow None for actual_size when unknown."""
        exc = ContentSizeExceededError(
            url="https://example.com/large",
            max_size=1000,
            actual_size=None,
        )
        assert exc.actual_size is None

    def test_exception_message_with_actual_size(self):
        """Exception should have a clear message when actual size is known."""
        exc = ContentSizeExceededError(
            url="https://example.com/large",
            max_size=1000,
            actual_size=5000,
        )
        msg = str(exc)
        assert "https://example.com/large" in msg
        assert "1000" in msg
        assert "5000" in msg

    def test_exception_message_without_actual_size(self):
        """Exception should have a clear message when actual size is unknown."""
        exc = ContentSizeExceededError(
            url="https://example.com/large",
            max_size=1000,
            actual_size=None,
        )
        msg = str(exc)
        assert "https://example.com/large" in msg
        assert "1000" in msg

    def test_exception_is_subclass_of_exception(self):
        """ContentSizeExceededError should be a proper Exception subclass."""
        exc = ContentSizeExceededError(
            url="https://example.com",
            max_size=1000,
        )
        assert isinstance(exc, Exception)


class TestTransportConfigContentLimits:
    """Tests for content limit configuration in TransportConfig."""

    def test_default_max_html_bytes_is_5mb(self):
        """Default max_html_bytes should be 5MB."""
        config = TransportConfig()
        assert config.max_html_bytes == 5_242_880

    def test_default_max_json_bytes_is_2mb(self):
        """Default max_json_bytes should be 2MB."""
        config = TransportConfig()
        assert config.max_json_bytes == 2_097_152

    def test_custom_max_html_bytes(self):
        """Should allow custom max_html_bytes."""
        config = TransportConfig(max_html_bytes=10_000_000)
        assert config.max_html_bytes == 10_000_000

    def test_custom_max_json_bytes(self):
        """Should allow custom max_json_bytes."""
        config = TransportConfig(max_json_bytes=1_000_000)
        assert config.max_json_bytes == 1_000_000

    def test_to_dict_includes_content_limits(self):
        """to_dict should include content limit values."""
        config = TransportConfig(max_html_bytes=1000, max_json_bytes=500)
        d = config.to_dict()
        assert d["max_html_bytes"] == 1000
        assert d["max_json_bytes"] == 500

    def test_from_dict_parses_content_limits(self):
        """from_dict should parse content limit values."""
        d = {
            "initial": "httpx",
            "max_html_bytes": 3000,
            "max_json_bytes": 1500,
        }
        config = TransportConfig.from_dict(d)
        assert config.max_html_bytes == 3000
        assert config.max_json_bytes == 1500

    def test_from_dict_uses_defaults_when_missing(self):
        """from_dict should use defaults when limits are not in dict."""
        d = {"initial": "httpx"}
        config = TransportConfig.from_dict(d)
        assert config.max_html_bytes == 5_242_880
        assert config.max_json_bytes == 2_097_152


class TestFetchArtifactTruncated:
    """Tests for the truncated field on FetchArtifact."""

    def test_fetch_artifact_truncated_defaults_to_false(self):
        """FetchArtifact.truncated should default to False."""
        from monitoring.content_pipeline.models import FetchArtifact

        artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="hello",
        )
        assert artifact.truncated is False

    def test_fetch_artifact_truncated_can_be_true(self):
        """FetchArtifact.truncated should be settable to True."""
        from monitoring.content_pipeline.models import FetchArtifact

        artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="hello",
            truncated=True,
        )
        assert artifact.truncated is True

    def test_fetch_artifact_to_dict_includes_truncated(self):
        """FetchArtifact.to_dict should include truncated field."""
        from monitoring.content_pipeline.models import FetchArtifact

        artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="hello",
            truncated=True,
        )
        d = artifact.to_dict()
        assert "truncated" in d
        assert d["truncated"] is True


class TestContentTypeBasedLimits:
    """Tests for Content-Type based size limits."""

    @pytest.mark.asyncio
    async def test_json_content_type_uses_json_limit(self):
        """Content-Type containing 'json' should use max_json_bytes limit."""
        # Content exceeds 2MB JSON limit but would be under 5MB HTML limit
        large_json = '{"data": "' + "x" * 3_000_000 + '"}'

        async def mock_stream():
            yield large_json.encode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "application/json",
            "content-length": str(len(large_json)),
        }
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError) as exc_info:
                await transport.fetch("https://example.com/api/data")

            assert exc_info.value.max_size == 2_097_152  # JSON limit

    @pytest.mark.asyncio
    async def test_html_content_type_uses_html_limit(self):
        """Content-Type text/html should use max_html_bytes limit."""
        # Content exceeds 5MB HTML limit
        large_html = "<html>" + "x" * 6_000_000 + "</html>"

        async def mock_stream():
            yield large_html.encode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "text/html; charset=utf-8",
            "content-length": str(len(large_html)),
        }
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError) as exc_info:
                await transport.fetch("https://example.com/page")

            assert exc_info.value.max_size == 5_242_880  # HTML limit

    @pytest.mark.asyncio
    async def test_unknown_content_type_uses_html_limit(self):
        """Unknown Content-Type should default to max_html_bytes limit."""
        transport = HttpxTransport()
        # Default behavior - unknown types use HTML limit
        assert transport.default_max_size == 5_242_880


class TestContentLengthHeaderEarlyRejection:
    """Tests for early rejection based on Content-Length header."""

    @pytest.mark.asyncio
    async def test_large_content_length_rejected_early_for_json(self):
        """Should reject early if Content-Length exceeds JSON limit."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "application/json",
            "content-length": "10000000",  # 10MB - exceeds 2MB JSON limit
        }
        # Should not call aiter_bytes if we reject early
        mock_response.aiter_bytes = MagicMock(side_effect=AssertionError("Should not read body"))
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError) as exc_info:
                await transport.fetch("https://example.com/api/data")

            assert exc_info.value.actual_size == 10000000

    @pytest.mark.asyncio
    async def test_large_content_length_rejected_early_for_html(self):
        """Should reject early if Content-Length exceeds HTML limit."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "text/html",
            "content-length": "10000000",  # 10MB - exceeds 5MB HTML limit
        }
        mock_response.aiter_bytes = MagicMock(side_effect=AssertionError("Should not read body"))
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError) as exc_info:
                await transport.fetch("https://example.com/page")

            assert exc_info.value.actual_size == 10000000


class TestStreamingSizeLimits:
    """Tests for streaming-based size enforcement."""

    @pytest.mark.asyncio
    async def test_streaming_stops_at_limit(self):
        """Should stop reading stream when limit is reached."""
        # Create chunks that exceed the limit when combined
        chunk_size = 500_000  # 500KB per chunk
        chunks_read = []

        async def mock_stream():
            for i in range(20):  # Would be 10MB total
                chunk = b"x" * chunk_size
                chunks_read.append(i)
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}  # 2MB limit
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError):
                await transport.fetch("https://example.com/api/data")

            # Should have stopped reading after ~5 chunks (2.5MB > 2MB limit)
            # The exact number depends on implementation, but should be < 10
            assert len(chunks_read) < 10

    @pytest.mark.asyncio
    async def test_content_under_limit_not_truncated(self):
        """Content under limit should not be marked as truncated."""
        small_content = b"Hello, World!"

        async def mock_stream():
            yield small_content

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.encoding = "utf-8"
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            result = await transport.fetch("https://example.com")

            assert result.content == "Hello, World!"
            assert result.truncated is False


class TestCustomConfigLimits:
    """Tests for using TransportConfig to set custom limits."""

    @pytest.mark.asyncio
    async def test_fetch_with_transport_config(self):
        """Should respect limits from TransportConfig."""
        config = TransportConfig(max_html_bytes=1000, max_json_bytes=500)

        large_html = "x" * 2000  # Exceeds custom 1KB limit

        async def mock_stream():
            yield large_html.encode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "text/html",
            "content-length": "2000",
        }
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            transport = HttpxTransport()
            with pytest.raises(ContentSizeExceededError) as exc_info:
                await transport.fetch(
                    "https://example.com",
                    max_html_bytes=config.max_html_bytes,
                    max_json_bytes=config.max_json_bytes,
                )

            assert exc_info.value.max_size == 1000


class TestExceptionErrorDetails:
    """Tests for exception providing useful error details."""

    def test_exception_url_accessible(self):
        """Exception should expose URL for error handlers."""
        exc = ContentSizeExceededError(
            url="https://api.example.com/huge-response",
            max_size=2_097_152,
            actual_size=50_000_000,
        )
        assert exc.url == "https://api.example.com/huge-response"

    def test_exception_provides_size_comparison(self):
        """Exception message should help understand the size issue."""
        exc = ContentSizeExceededError(
            url="https://example.com",
            max_size=2_097_152,
            actual_size=50_000_000,
        )
        msg = str(exc)
        # Should contain information about both sizes for debugging
        assert "2097152" in msg or "2MB" in msg.lower() or "2,097,152" in msg
        assert "50000000" in msg or "50MB" in msg.lower() or "50,000,000" in msg
