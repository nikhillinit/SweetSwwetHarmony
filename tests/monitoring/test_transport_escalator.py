"""Tests for TransportEscalator with automatic fallback logic.

Tests cover:
- Basic fetch through primary transport
- Escalation on 403 Forbidden
- Escalation on 429 Too Many Requests
- Escalation on blocked response patterns
- No escalation when not configured
- Transport usage tracking
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from monitoring.content_pipeline.models import FetchArtifact
from monitoring.content_pipeline.config import TransportConfig


class TestTransportEscalatorExists:
    """Test that TransportEscalator exists and has correct interface."""

    def test_import_transport_escalator(self):
        """TransportEscalator should be importable."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator
        assert TransportEscalator is not None

    def test_has_fetch_method(self):
        """TransportEscalator should have async fetch method."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator
        escalator = TransportEscalator()
        assert hasattr(escalator, "fetch")
        import inspect
        assert inspect.iscoroutinefunction(escalator.fetch)

    def test_accepts_transport_config(self):
        """TransportEscalator should accept TransportConfig."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator
        config = TransportConfig(on_403="curl_cffi")
        escalator = TransportEscalator(config=config)
        assert escalator._config == config


class TestTransportEscalatorBasicFetch:
    """Test basic fetch through primary transport."""

    @pytest.mark.asyncio
    async def test_fetch_uses_primary_transport_on_success(self):
        """fetch() should use primary transport when successful."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html>content</html>",
            transport_used="httpx",
            fetch_time_ms=100,
        ))

        escalator = TransportEscalator(primary_transport=mock_primary)
        result = await escalator.fetch("https://example.com")

        assert result.status_code == 200
        assert result.transport_used == "httpx"
        mock_primary.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_returns_fetch_artifact(self):
        """fetch() should return a FetchArtifact."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="content",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        escalator = TransportEscalator(primary_transport=mock_primary)
        result = await escalator.fetch("https://example.com")

        assert isinstance(result, FetchArtifact)


class TestTransportEscalatorOn403:
    """Test escalation on 403 Forbidden."""

    @pytest.mark.asyncio
    async def test_escalates_on_403_when_configured(self):
        """fetch() should escalate to fallback on 403 when on_403 is set."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html>success</html>",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_403="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        # Should have tried primary, then fallback
        mock_primary.fetch.assert_called_once()
        mock_fallback.fetch.assert_called_once()
        assert result.status_code == 200
        assert result.transport_used == "curl_cffi"

    @pytest.mark.asyncio
    async def test_no_escalation_on_403_when_not_configured(self):
        """fetch() should NOT escalate on 403 when on_403 is None."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock()

        config = TransportConfig(on_403=None)  # Not configured
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        # Should NOT have tried fallback
        mock_primary.fetch.assert_called_once()
        mock_fallback.fetch.assert_not_called()
        assert result.status_code == 403


class TestTransportEscalatorOn429:
    """Test escalation on 429 Too Many Requests."""

    @pytest.mark.asyncio
    async def test_escalates_on_429_when_configured(self):
        """fetch() should escalate to fallback on 429 when on_429 is set."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=429,
            headers={},
            content="Too Many Requests",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html>success</html>",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_429="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        mock_primary.fetch.assert_called_once()
        mock_fallback.fetch.assert_called_once()
        assert result.status_code == 200
        assert result.transport_used == "curl_cffi"

    @pytest.mark.asyncio
    async def test_no_escalation_on_429_when_not_configured(self):
        """fetch() should NOT escalate on 429 when on_429 is None."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=429,
            headers={},
            content="Too Many Requests",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()

        config = TransportConfig(on_429=None)
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        mock_primary.fetch.assert_called_once()
        mock_fallback.fetch.assert_not_called()
        assert result.status_code == 429


