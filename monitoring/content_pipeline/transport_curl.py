"""
HTTP Transport using curl_cffi with Browser Impersonation

Provides curl_cffi-based HTTP fetching with support for:
- Browser impersonation (Chrome, Firefox, Safari TLS fingerprints)
- Conditional requests (ETag/If-None-Match, Last-Modified/If-Modified-Since)
- 304 Not Modified response handling
- Content size limits
- HTTP/2 support (automatic with browser impersonation)

This transport is used as a fallback when httpx fails due to bot detection.
"""

import time
from typing import Optional, Set

from curl_cffi.requests import AsyncSession

from monitoring.content_pipeline.exceptions import ContentSizeExceededError
from monitoring.content_pipeline.models import FetchArtifact


class CurlCffiTransport:
    """
    HTTP transport with browser impersonation for bypassing bot detection.

    Uses curl_cffi to impersonate real browsers at the TLS fingerprint level,
    making requests appear as if they come from Chrome, Firefox, or Safari.

    This is useful as a fallback transport when httpx receives 403/429 responses
    due to anti-bot measures that detect non-browser TLS fingerprints.

    Content size limits are enforced after receiving the response (curl_cffi
    doesn't support streaming in the same way as httpx).
    """

    # Default max content sizes
    default_max_html_bytes: int = 5_242_880  # 5MB
    default_max_json_bytes: int = 2_097_152  # 2MB

    # Default timeout in seconds
    default_timeout: float = 30.0

    # Default browser impersonation profile
    default_impersonate: str = "chrome120"

    # Supported impersonation profiles
    supported_profiles: Set[str] = {
        "chrome",
        "chrome99",
        "chrome100",
        "chrome101",
        "chrome104",
        "chrome107",
        "chrome110",
        "chrome116",
        "chrome119",
        "chrome120",
        "chrome123",
        "chrome124",
        "chrome131",
        "firefox",
        "ff91",
        "ff95",
        "ff98",
        "ff100",
        "ff102",
        "ff109",
        "ff117",
        "ff120",
        "ff133",
        "safari",
        "safari15_3",
        "safari15_5",
        "safari16_0",
        "safari17_0",
        "safari17_2_ios",
        "safari18_0",
        "safari18_0_ios",
    }

    def _get_limit_for_content_type(
        self,
        content_type: Optional[str],
        max_html_bytes: int,
        max_json_bytes: int,
    ) -> int:
        """
        Determine the size limit based on Content-Type header.

        Args:
            content_type: The Content-Type header value (may be None)
            max_html_bytes: Limit for HTML content
            max_json_bytes: Limit for JSON content

        Returns:
            The appropriate size limit in bytes
        """
        if content_type and "json" in content_type.lower():
            return max_json_bytes
        return max_html_bytes

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
        impersonate: Optional[str] = None,
    ) -> FetchArtifact:
        """
        Fetch URL with browser impersonation and conditional request headers.

        If etag provided, sends If-None-Match header.
        If last_modified provided, sends If-Modified-Since header.
        Returns FetchArtifact with is_not_modified=True on 304.

        Args:
            url: URL to fetch
            etag: Previous ETag value for conditional request
            last_modified: Previous Last-Modified value for conditional request
            timeout: Request timeout in seconds (default: 30.0)
            max_html_bytes: Maximum HTML content size (default: 5MB)
            max_json_bytes: Maximum JSON content size (default: 2MB)
            impersonate: Browser to impersonate (default: chrome120)

        Returns:
            FetchArtifact containing response data

        Raises:
            ContentSizeExceededError: If content exceeds size limit
            curl_cffi.requests.RequestsError: For network/timeout errors
        """
        timeout = timeout if timeout is not None else self.default_timeout
        max_html = max_html_bytes if max_html_bytes is not None else self.default_max_html_bytes
        max_json = max_json_bytes if max_json_bytes is not None else self.default_max_json_bytes
        browser = impersonate if impersonate is not None else self.default_impersonate

        # Build request headers
        headers = {}

        # Add conditional request headers
        if etag is not None:
            headers["If-None-Match"] = etag
        if last_modified is not None:
            headers["If-Modified-Since"] = last_modified

        # Record start time for timing
        start_time = time.perf_counter()

        async with AsyncSession(impersonate=browser) as session:
            response = await session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )

            # Get content type and determine limit
            content_type = response.headers.get("content-type")
            size_limit = self._get_limit_for_content_type(
                content_type, max_html, max_json
            )

            # Get content bytes
            content_bytes = response.content

            # Check content size
            if len(content_bytes) > size_limit:
                raise ContentSizeExceededError(
                    url=url,
                    max_size=size_limit,
                    actual_size=len(content_bytes),
                )

            # Calculate fetch time
            fetch_time_ms = int((time.perf_counter() - start_time) * 1000)

            # Decode content
            encoding = response.encoding or "utf-8"
            try:
                content = content_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                content = content_bytes.decode("utf-8", errors="replace")

            # Extract response headers (lowercased keys)
            response_headers = {k.lower(): v for k, v in response.headers.items()}

            return FetchArtifact(
                url=url,
                status_code=response.status_code,
                headers=response_headers,
                content=content,
                encoding=encoding,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                transport_used="curl_cffi",
                fetch_time_ms=fetch_time_ms,
                truncated=False,
            )
