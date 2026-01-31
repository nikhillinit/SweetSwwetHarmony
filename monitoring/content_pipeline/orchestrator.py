"""
Content Pipeline Orchestrator

Coordinates the content extraction pipeline for a Watch:
1. Uses PipelinePlanner to determine WatchConfig from URL and config_json
2. Uses HttpxTransport to fetch content with conditional requests
3. Uses SelectorExtractor to extract content from HTML
4. Builds PipelineResult with timing and metadata

Handles 304 Not Modified responses and various error conditions.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

import httpx

from monitoring.content_pipeline.config import FallbackConfig, WatchConfig
from monitoring.content_pipeline.exceptions import ContentSizeExceededError
from monitoring.content_pipeline.extract_html import SelectorExtractor
from monitoring.content_pipeline.models import (
    FetchArtifact,
    PipelineResult,
    RepresentationType,
)
from monitoring.content_pipeline.planner import PipelinePlanner
from monitoring.content_pipeline.transport_httpx import HttpxTransport

if TYPE_CHECKING:
    from monitoring.models import Watch

logger = logging.getLogger(__name__)


class ContentPipeline:
    """
    Orchestrates the content extraction pipeline for a Watch.

    Coordinates:
    - PipelinePlanner: Determines WatchConfig from URL and config_json
    - HttpxTransport: Fetches content with conditional requests (ETag/Last-Modified)
    - SelectorExtractor: Extracts content from HTML using CSS/XPath selectors

    Usage:
        pipeline = ContentPipeline()

        # Process a Watch object
        result = await pipeline.process(watch)

        # Or process a URL directly
        result = await pipeline.process_url(
            watch_id=1,
            url="https://example.com",
            etag='"abc123"',
        )
    """

    def __init__(
        self,
        planner: Optional[PipelinePlanner] = None,
        transport: Optional[HttpxTransport] = None,
        extractor: Optional[SelectorExtractor] = None,
    ):
        """
        Initialize the ContentPipeline.

        Args:
            planner: Optional PipelinePlanner instance (creates default if not provided)
            transport: Optional HttpxTransport instance (creates default if not provided)
            extractor: Optional SelectorExtractor instance (creates default if not provided)
        """
        self._planner = planner or PipelinePlanner()
        self._transport = transport or HttpxTransport()
        self._extractor = extractor or SelectorExtractor()

    async def process(self, watch: "Watch") -> PipelineResult:
        """
        Process a Watch object to produce a PipelineResult.

        Args:
            watch: The Watch object to process

        Returns:
            PipelineResult with fetch artifact and extracted content
        """
        return await self.process_url(
            watch_id=watch.id or 0,
            url=watch.url,
            config_json=watch.config_json,
            etag=watch.last_etag,
            last_modified=watch.last_modified,
        )

    async def process_url(
        self,
        watch_id: int,
        url: str,
        config_json: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> PipelineResult:
        """
        Process a URL to produce a PipelineResult.

        Args:
            watch_id: ID of the watch
            url: URL to fetch and extract content from
            config_json: Optional explicit configuration JSON
            etag: Optional ETag for conditional request
            last_modified: Optional Last-Modified for conditional request

        Returns:
            PipelineResult with fetch artifact and extracted content
        """
        pipeline_start = time.perf_counter()

        # Get configuration from planner
        watch_config = self._planner.plan(url, config_json=config_json)

        # Prepare fetch artifact placeholder for error cases
        fetch_artifact: Optional[FetchArtifact] = None
        error_message: Optional[str] = None

        # Fetch content
        try:
            fetch_artifact = await self._fetch(
                url=url,
                etag=etag,
                last_modified=last_modified,
                max_html_bytes=watch_config.transport.max_html_bytes,
                max_json_bytes=watch_config.transport.max_json_bytes,
            )
        except httpx.TimeoutException as e:
            error_message = f"Request timeout: {str(e)}"
            logger.warning("Fetch timeout for %s: %s", url, error_message)
        except httpx.ConnectError as e:
            error_message = f"Connection error: {str(e)}"
            logger.warning("Connection error for %s: %s", url, error_message)
        except ContentSizeExceededError as e:
            error_message = f"Content size exceeded: {str(e)}"
            logger.warning("Content size exceeded for %s: %s", url, error_message)
        except httpx.HTTPError as e:
            error_message = f"HTTP error: {str(e)}"
            logger.warning("HTTP error for %s: %s", url, error_message)

        # If fetch failed, return error result
        if fetch_artifact is None:
            total_time_ms = int((time.perf_counter() - pipeline_start) * 1000)
            return PipelineResult(
                watch_id=watch_id,
                fetch_artifact=FetchArtifact(
                    url=url,
                    status_code=0,
                    headers={},
                    content="",
                    fetch_time_ms=total_time_ms,
                ),
                representations=[],
                primary_representation=RepresentationType.TEXT,
                success=False,
                error=error_message,
                preset_used=watch_config.preset,
                total_time_ms=total_time_ms,
            )

        # Handle 304 Not Modified - no extraction needed
        if fetch_artifact.is_not_modified:
            # Use fetch_time_ms since no extraction was done
            total_time_ms = fetch_artifact.fetch_time_ms
            return PipelineResult(
                watch_id=watch_id,
                fetch_artifact=fetch_artifact,
                representations=[],
                primary_representation=RepresentationType.TEXT,
                success=True,
                preset_used=watch_config.preset,
                total_time_ms=total_time_ms,
            )

        # Extract content
        try:
            extracted_content = self._extract_content(
                html=fetch_artifact.content,
                watch_config=watch_config,
            )
        except Exception as e:
            error_message = f"Extraction error: {str(e)}"
            logger.warning("Extraction error for %s: %s", url, error_message)

            total_time_ms = int((time.perf_counter() - pipeline_start) * 1000)
            return PipelineResult(
                watch_id=watch_id,
                fetch_artifact=fetch_artifact,
                representations=[],
                primary_representation=RepresentationType.TEXT,
                success=False,
                error=error_message,
                preset_used=watch_config.preset,
                total_time_ms=total_time_ms,
            )

        # Calculate total time
        extraction_time = extracted_content.extraction_time_ms if extracted_content else 0
        total_time_ms = fetch_artifact.fetch_time_ms + extraction_time

        # Build selectors_tried from extraction metadata
        selectors_tried = None
        if extracted_content and extracted_content.metadata:
            selectors_tried = extracted_content.metadata.get("selectors_tried")
            # If no selectors_tried, try to get from selector_used
            if selectors_tried is None and extracted_content.metadata.get("selector_used"):
                selectors_tried = [extracted_content.metadata["selector_used"]]

        return PipelineResult(
            watch_id=watch_id,
            fetch_artifact=fetch_artifact,
            representations=[extracted_content] if extracted_content else [],
            primary_representation=RepresentationType.TEXT,
            success=True,
            preset_used=watch_config.preset,
            selectors_tried=selectors_tried,
            total_time_ms=total_time_ms,
        )

    async def _fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
    ) -> FetchArtifact:
        """
        Fetch content from URL using HttpxTransport.

        Args:
            url: URL to fetch
            etag: Optional ETag for conditional request
            last_modified: Optional Last-Modified for conditional request
            max_html_bytes: Maximum HTML content size
            max_json_bytes: Maximum JSON content size

        Returns:
            FetchArtifact with response data
        """
        return await self._transport.fetch(
            url=url,
            etag=etag,
            last_modified=last_modified,
            max_html_bytes=max_html_bytes,
            max_json_bytes=max_json_bytes,
        )

    def _extract_content(self, html: str, watch_config: WatchConfig) -> Optional["ExtractedContent"]:
        """
        Extract content from HTML using SelectorExtractor.

        Args:
            html: Raw HTML content
            watch_config: WatchConfig with extraction settings

        Returns:
            ExtractedContent or None if extraction fails
        """
        from monitoring.content_pipeline.models import ExtractedContent

        # Get selectors from config
        selectors = watch_config.extractor.selectors or ["body"]

        # Get remove_selectors from metadata if present
        remove_selectors = watch_config.metadata.get("remove_selectors")

        # Build FallbackConfig from extractor settings
        fallback_config = FallbackConfig(
            fallback_on_empty=watch_config.extractor.fallback_on_empty,
            min_chars=0,
            always_include_body=True,
        )

        return self._extractor.extract(
            html=html,
            selectors=selectors,
            remove_selectors=remove_selectors,
            fallback_config=fallback_config,
        )