class TestTransportEscalatorBlockedPatterns:
    """Test escalation on blocked response body patterns."""

    @pytest.mark.asyncio
    async def test_escalates_on_cloudflare_challenge(self):
        """fetch() should escalate when response contains Cloudflare challenge."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        cloudflare_content = """
        <html>
        <head><title>Just a moment...</title></head>
        <body>
        <h1>Checking your browser before accessing</h1>
        <p>cf-browser-verification</p>
        </body>
        </html>
        """

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,  # Cloudflare often returns 200
            headers={},
            content=cloudflare_content,
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html>real content</html>",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_403="curl_cffi")  # Enable escalation
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        # Should have escalated due to Cloudflare challenge pattern
        mock_fallback.fetch.assert_called_once()
        assert result.transport_used == "curl_cffi"

    @pytest.mark.asyncio
    async def test_escalates_on_bot_detection(self):
        """fetch() should escalate when response contains bot detection."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        bot_detection_content = """
        <html>
        <body>
        <h1>Access Denied</h1>
        <p>We have detected unusual traffic from your computer.</p>
        </body>
        </html>
        """

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content=bot_detection_content,
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html>real content</html>",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_403="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        mock_fallback.fetch.assert_called_once()
        assert result.transport_used == "curl_cffi"


class TestTransportEscalatorPassesParameters:
    """Test that escalator passes all fetch parameters correctly."""

    @pytest.mark.asyncio
    async def test_passes_conditional_request_headers(self):
        """fetch() should pass etag and last_modified to transport."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="content",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        escalator = TransportEscalator(primary_transport=mock_primary)
        await escalator.fetch(
            "https://example.com",
            etag='"abc123"',
            last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
        )

        call_kwargs = mock_primary.fetch.call_args.kwargs
        assert call_kwargs.get("etag") == '"abc123"'
        assert call_kwargs.get("last_modified") == "Wed, 21 Oct 2025 07:28:00 GMT"

    @pytest.mark.asyncio
    async def test_passes_size_limits(self):
        """fetch() should pass max_html_bytes and max_json_bytes to transport."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="content",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        escalator = TransportEscalator(primary_transport=mock_primary)
        await escalator.fetch(
            "https://example.com",
            max_html_bytes=1_000_000,
            max_json_bytes=500_000,
        )

        call_kwargs = mock_primary.fetch.call_args.kwargs
        assert call_kwargs.get("max_html_bytes") == 1_000_000
        assert call_kwargs.get("max_json_bytes") == 500_000


class TestTransportEscalatorFallbackFails:
    """Test behavior when fallback also fails."""

    @pytest.mark.asyncio
    async def test_returns_fallback_result_when_fallback_fails(self):
        """fetch() should return fallback result even if it fails."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=403,
            headers={},
            content="Still Forbidden",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_403="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        result = await escalator.fetch("https://example.com")

        # Should return fallback result even if it also failed
        assert result.status_code == 403
        assert result.transport_used == "curl_cffi"
        assert result.content == "Still Forbidden"


