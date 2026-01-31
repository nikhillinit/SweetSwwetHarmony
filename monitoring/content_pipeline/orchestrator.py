"""
Content Pipeline Orchestrator

Coordinates the content extraction pipeline for a Watch:
1. Uses PipelinePlanner to determine WatchConfig from URL and config_json
2. Uses HttpxTransport to fetch content with conditional requests
3. Uses SelectorExtractor to extract content from HTML
4. Optionally uses StructuredDataExtractor for JSON-LD/microdata extraction
5. Optionally uses HydrationExtractor for SPA hydration data (__NEXT_DATA__, __NUXT__)
6. Builds PipelineResult with timing and metadata

Handles 304 Not Modified responses and various error conditions.
Supports multiple representations (TEXT, JSON) when prefer_structured=True.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, TYPE_CHECKING

import httpx

from monitoring.content_pipeline.config import FallbackConfig, WatchConfig
from monitoring.content_pipeline.exceptions import ContentSizeExceededError
from monitoring.content_pipeline.extract_html import SelectorExtractor
from monitoring.content_pipeline.extract_hydration import HydrationExtractor
from monitoring.content_pipeline.extract_structured import StructuredDataExtractor
from monitoring.content_pipeline.models import (
    ExtractedContent,
    FetchArtifact,
    PipelineResult,
    RepresentationType,
)
from monitoring.content_pipeline.planner import PipelinePlanner
from monitoring.content_pipeline.transport_httpx import HttpxTransport

if TYPE_CHECKING:
    from monitoring.models import Watch

logger = logging.getLogger(__name__)

# Minimum confidence for structured data to be considered "rich"
STRUCTURED_DATA_CONFIDENCE_THRESHOLD = 0.5


class ContentPipeline:
    """
    Orchestrates the content extraction pipeline for a Watch.

    Coordinates:
    - PipelinePlanner: Determines WatchConfig from URL and config_json
    - HttpxTransport: Fetches content with conditional requests (ETag/Last-Modified)
    - SelectorExtractor: Extracts content from HTML using CSS/XPath selectors
    - StructuredDataExtractor: Extracts JSON-LD/microdata (when prefer_structured=True)

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

        # With structured data extraction
        # Set prefer_structured=True in ExtractorConfig to get JSON-LD as primary
    """

    def __init__(
        self,
        planner: Optional[PipelinePlanner] = None,
        transport: Optional[HttpxTransport] = None,
        extractor: Optional[SelectorExtractor] = None,
        structured_extractor: Optional[StructuredDataExtractor] = None,
        hydration_extractor: Optional[HydrationExtractor] = None,
    ):
        """
        Initialize the ContentPipeline.

        Args:
            planner: Optional PipelinePlanner instance (creates default if not provided)
            transport: Optional HttpxTransport instance (creates default if not provided)
            extractor: Optional SelectorExtractor instance (creates default if not provided)
            structured_extractor: Optional StructuredDataExtractor (not created by default)
            hydration_extractor: Optional HydrationExtractor for SPA data (not created by default)
        """
        self._planner = planner or PipelinePlanner()
        self._transport = transport or HttpxTransport()
        self._extractor = extractor or SelectorExtractor()
        self._structured_extractor = structured_extractor
        self._hydration_extractor = hydration_extractor

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

        # Extract content - potentially both structured and text
        try:
            representations, primary_rep, selectors_tried = self._extract_all_content(
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

        # Calculate total time from all representations
        extraction_time = sum(r.extraction_time_ms for r in representations)
        total_time_ms = fetch_artifact.fetch_time_ms + extraction_time

        return PipelineResult(
            watch_id=watch_id,
            fetch_artifact=fetch_artifact,
            representations=representations,
            primary_representation=primary_rep,
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

    def _extract_all_content(
        self,
        html: str,
        watch_config: WatchConfig,
    ) -> tuple[List[ExtractedContent], RepresentationType, Optional[List[str]]]:
        """
        Extract all content representations from HTML.

        For SPA presets (spa, spa_hydration_v1):
        - Tries hydration extraction first (__NEXT_DATA__, __NUXT__)
        - Falls back to text extraction if no hydration data found

        When prefer_structured=True (non-SPA):
        - Tries structured data extraction first (JSON-LD/microdata)
        - If structured data is rich (confidence >= 0.5), includes it
        - Always does text extraction
        - Returns JSON as primary if structured data is rich

        When prefer_structured=False:
        - Only does text extraction
        - Returns TEXT as primary

        Args:
            html: Raw HTML content
            watch_config: WatchConfig with extraction settings

        Returns:
            Tuple of (representations list, primary representation type, selectors_tried)
        """
        representations: List[ExtractedContent] = []
        primary_rep = RepresentationType.TEXT
        selectors_tried: Optional[List[str]] = None

        prefer_structured = watch_config.extractor.prefer_structured
        is_spa_preset = watch_config.extractor.preset in ("spa", "spa_hydration_v1")

        # For SPA presets, try hydration extraction first
        hydration_content: Optional[ExtractedContent] = None
        if is_spa_preset and self._hydration_extractor is not None:
            hydration_content = self._extract_hydration(html)
            if hydration_content is not None:
                representations.append(hydration_content)
                primary_rep = RepresentationType.JSON
                # For SPA, hydration data is primary - skip other extractions unless fallback needed
                if not watch_config.extractor.fallback_on_empty:
                    return representations, primary_rep, selectors_tried

        # Try structured extraction if prefer_structured and not SPA (or SPA with no hydration data)
        structured_content: Optional[ExtractedContent] = None
        if prefer_structured and not is_spa_preset:
            structured_content = self._extract_structured(html)
            # Include structured data if confidence meets threshold
            if (
                structured_content is not None
                and structured_content.confidence >= STRUCTURED_DATA_CONFIDENCE_THRESHOLD
            ):
                representations.append(structured_content)
                primary_rep = RepresentationType.JSON

        # Do text extraction if:
        # - Not a SPA preset, OR
        # - SPA preset but no hydration data found and fallback_on_empty is True
        should_extract_text = (
            not is_spa_preset or
            (hydration_content is None and watch_config.extractor.fallback_on_empty)
        )

        if should_extract_text:
            text_content = self._extract_text(html, watch_config)
            if text_content is not None:
                representations.append(text_content)

                # Get selectors_tried from text extraction metadata
                if text_content.metadata:
                    selectors_tried = text_content.metadata.get("selectors_tried")
                    if selectors_tried is None and text_content.metadata.get("selector_used"):
                        selectors_tried = [text_content.metadata["selector_used"]]

        # If no structured/hydration data was found or confidence was low, text is primary
        if hydration_content is None and (
            not representations or
            (structured_content is None or
             structured_content.confidence < STRUCTURED_DATA_CONFIDENCE_THRESHOLD)
        ):
            primary_rep = RepresentationType.TEXT

        return representations, primary_rep, selectors_tried

    def _extract_hydration(self, html: str) -> Optional[ExtractedContent]:
        """
        Extract SPA hydration data (__NEXT_DATA__, __NUXT__) from HTML.

        Args:
            html: Raw HTML content

        Returns:
            ExtractedContent with JSON representation or None
        """
        if self._hydration_extractor is None:
            return None

        try:
            return self._hydration_extractor.extract(html)
        except Exception as e:
            logger.debug("Hydration extraction failed: %s", str(e))
            return None

    def _extract_structured(self, html: str) -> Optional[ExtractedContent]:
        """
        Extract structured data (JSON-LD/microdata) from HTML.

        Args:
            html: Raw HTML content

        Returns:
            ExtractedContent with JSON representation or None
        """
        # Create extractor on demand if not injected
        extractor = self._structured_extractor
        if extractor is None:
            extractor = StructuredDataExtractor()

        try:
            return extractor.extract(html)
        except Exception as e:
            logger.debug("Structured data extraction failed: %s", str(e))
            return None

    def _extract_text(self, html: str, watch_config: WatchConfig) -> Optional[ExtractedContent]:
        """
        Extract text content from HTML using SelectorExtractor.

        Args:
            html: Raw HTML content
            watch_config: WatchConfig with extraction settings

        Returns:
            ExtractedContent or None if extraction fails
        """
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

    def _extract_content(self, html: str, watch_config: WatchConfig) -> Optional[ExtractedContent]:
        """
        Extract content from HTML using SelectorExtractor.

        DEPRECATED: Use _extract_all_content for multiple representations.

        Args:
            html: Raw HTML content
            watch_config: WatchConfig with extraction settings

        Returns:
            ExtractedContent or None if extraction fails
        """
        return self._extract_text(html, watch_config)
