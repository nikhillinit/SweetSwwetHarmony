"""
HTTP Transport with Conditional Request Support

Provides httpx-based HTTP fetching with support for:
- Conditional requests (ETag/If-None-Match, Last-Modified/If-Modified-Since)
- 304 Not Modified response handling
- Content size limits with streaming enforcement
- Content-Type based limits (HTML vs JSON)
- Configurable timeouts
"""

import time
from typing import Optional

import httpx

from monitoring.content_pipeline.exceptions import ContentSizeExceededError
from monitoring.content_pipeline.models import FetchArtifact


class HttpxTransport:
    """
    HTTP transport with conditional request support.

    Supports ETag and Last-Modified based conditional requests to enable
    304 Not Modified responses, avoiding redundant content extraction
    when pages haven't changed.

    Content size limits are enforced via streaming to avoid memory bombs:
    - HTML/unknown: 5MB default
    - JSON: 2MB default

    HTTP/2 is enabled by default for better performance and connection multiplexing.
    """

    # Default max content sizes
    default_max_size: int = 5_242_880  # 5MB for HTML
    default_max_html_bytes: int = 5_242_880  # 5MB
    default_max_json_bytes: int = 2_097_152  # 2MB

    # Default timeout in seconds
    default_timeout: float = 30.0

    # User-Agent string
    user_agent: str = "DiscoveryEngine/1.0 (ContentPipeline; +https://github.com/harmonic)"

    # HTTP/2 enabled by default
    http2_enabled: bool = True

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
        max_size: Optional[int] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
    ) -> FetchArtifact:
        """
        Fetch URL with conditional request headers and streaming size limits.

        If etag provided, sends If-None-Match header.
        If last_modified provided, sends If-Modified-Since header.
        Returns FetchArtifact with is_not_modified=True on 304.

        Size limits are enforced via streaming:
        - If Content-Length header exceeds limit, fails fast without reading body
        - Otherwise reads body in streaming chunks until limit is reached

        Args:
            url: URL to fetch
            etag: Previous ETag value for conditional request
            last_modified: Previous Last-Modified value for conditional request
            timeout: Request timeout in seconds (default: 30.0)
            max_size: Legacy max content size in bytes (default: 5MB)
            max_html_bytes: Maximum HTML content size (default: 5MB)
            max_json_bytes: Maximum JSON content size (default: 2MB)

        Returns:
            FetchArtifact containing response data

        Raises:
            ContentSizeExceededError: If content exceeds size limit
            httpx.TimeoutException: If request times out
            httpx.ConnectError: If connection fails
            httpx.HTTPError: For other HTTP-level errors
        """
        timeout = timeout if timeout is not None else self.default_timeout
        max_html = max_html_bytes if max_html_bytes is not None else self.default_max_html_bytes
        max_json = max_json_bytes if max_json_bytes is not None else self.default_max_json_bytes

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

        async with httpx.AsyncClient(http2=self.http2_enabled) as client:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            ) as response:
                # Get content type and determine limit
                content_type = response.headers.get("content-type")
                size_limit = self._get_limit_for_content_type(
                    content_type, max_html, max_json
                )

                # Check Content-Length header for early rejection
                content_length_str = response.headers.get("content-length")
                if content_length_str:
                    try:
                        content_length = int(content_length_str)
                        if content_length > size_limit:
                            raise ContentSizeExceededError(
                                url=url,
                                max_size=size_limit,
                                actual_size=content_length,
                            )
                    except ValueError:
                        pass  # Ignore invalid Content-Length

                # For 304 responses, no body to read
                if response.status_code == 304:
                    content_bytes = b""
                    truncated = False
                else:
                    # Stream content with size limit
                    content_bytes, truncated = await self._stream_with_limit(
                        response, size_limit, url
                    )

                # Store response metadata before exiting context
                status_code = response.status_code
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                encoding = response.encoding or "utf-8"
                response_etag = response.headers.get("ETag")
                response_last_modified = response.headers.get("Last-Modified")

        # Calculate fetch time
        fetch_time_ms = int((time.perf_counter() - start_time) * 1000)

        # Decode content
        try:
            content = content_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            content = content_bytes.decode("utf-8", errors="replace")

        return FetchArtifact(
            url=url,
            status_code=status_code,
            headers=response_headers,
            content=content,
            encoding=encoding,
            etag=response_etag,
            last_modified=response_last_modified,
            transport_used="httpx",
            fetch_time_ms=fetch_time_ms,
            truncated=truncated,
        )

    async def _stream_with_limit(
        self,
        response: httpx.Response,
        size_limit: int,
        url: str,
    ) -> tuple[bytes, bool]:
        """
        Stream response body with size limit enforcement.

        Args:
            response: The httpx Response object to stream from
            size_limit: Maximum bytes to read
            url: URL for error reporting

        Returns:
            Tuple of (content_bytes, truncated)

        Raises:
            ContentSizeExceededError: If content exceeds size limit
        """
        chunks = []
        total_size = 0

        async for chunk in response.aiter_bytes():
            total_size += len(chunk)
            if total_size > size_limit:
                # Content exceeded limit
                raise ContentSizeExceededError(
                    url=url,
                    max_size=size_limit,
                    actual_size=None,  # Don't know total size
                )
            chunks.append(chunk)

        return b"".join(chunks), False
