"""
Data Models for Content Pipeline

FetchArtifact and PipelineResult types for content extraction results.
These types represent the output of HTTP fetches and content extraction.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RepresentationType(str, Enum):
    """Types of content representations that can be extracted."""

    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"
    STRUCTURED = "structured"  # JSON-LD/microdata


@dataclass
class FetchArtifact:
    """
    Result of an HTTP fetch operation before content extraction.

    Immutable record capturing all HTTP response metadata needed for
    caching, conditional requests, and debugging.
    """

    url: str
    status_code: int
    headers: Dict[str, str]  # Lowercased header names
    content: str  # Raw HTML/JSON response body

    # Optional response metadata
    encoding: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    # Timing and transport
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    transport_used: str = "httpx"
    fetch_time_ms: int = 0

    # Content size handling
    truncated: bool = False  # True if content was truncated due to size limits

    @property
    def is_cacheable(self) -> bool:
        """
        Check if this response is cacheable.

        A response is cacheable if it's a 200 OK and has either
        an ETag or Last-Modified header for conditional requests.
        """
        return self.status_code == 200 and (
            self.etag is not None or self.last_modified is not None
        )

    @property
    def is_not_modified(self) -> bool:
        """Check if this is a 304 Not Modified response."""
        return self.status_code == 304

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "url": self.url,
            "status_code": self.status_code,
            "headers": self.headers,
            "content": self.content,
            "encoding": self.encoding,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "transport_used": self.transport_used,
            "fetch_time_ms": self.fetch_time_ms,
            "truncated": self.truncated,
            "is_cacheable": self.is_cacheable,
            "is_not_modified": self.is_not_modified,
        }


@dataclass
class ExtractedContent:
    """
    A single extracted content representation.

    Represents the output of a content extractor, capturing
    the extracted text/data and metadata about the extraction process.
    """

    representation_type: RepresentationType
    content: str

    # Extraction metadata
    confidence: float = 1.0
    extractor_name: str = ""
    extraction_time_ms: int = 0
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "representation_type": self.representation_type.value,
            "content": self.content,
            "confidence": self.confidence,
            "extractor_name": self.extractor_name,
            "extraction_time_ms": self.extraction_time_ms,
            "metadata": self.metadata,
        }


@dataclass
class PipelineResult:
    """
    Final output from the content extraction pipeline.

    Combines the HTTP fetch artifact with all extracted content
    representations and pipeline execution metadata.
    """

    watch_id: int
    fetch_artifact: FetchArtifact

    # Extracted content
    representations: List[ExtractedContent] = field(default_factory=list)
    primary_representation: Optional[RepresentationType] = None

    # Pipeline status
    success: bool = True
    error: Optional[str] = None

    # Pipeline execution metadata
    preset_used: str = ""
    selectors_tried: Optional[List[str]] = None
    total_time_ms: int = 0
    pipeline_version: str = "1.0"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_primary_content(self) -> Optional[str]:
        """
        Get the content from the primary representation.

        Returns:
            The content string from the primary representation,
            or None if no primary representation is set or found.
        """
        if self.primary_representation is None:
            return None

        for rep in self.representations:
            if rep.representation_type == self.primary_representation:
                return rep.content

        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "watch_id": self.watch_id,
            "fetch_artifact": self.fetch_artifact.to_dict(),
            "representations": [rep.to_dict() for rep in self.representations],
            "primary_representation": (
                self.primary_representation.value
                if self.primary_representation
                else None
            ),
            "success": self.success,
            "error": self.error,
            "preset_used": self.preset_used,
            "selectors_tried": self.selectors_tried,
            "total_time_ms": self.total_time_ms,
            "pipeline_version": self.pipeline_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