class TestTransportEscalatorUserAgentProfile:
    """Test that user_agent_profile is passed to curl_cffi transport."""

    @pytest.mark.asyncio
    async def test_passes_user_agent_profile_to_fallback(self):
        """fetch() should pass user_agent_profile as impersonate to fallback."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="success",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_403="curl_cffi", user_agent_profile="firefox")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )
        await escalator.fetch("https://example.com")

        # Verify impersonate was passed to fallback
        call_kwargs = mock_fallback.fetch.call_args.kwargs
        assert call_kwargs.get("impersonate") == "firefox"


class TestBlockedSiteRecovery:
    """Comprehensive tests for blocked site recovery scenarios.

    These tests verify that the escalation system correctly recovers
    from various types of blocking:
    - 403 Forbidden → curl_cffi succeeds
    - 429 Rate Limited → curl_cffi succeeds
    - Cloudflare challenge → curl_cffi succeeds
    - curl_cffi only used when needed (not preemptively)
    """

    @pytest.mark.asyncio
    async def test_recovery_from_403_forbidden(self):
        """Should recover from 403 by escalating to curl_cffi."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        # Primary transport gets 403
        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=403,
            headers={},
            content="<html><body>403 Forbidden - Access Denied</body></html>",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        # Fallback transport succeeds
        expected_content = "<html><body><h1>Welcome!</h1><p>Real content here.</p></body></html>"
        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=expected_content,
            transport_used="curl_cffi",
            fetch_time_ms=150,
        ))

        config = TransportConfig(on_403="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        result = await escalator.fetch("https://protected.com")

        # Should have recovered with curl_cffi
        assert result.status_code == 200
        assert result.transport_used == "curl_cffi"
        assert "Welcome!" in result.content

    @pytest.mark.asyncio
    async def test_recovery_from_429_rate_limited(self):
        """Should recover from 429 by escalating to curl_cffi."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://api.example.com",
            status_code=429,
            headers={"retry-after": "60"},
            content="Rate limit exceeded. Please try again later.",
            transport_used="httpx",
            fetch_time_ms=30,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://api.example.com",
            status_code=200,
            headers={"content-type": "application/json"},
            content='{"data": "success"}',
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_429="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        result = await escalator.fetch("https://api.example.com")

        assert result.status_code == 200
        assert result.transport_used == "curl_cffi"
        assert "success" in result.content

    @pytest.mark.asyncio
    async def test_recovery_from_cloudflare_challenge(self):
        """Should recover from Cloudflare challenge page by escalating."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        cloudflare_page = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Just a moment...</title>
        </head>
        <body>
            <div id="cf-content">
                <h1>Checking your browser before accessing</h1>
                <p>This process is automatic. Please wait...</p>
                <noscript>Enable JavaScript and cookies to continue.</noscript>
                <span data-translate="cf-browser-verification"></span>
            </div>
        </body>
        </html>
        """

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected-site.com",
            status_code=200,  # Cloudflare returns 200 with challenge
            headers={"server": "cloudflare"},
            content=cloudflare_page,
            transport_used="httpx",
            fetch_time_ms=80,
        ))

        real_content = """
        <html>
            <body>
                <h1>Product Details</h1>
                <p>This is the actual product page content.</p>
            </body>
        </html>
        """
        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected-site.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=real_content,
            transport_used="curl_cffi",
            fetch_time_ms=200,
        ))

        config = TransportConfig(on_403="curl_cffi")  # Enable escalation
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        result = await escalator.fetch("https://protected-site.com")

        # Should detect Cloudflare challenge and escalate
        assert result.transport_used == "curl_cffi"
        assert "Product Details" in result.content

    @pytest.mark.asyncio
    async def test_curl_cffi_not_used_when_httpx_succeeds(self):
        """curl_cffi should NOT be used when httpx returns valid content."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        valid_content = """
        <html>
            <body>
                <h1>Normal Page</h1>
                <p>This is regular content that loaded fine.</p>
            </body>
        </html>
        """

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://normal-site.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=valid_content,
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock()  # Should never be called

        config = TransportConfig(on_403="curl_cffi", on_429="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        result = await escalator.fetch("https://normal-site.com")

        # httpx should have succeeded without escalation
        assert result.status_code == 200
        assert result.transport_used == "httpx"
        mock_fallback.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_curl_cffi_not_used_when_escalation_disabled(self):
        """curl_cffi should NOT be used when escalation is not configured."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://blocked-site.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock()  # Should never be called

        # No escalation configured (defaults)
        config = TransportConfig()
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        result = await escalator.fetch("https://blocked-site.com")

        # Should return 403 without escalating
        assert result.status_code == 403
        assert result.transport_used == "httpx"
        mock_fallback.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_preserves_parameters(self):
        """Fallback should receive all original fetch parameters."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://api.example.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://api.example.com",
            status_code=200,
            headers={},
            content="Success",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        config = TransportConfig(on_403="curl_cffi", user_agent_profile="chrome")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        await escalator.fetch(
            url="https://api.example.com",
            etag='"abc123"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
            max_html_bytes=1_000_000,
            max_json_bytes=500_000,
        )

        # Verify all parameters were passed to fallback
        call_kwargs = mock_fallback.fetch.call_args.kwargs
        assert call_kwargs["url"] == "https://api.example.com"
        assert call_kwargs["etag"] == '"abc123"'
        assert call_kwargs["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
        assert call_kwargs["max_html_bytes"] == 1_000_000
        assert call_kwargs["max_json_bytes"] == 500_000
        assert call_kwargs["impersonate"] == "chrome"

    @pytest.mark.asyncio
    async def test_recovery_returns_fallback_result_even_on_failure(self):
        """If fallback also fails, should return fallback result (not primary)."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://blocked.com",
            status_code=403,
            headers={},
            content="Forbidden by WAF",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        # Fallback also gets blocked
        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://blocked.com",
            status_code=403,
            headers={},
            content="Still Forbidden (curl_cffi)",
            transport_used="curl_cffi",
            fetch_time_ms=150,
        ))

        config = TransportConfig(on_403="curl_cffi")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
        )

        result = await escalator.fetch("https://blocked.com")

        # Should return fallback result, even though it also failed
        assert result.status_code == 403
        assert result.transport_used == "curl_cffi"
        assert "curl_cffi" in result.content

    @pytest.mark.asyncio
    async def test_multiple_blocked_patterns_detected(self):
        """Should detect various blocked response patterns."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        blocked_patterns = [
            "Access Denied - You have been blocked",
            "We detected unusual traffic from your computer",
            "Please complete the CAPTCHA below",
            "px-captcha-error",  # PerimeterX
            "datadome-captcha",  # DataDome
        ]

        for pattern in blocked_patterns:
            mock_primary = AsyncMock()
            mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
                url="https://test.com",
                status_code=200,  # Many blockers return 200
                headers={},
                content=f"<html><body>{pattern}</body></html>",
                transport_used="httpx",
                fetch_time_ms=50,
            ))

            mock_fallback = AsyncMock()
            mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
                url="https://test.com",
                status_code=200,
                headers={},
                content="<html><body>Real content</body></html>",
                transport_used="curl_cffi",
                fetch_time_ms=100,
            ))

            config = TransportConfig(on_403="curl_cffi")
            escalator = TransportEscalator(
                config=config,
                primary_transport=mock_primary,
                fallback_transport=mock_fallback,
            )

            result = await escalator.fetch("https://test.com")

            assert result.transport_used == "curl_cffi", f"Failed to detect pattern: {pattern}"


class TestThreeTierEscalation:
    """Test 3-tier escalation: httpx → curl_cffi → playwright.

    When on_blocked is configured and curl_cffi also gets blocked,
    should escalate to playwright as the final fallback.
    """

    @pytest.mark.asyncio
    async def test_escalates_to_playwright_when_curl_also_blocked(self):
        """Should escalate to playwright when both httpx and curl_cffi are blocked."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        # Primary (httpx) gets 403
        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        # Fallback (curl_cffi) also gets blocked
        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=403,
            headers={},
            content="Still Forbidden",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        # Third tier (playwright) succeeds
        mock_third = AsyncMock()
        mock_third.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=200,
            headers={},
            content="<html>Success via Playwright</html>",
            transport_used="playwright",
            fetch_time_ms=500,
        ))

        config = TransportConfig(on_403="curl_cffi", on_blocked="playwright")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
            third_tier_transport=mock_third,
        )

        result = await escalator.fetch("https://protected.com")

        # Should have tried all three
        mock_primary.fetch.assert_called_once()
        mock_fallback.fetch.assert_called_once()
        mock_third.fetch.assert_called_once()
        assert result.status_code == 200
        assert result.transport_used == "playwright"

    @pytest.mark.asyncio
    async def test_escalates_to_playwright_on_blocked_pattern_after_curl(self):
        """Should escalate to playwright when curl_cffi returns blocked pattern."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        # Primary (httpx) gets Cloudflare challenge
        cloudflare_content = "<html>Checking your browser before accessing</html>"
        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://cloudflare-site.com",
            status_code=200,
            headers={},
            content=cloudflare_content,
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        # Fallback (curl_cffi) also gets Cloudflare challenge
        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://cloudflare-site.com",
            status_code=200,
            headers={},
            content=cloudflare_content,  # Still blocked
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        # Third tier (playwright) bypasses
        mock_third = AsyncMock()
        mock_third.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://cloudflare-site.com",
            status_code=200,
            headers={},
            content="<html>Real Content</html>",
            transport_used="playwright",
            fetch_time_ms=500,
        ))

        config = TransportConfig(on_403="curl_cffi", on_blocked="playwright")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
            third_tier_transport=mock_third,
        )

        result = await escalator.fetch("https://cloudflare-site.com")

        assert result.transport_used == "playwright"
        assert "Real Content" in result.content

    @pytest.mark.asyncio
    async def test_no_playwright_when_on_blocked_not_configured(self):
        """Should NOT escalate to playwright when on_blocked is not set."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=403,
            headers={},
            content="Still Forbidden",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        mock_third = AsyncMock()
        mock_third.fetch = AsyncMock()  # Should never be called

        # on_blocked is NOT configured
        config = TransportConfig(on_403="curl_cffi", on_blocked=None)
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
            third_tier_transport=mock_third,
        )

        result = await escalator.fetch("https://protected.com")

        # Should return curl_cffi result without escalating
        mock_third.fetch.assert_not_called()
        assert result.transport_used == "curl_cffi"

    @pytest.mark.asyncio
    async def test_stops_at_curl_when_curl_succeeds(self):
        """Should NOT escalate to playwright when curl_cffi succeeds."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        # Fallback (curl_cffi) succeeds
        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://protected.com",
            status_code=200,
            headers={},
            content="<html>Success via curl_cffi</html>",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        mock_third = AsyncMock()
        mock_third.fetch = AsyncMock()  # Should never be called

        config = TransportConfig(on_403="curl_cffi", on_blocked="playwright")
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
            third_tier_transport=mock_third,
        )

        result = await escalator.fetch("https://protected.com")

        # Should stop at curl_cffi
        mock_third.fetch.assert_not_called()
        assert result.transport_used == "curl_cffi"

    @pytest.mark.asyncio
    async def test_passes_playwright_config_to_third_tier(self):
        """Should pass playwright_wait_selector and timeout to third tier."""
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        mock_primary = AsyncMock()
        mock_primary.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://spa-site.com",
            status_code=403,
            headers={},
            content="Forbidden",
            transport_used="httpx",
            fetch_time_ms=50,
        ))

        mock_fallback = AsyncMock()
        mock_fallback.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://spa-site.com",
            status_code=403,
            headers={},
            content="Still Forbidden",
            transport_used="curl_cffi",
            fetch_time_ms=100,
        ))

        mock_third = AsyncMock()
        mock_third.fetch = AsyncMock(return_value=FetchArtifact(
            url="https://spa-site.com",
            status_code=200,
            headers={},
            content="<html>SPA Content</html>",
            transport_used="playwright",
            fetch_time_ms=500,
        ))

        config = TransportConfig(
            on_403="curl_cffi",
            on_blocked="playwright",
            playwright_wait_selector="#app",
            playwright_timeout_ms=15000,
        )
        escalator = TransportEscalator(
            config=config,
            primary_transport=mock_primary,
            fallback_transport=mock_fallback,
            third_tier_transport=mock_third,
        )

        await escalator.fetch("https://spa-site.com")

        # Verify playwright config was passed
        call_kwargs = mock_third.fetch.call_args.kwargs
        assert call_kwargs.get("wait_for_selector") == "#app"
        assert call_kwargs.get("timeout") == 15000
