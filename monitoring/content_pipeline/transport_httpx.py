"""
HTTP Transport with Conditional Request Support

Provides httpx-based HTTP fetching with support for:
- Conditional requests (ETag/If-None-Match, Last-Modified/If-Modified-Since)
- 304 Not Modified response handling
- Content size limits
- Configurable timeouts
"""

import time
from typing import Optional

import httpx

from monitoring.content_pipeline.models import FetchArtifact


class HttpxTransport:
    """
    HTTP transport with conditional request support.

    Supports ETag and Last-Modified based conditional requests to enable
    304 Not Modified responses, avoiding redundant content extraction
    when pages haven't changed.
    """

    # Default max content size: 5MB
    default_max_size: int = 5_242_880

    # Default timeout in seconds
    default_timeout: float = 30.0

    # User-Agent string
    user_agent: str = "DiscoveryEngine/1.0 (ContentPipeline; +https://github.com/harmonic)"

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
        max_size: Optional[int] = None,
    ) -> FetchArtifact:
        """
        Fetch URL with conditional request headers.

        If etag provided, sends If-None-Match header.
        If last_modified provided, sends If-Modified-Since header.
        Returns FetchArtifact with is_not_modified=True on 304.

        Args:
            url: URL to fetch
            etag: Previous ETag value for conditional request
            last_modified: Previous Last-Modified value for conditional request
            timeout: Request timeout in seconds (default: 30.0)
            max_size: Maximum content size in bytes (default: 5MB)

        Returns:
            FetchArtifact containing response data

        Raises:
            httpx.TimeoutException: If request times out
            httpx.ConnectError: If connection fails
            httpx.HTTPError: For other HTTP-level errors
        """
        timeout = timeout if timeout is not None else self.default_timeout
        max_size = max_size if max_size is not None else self.default_max_size

        # Build request headers
        headers = {
            "User-Agent": self.user_agent,
        }

        # Add conditional request headers
        if etag is not None:
            headers["If-None-Match"] = etag
        if last_modified is not None:
            headers["If-Modified-Since"] = last_modified

        # Record start time for timing
        start_time = time.perf_counter()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )

        # Calculate fetch time
        fetch_time_ms = int((time.perf_counter() - start_time) * 1000)

        # Extract content (may be empty for 304)
        content = response.text or ""

        # Apply content size limit
        if max_size and len(content) > max_size:
            content = content[:max_size]

        # Lowercase header names for consistency
        response_headers = {k.lower(): v for k, v in response.headers.items()}

        # Extract caching headers from response
        response_etag = response.headers.get("ETag")
        response_last_modified = response.headers.get("Last-Modified")

        return FetchArtifact(
            url=url,
            status_code=response.status_code,
            headers=response_headers,
            content=content,
            encoding=response.encoding,
            etag=response_etag,
            last_modified=response_last_modified,
            transport_used="httpx",
            fetch_time_ms=fetch_time_ms,
        )
