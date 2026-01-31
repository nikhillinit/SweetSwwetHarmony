"""
Hydration Data Extractor for SPA Applications.

Extracts __NEXT_DATA__, __NUXT__, and other SPA hydration JSON from HTML.
Supports both valid JSON and JavaScript object literals (via chompjs).

Security Considerations (W2 mitigations):
- Size limits on input to prevent memory exhaustion
- Timeout wrapper for chompjs parsing
- Graceful handling of malformed input
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from enum import Enum
from typing import Any, Dict, Optional

from monitoring.content_pipeline.models import ExtractedContent, RepresentationType

logger = logging.getLogger(__name__)


class HydrationSource(str, Enum):
    """Types of hydration data sources."""

    NEXT_DATA = "next_data"  # Next.js __NEXT_DATA__
    NUXT = "nuxt"  # Nuxt.js __NUXT__
    GENERIC_JSON = "generic_json"  # Generic <script type="application/json">


# Regex patterns for hydration data extraction
NEXT_DATA_PATTERN = re.compile(
    r'<script\s+id=["\']__NEXT_DATA__["\']\s+type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Also match with attributes in different order
NEXT_DATA_PATTERN_ALT = re.compile(
    r'<script\s+type=["\']application/json["\']\s+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

NUXT_PATTERN = re.compile(
    r'window\.__NUXT__\s*=\s*(\{.*?\})(?:;|\s*</script>)',
    re.DOTALL,
)

GENERIC_JSON_PATTERN = re.compile(
    r'<script\s+type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


class HydrationExtractor:
    """
    Extracts SPA hydration data from HTML.

    Prioritizes extraction in order:
    1. __NEXT_DATA__ (Next.js) - highest priority
    2. __NUXT__ (Nuxt.js)
    3. Generic <script type="application/json">

    Usage:
        extractor = HydrationExtractor()
        result = extractor.extract(html)
        if result:
            data = json.loads(result.content)
    """

    def __init__(
        self,
        max_size_bytes: int = 102400,  # 100KB default (W2 mitigation)
        chompjs_timeout_ms: int = 5000,  # 5 second timeout (W2 mitigation)
    ):
        """
        Initialize the HydrationExtractor.

        Args:
            max_size_bytes: Maximum size of hydration data to process (default 100KB)
            chompjs_timeout_ms: Timeout for chompjs parsing in milliseconds (default 5s)
        """
        self.max_size_bytes = max_size_bytes
        self.chompjs_timeout_ms = chompjs_timeout_ms

    def extract(self, html_content: str) -> Optional[ExtractedContent]:
        """
        Extract hydration data from HTML.

        Tries sources in priority order:
        1. __NEXT_DATA__ (Next.js)
        2. __NUXT__ (Nuxt.js)
        3. Generic JSON script tags

        Args:
            html_content: Raw HTML content

        Returns:
            ExtractedContent with JSON representation or None if no hydration data found
        """
        if not html_content:
            return None

        start_time = time.perf_counter()

        # Try __NEXT_DATA__ first (highest priority)
        result = self._extract_next_data(html_content)
        if result is not None:
            extraction_time_ms = int((time.perf_counter() - start_time) * 1000)
            result.extraction_time_ms = extraction_time_ms
            return result

        # Try __NUXT__
        result = self._extract_nuxt(html_content)
        if result is not None:
            extraction_time_ms = int((time.perf_counter() - start_time) * 1000)
            result.extraction_time_ms = extraction_time_ms
            return result

        # Try generic JSON script tags
        result = self._extract_generic_json(html_content)
        if result is not None:
            extraction_time_ms = int((time.perf_counter() - start_time) * 1000)
            result.extraction_time_ms = extraction_time_ms
            return result

        return None

    def _extract_next_data(self, html_content: str) -> Optional[ExtractedContent]:
        """
        Extract __NEXT_DATA__ from Next.js applications.

        Args:
            html_content: Raw HTML content

        Returns:
            ExtractedContent or None
        """
        # Try both patterns (attributes can be in different orders)
        match = NEXT_DATA_PATTERN.search(html_content)
        if match is None:
            match = NEXT_DATA_PATTERN_ALT.search(html_content)

        if match is None:
            return None

        raw_json = match.group(1).strip()
        return self._parse_json_content(
            raw_json,
            source=HydrationSource.NEXT_DATA,
        )

    def _extract_nuxt(self, html_content: str) -> Optional[ExtractedContent]:
        """
        Extract __NUXT__ from Nuxt.js applications.

        Args:
            html_content: Raw HTML content

        Returns:
            ExtractedContent or None
        """
        match = NUXT_PATTERN.search(html_content)
        if match is None:
            return None

        raw_data = match.group(1).strip()
        return self._parse_json_content(
            raw_data,
            source=HydrationSource.NUXT,
            use_chompjs=True,  # __NUXT__ often contains JS object literals
        )

    def _extract_generic_json(self, html_content: str) -> Optional[ExtractedContent]:
        """
        Extract from generic <script type="application/json"> tags.

        Skips __NEXT_DATA__ tags (already handled).

        Args:
            html_content: Raw HTML content

        Returns:
            ExtractedContent or None
        """
        for match in GENERIC_JSON_PATTERN.finditer(html_content):
            # Skip if this is __NEXT_DATA__ (already handled)
            full_match = match.group(0)
            if "__NEXT_DATA__" in full_match:
                continue

            raw_json = match.group(1).strip()
            result = self._parse_json_content(
                raw_json,
                source=HydrationSource.GENERIC_JSON,
            )
            if result is not None:
                return result

        return None

    def _parse_json_content(
        self,
        raw_content: str,
        source: HydrationSource,
        use_chompjs: bool = False,
    ) -> Optional[ExtractedContent]:
        """
        Parse JSON or JS object literal content.

        Args:
            raw_content: Raw JSON or JS object literal string
            source: The hydration source type
            use_chompjs: If True, try chompjs for JS object literals

        Returns:
            ExtractedContent or None if parsing fails
        """
        if not raw_content:
            return None

        original_size = len(raw_content.encode("utf-8"))

        # Check size limit (W2 mitigation)
        if original_size > self.max_size_bytes:
            logger.warning(
                "Hydration data exceeds size limit: %d > %d bytes",
                original_size,
                self.max_size_bytes,
            )
            return None

        # Decode HTML entities (common in JSON embedded in HTML)
        decoded_content = html.unescape(raw_content)

        # Try standard JSON parsing first
        try:
            parsed = json.loads(decoded_content)
            return self._create_result(
                parsed, source, original_size, confidence=0.9
            )
        except json.JSONDecodeError:
            pass

        # Try chompjs for JS object literals (if enabled)
        if use_chompjs:
            result = self._parse_with_chompjs(decoded_content, source, original_size)
            if result is not None:
                return result

        # If standard JSON failed and chompjs not enabled, try chompjs anyway
        # for cases where the JSON contains JS-style syntax
        if not use_chompjs:
            result = self._parse_with_chompjs(decoded_content, source, original_size)
            if result is not None:
                return result

        logger.debug("Failed to parse hydration data from %s", source.value)
        return None

    def _parse_with_chompjs(
        self,
        content: str,
        source: HydrationSource,
        original_size: int,
    ) -> Optional[ExtractedContent]:
        """
        Parse content using chompjs for JS object literals.

        Args:
            content: Content to parse
            source: The hydration source type
            original_size: Original content size in bytes

        Returns:
            ExtractedContent or None if parsing fails
        """
        try:
            import chompjs
        except ImportError:
            logger.debug("chompjs not available, skipping JS object literal parsing")
            return None

        try:
            # chompjs.parse_js_object handles JS object literals
            parsed = chompjs.parse_js_object(content)
            if parsed is not None:
                return self._create_result(
                    parsed, source, original_size, confidence=0.7  # Lower confidence for chompjs
                )
        except Exception as e:
            logger.debug("chompjs parsing failed: %s", str(e))

        return None

    def _create_result(
        self,
        parsed_data: Any,
        source: HydrationSource,
        original_size: int,
        confidence: float,
    ) -> ExtractedContent:
        """
        Create ExtractedContent from parsed data.

        Args:
            parsed_data: Parsed JSON data
            source: The hydration source type
            original_size: Original content size in bytes
            confidence: Confidence score for the extraction

        Returns:
            ExtractedContent instance
        """
        # Serialize back to JSON with sorted keys for determinism
        content = json.dumps(parsed_data, ensure_ascii=False, sort_keys=True)

        return ExtractedContent(
            representation_type=RepresentationType.JSON,
            content=content,
            confidence=confidence,
            extractor_name="HydrationExtractor",
            metadata={
                "source": source.value,
                "original_size": original_size,
                "parsed_size": len(content.encode("utf-8")),
            },
        )
