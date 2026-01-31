"""
Transport Escalator with Automatic Fallback Logic

Orchestrates HTTP transport escalation when primary transport encounters
bot detection, rate limiting, or other access issues.

Escalation triggers:
- 403 Forbidden: Bot detection at IP/TLS fingerprint level
- 429 Too Many Requests: Rate limiting
- Blocked response patterns: Cloudflare challenges, CAPTCHA, etc.

The escalator uses the primary transport (httpx) first, then falls back
to curl_cffi with browser impersonation when needed.
"""

import logging
import re
from typing import Optional, Protocol

from monitoring.content_pipeline.config import TransportConfig
from monitoring.content_pipeline.models import FetchArtifact

logger = logging.getLogger(__name__)


# Patterns that indicate the response is a bot detection page, not real content
BLOCKED_PATTERNS = [
    # Cloudflare
    re.compile(r"cf-browser-verification", re.IGNORECASE),
    re.compile(r"Checking your browser before accessing", re.IGNORECASE),
    re.compile(r"Enable JavaScript and cookies", re.IGNORECASE),
    re.compile(r"Ray ID:", re.IGNORECASE),
    # Generic bot detection
    re.compile(r"Access Denied", re.IGNORECASE),
    re.compile(r"unusual traffic from your computer", re.IGNORECASE),
    re.compile(r"automated access", re.IGNORECASE),
    re.compile(r"captcha", re.IGNORECASE),
    # PerimeterX
    re.compile(r"px-captcha", re.IGNORECASE),
    # DataDome
    re.compile(r"datadome", re.IGNORECASE),
]


class TransportProtocol(Protocol):
    """Protocol for HTTP transports (HttpxTransport, CurlCffiTransport)."""

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
        **kwargs,
    ) -> FetchArtifact:
        """Fetch URL and return FetchArtifact."""
        ...


