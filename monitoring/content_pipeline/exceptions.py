"""
Exceptions for Content Pipeline

Custom exceptions for content extraction and transport errors.
"""

from typing import Optional


class ContentSizeExceededError(Exception):
    """
    Raised when content exceeds the configured size limit.

    This exception is raised when:
    - Content-Length header indicates content exceeds limit (early rejection)
    - Streaming content exceeds limit during download

    Attributes:
        url: The URL that returned oversized content
        max_size: The maximum allowed size in bytes
        actual_size: The actual content size in bytes (None if unknown)
    """

    def __init__(
        self,
        url: str,
        max_size: int,
        actual_size: Optional[int] = None,
    ) -> None:
        self.url = url
        self.max_size = max_size
        self.actual_size = actual_size

        if actual_size is not None:
            message = (
                f"Content size {actual_size} bytes exceeds limit of {max_size} bytes "
                f"for URL: {url}"
            )
        else:
            message = (
                f"Content exceeds limit of {max_size} bytes "
                f"for URL: {url}"
            )

        super().__init__(message)