class TransportEscalator:
    """
    Orchestrates 3-tier transport escalation for bot detection bypass.

    Escalation tiers:
    - Tier 1: Primary transport (httpx) - Fast, standard HTTP/2
    - Tier 2: Fallback transport (curl_cffi) - Browser TLS impersonation
    - Tier 3: Third tier transport (playwright) - Full headless browser

    Escalation triggers:
    - 403 Forbidden (if on_403 configured)
    - 429 Too Many Requests (if on_429 configured)
    - Blocked response patterns (Cloudflare, CAPTCHA, etc.)
    - on_blocked: Additional escalation to playwright when Tier 2 also blocked

    Example:
        config = TransportConfig(
            on_403="curl_cffi",
            on_blocked="playwright",
            user_agent_profile="chrome",
        )
        escalator = TransportEscalator(config=config)
        result = await escalator.fetch("https://example.com")
    """

    def __init__(
        self,
        config: Optional[TransportConfig] = None,
        primary_transport: Optional[TransportProtocol] = None,
        fallback_transport: Optional[TransportProtocol] = None,
        third_tier_transport: Optional[TransportProtocol] = None,
    ):
        """
        Initialize the TransportEscalator.

        Args:
            config: TransportConfig with escalation settings
            primary_transport: Primary transport (defaults to HttpxTransport)
            fallback_transport: Fallback transport (defaults to CurlCffiTransport)
            third_tier_transport: Third tier transport (defaults to PlaywrightTransport)
        """
        self._config = config or TransportConfig()
        self._primary_transport = primary_transport
        self._fallback_transport = fallback_transport
        self._third_tier_transport = third_tier_transport

    def _get_primary_transport(self) -> TransportProtocol:
        """Get or create primary transport."""
        if self._primary_transport is not None:
            return self._primary_transport

        from monitoring.content_pipeline.transport_httpx import HttpxTransport
        self._primary_transport = HttpxTransport()
        return self._primary_transport

    def _get_fallback_transport(self) -> TransportProtocol:
        """Get or create fallback transport."""
        if self._fallback_transport is not None:
            return self._fallback_transport

        from monitoring.content_pipeline.transport_curl import CurlCffiTransport
        self._fallback_transport = CurlCffiTransport()
        return self._fallback_transport

    def _get_third_tier_transport(self) -> TransportProtocol:
        """Get or create third tier transport (playwright)."""
        if self._third_tier_transport is not None:
            return self._third_tier_transport

        from monitoring.content_pipeline.transport_playwright import PlaywrightTransport
        self._third_tier_transport = PlaywrightTransport()
        return self._third_tier_transport

    def _should_escalate_to_tier2(self, artifact: FetchArtifact) -> bool:
        """
        Determine if we should escalate from primary to fallback transport.

        Escalation triggers:
        - 403 status code (when on_403 is configured)
        - 429 status code (when on_429 is configured)
        - Blocked response patterns (when any escalation is configured)

        Args:
            artifact: FetchArtifact from primary transport

        Returns:
            True if we should try fallback transport
        """
        # Check if any escalation is enabled
        escalation_enabled = (
            self._config.on_403 is not None or
            self._config.on_429 is not None
        )

        if not escalation_enabled:
            return False

        # Check 403
        if artifact.status_code == 403 and self._config.on_403 is not None:
            logger.info("Escalating to tier 2 due to 403 Forbidden")
            return True

        # Check 429
        if artifact.status_code == 429 and self._config.on_429 is not None:
            logger.info("Escalating to tier 2 due to 429 Too Many Requests")
            return True

        # Check blocked patterns in response body
        if self._is_blocked_response(artifact.content):
            logger.info("Escalating to tier 2 due to blocked response pattern")
            return True

        return False

    def _should_escalate_to_tier3(self, artifact: FetchArtifact) -> bool:
        """
        Determine if we should escalate from fallback to third tier (playwright).

        Escalation triggers:
        - on_blocked is configured AND:
          - 403 status code, OR
          - 429 status code, OR
          - Blocked response patterns

        Args:
            artifact: FetchArtifact from fallback transport

        Returns:
            True if we should try third tier transport
        """
        # Check if third tier escalation is enabled
        if self._config.on_blocked is None:
            return False

        # Check 403
        if artifact.status_code == 403:
            logger.info("Escalating to tier 3 due to 403 Forbidden")
            return True

        # Check 429
        if artifact.status_code == 429:
            logger.info("Escalating to tier 3 due to 429 Too Many Requests")
            return True

        # Check blocked patterns in response body
        if self._is_blocked_response(artifact.content):
            logger.info("Escalating to tier 3 due to blocked response pattern")
            return True

        return False

    def _should_escalate(self, artifact: FetchArtifact) -> bool:
        """
        Determine if we should escalate to fallback transport.

        DEPRECATED: Use _should_escalate_to_tier2 instead.
        Kept for backwards compatibility.
        """
        return self._should_escalate_to_tier2(artifact)

    def _is_blocked_response(self, content: str) -> bool:
        """
        Check if response content matches known bot detection patterns.

        Args:
            content: Response body text

        Returns:
            True if content appears to be a bot detection page
        """
        for pattern in BLOCKED_PATTERNS:
            if pattern.search(content):
                return True
        return False

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
    ) -> FetchArtifact:
        """
        Fetch URL with automatic 3-tier transport escalation.

        Escalation chain:
        1. Primary transport (httpx) - Fast HTTP/2
        2. Fallback transport (curl_cffi) - Browser TLS impersonation
        3. Third tier transport (playwright) - Full headless browser

        Each tier only escalates if:
        - Previous tier encounters 403/429/blocked patterns
        - Corresponding config option is set (on_403/on_429/on_blocked)

        Args:
            url: URL to fetch
            etag: Optional ETag for conditional request
            last_modified: Optional Last-Modified for conditional request
            timeout: Optional timeout in seconds
            max_html_bytes: Maximum HTML content size
            max_json_bytes: Maximum JSON content size

        Returns:
            FetchArtifact from whichever transport succeeded (or last result)
        """
        primary = self._get_primary_transport()

        # Tier 1: Try primary transport
        artifact = await primary.fetch(
            url=url,
            etag=etag,
            last_modified=last_modified,
            timeout=timeout,
            max_html_bytes=max_html_bytes,
            max_json_bytes=max_json_bytes,
        )

        # Check if we should escalate to Tier 2
        if self._should_escalate_to_tier2(artifact):
            fallback = self._get_fallback_transport()

            # Build fallback kwargs
            fallback_kwargs = {
                "url": url,
                "etag": etag,
                "last_modified": last_modified,
                "timeout": timeout,
                "max_html_bytes": max_html_bytes,
                "max_json_bytes": max_json_bytes,
            }

            # Pass user_agent_profile as impersonate for curl_cffi
            if self._config.user_agent_profile:
                fallback_kwargs["impersonate"] = self._config.user_agent_profile

            logger.info(
                "Escalating from %s to tier 2 (curl_cffi) for %s",
                artifact.transport_used,
                url,
            )

            # Tier 2: Try fallback transport
            artifact = await fallback.fetch(**fallback_kwargs)

            # Check if we should escalate to Tier 3
            if self._should_escalate_to_tier3(artifact):
                third_tier = self._get_third_tier_transport()

                # Build third tier kwargs
                third_tier_kwargs = {
                    "url": url,
                    "etag": etag,
                    "last_modified": last_modified,
                    "max_html_bytes": max_html_bytes,
                    "max_json_bytes": max_json_bytes,
                }

                # Pass playwright-specific config
                if self._config.playwright_wait_selector:
                    third_tier_kwargs["wait_for_selector"] = self._config.playwright_wait_selector
                if self._config.playwright_timeout_ms:
                    third_tier_kwargs["timeout"] = self._config.playwright_timeout_ms

                logger.info(
                    "Escalating from %s to tier 3 (playwright) for %s",
                    artifact.transport_used,
                    url,
                )

                # Tier 3: Try playwright transport
                artifact = await third_tier.fetch(**third_tier_kwargs)

        return artifact
