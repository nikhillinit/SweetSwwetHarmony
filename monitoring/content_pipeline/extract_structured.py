"""
Extruct-based Structured Data Extraction (JSON-LD, Microdata, OpenGraph, RDFa)

Extracts embedded structured data from HTML pages using the extruct library:
- JSON-LD: Schema.org structured data in <script type="application/ld+json">
- Microdata: HTML5 microdata (itemscope, itemprop attributes)
- OpenGraph: Facebook/social media meta tags (og:*)
- RDFa: W3C standard for embedding RDF in HTML

This extractor is ideal for pages with rich structured data like:
- E-commerce product pages
- SaaS pricing pages
- Company/organization pages

Usage:
    from monitoring.content_pipeline.extract_structured import StructuredDataExtractor

    extractor = StructuredDataExtractor()
    result = extractor.extract(html)
    # result.content is JSON string with extracted structured data
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

import extruct
from w3lib.html import get_base_url

from monitoring.content_pipeline.models import ExtractedContent, RepresentationType

logger = logging.getLogger(__name__)


class StructuredDataExtractor:
    """
    Extract structured data from HTML using extruct.

    Extracts JSON-LD, microdata, OpenGraph, and RDFa metadata from HTML
    and returns it as a JSON representation.

    Attributes:
        base_url: Base URL for resolving relative URLs (optional)
        syntaxes: List of syntaxes to extract (default: json-ld, microdata, opengraph, rdfa)
    """

    # Default syntaxes to extract (excluding microformat as per task spec)
    DEFAULT_SYNTAXES = ["json-ld", "microdata", "opengraph", "rdfa"]

    def __init__(
        self,
        base_url: Optional[str] = None,
        syntaxes: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize the StructuredDataExtractor.

        Args:
            base_url: Base URL for resolving relative URLs in the extracted data
            syntaxes: List of syntaxes to extract. Defaults to json-ld, microdata,
                     opengraph, and rdfa. Options: json-ld, microdata, opengraph,
                     rdfa, microformat, dublincore
        """
        self.base_url = base_url
        self.syntaxes = syntaxes or self.DEFAULT_SYNTAXES

    def extract(self, html: Optional[str]) -> ExtractedContent:
        """
        Extract structured data from HTML.

        Args:
            html: Raw HTML content to extract structured data from

        Returns:
            ExtractedContent with JSON representation of extracted structured data
        """
        start_time = time.perf_counter()

        # Handle empty/None HTML
        if not html or not html.strip():
            return self._empty_result(start_time)

        try:
            # Determine base URL for resolving relative URLs
            base_url = self.base_url or get_base_url(html, "")

            # Extract structured data using extruct
            data = extruct.extract(
                html,
                base_url=base_url,
                syntaxes=self.syntaxes,
                uniform=True,  # Normalize microdata to JSON-LD style
            )

            # Calculate extraction time
            extraction_time_ms = int((time.perf_counter() - start_time) * 1000)

            # Build metadata with counts and types found
            metadata = self._build_metadata(data)

            # Calculate confidence based on richness of data
            confidence = self._calculate_confidence(data)

            # Serialize to JSON
            content = json.dumps(data, indent=2, ensure_ascii=False)

            return ExtractedContent(
                representation_type=RepresentationType.JSON,
                content=content,
                extractor_name="extruct_v1",
                extraction_time_ms=extraction_time_ms,
                confidence=confidence,
                metadata=metadata,
            )

        except Exception as e:
            # Log the exception for debugging
            logger.debug(f"Extruct extraction failed: {e}")
            return self._empty_result(start_time)

    def _empty_result(self, start_time: float) -> ExtractedContent:
        """
        Create an empty ExtractedContent result.

        Args:
            start_time: Extraction start time for timing calculation

        Returns:
            ExtractedContent with empty JSON object
        """
        extraction_time_ms = int((time.perf_counter() - start_time) * 1000)

        return ExtractedContent(
            representation_type=RepresentationType.JSON,
            content="{}",
            extractor_name="extruct_v1",
            extraction_time_ms=extraction_time_ms,
            confidence=0.0,
            metadata=self._build_metadata({}),
        )

    def _build_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build metadata dictionary with extraction statistics.

        Args:
            data: Extracted structured data dictionary

        Returns:
            Dictionary containing counts and types of extracted data
        """
        # Count items in each category
        json_ld_count = len(data.get("json-ld", []))
        opengraph_count = len(data.get("opengraph", []))
        microdata_count = len(data.get("microdata", []))
        rdfa_count = len(data.get("rdfa", []))

        # Determine which types were found
        types_found = []
        if json_ld_count > 0:
            types_found.append("json-ld")
        if opengraph_count > 0:
            types_found.append("opengraph")
        if microdata_count > 0:
            types_found.append("microdata")
        if rdfa_count > 0:
            types_found.append("rdfa")

        return {
            "json_ld_count": json_ld_count,
            "opengraph_count": opengraph_count,
            "microdata_count": microdata_count,
            "rdfa_count": rdfa_count,
            "total_items": json_ld_count + opengraph_count + microdata_count + rdfa_count,
            "types_found": types_found,
            "syntaxes_requested": self.syntaxes,
        }

    def _calculate_confidence(self, data: Dict[str, Any]) -> float:
        """
        Calculate confidence score based on richness of extracted data.

        Confidence levels:
        - 1.0: Rich data (3+ items or 2+ types)
        - 0.8: Good data (2+ items)
        - 0.5: Minimal data (1 item)
        - 0.3: Very minimal data (only basic OpenGraph)
        - 0.0: No data

        Args:
            data: Extracted structured data dictionary

        Returns:
            Confidence score between 0.0 and 1.0
        """
        json_ld_count = len(data.get("json-ld", []))
        opengraph_count = len(data.get("opengraph", []))
        microdata_count = len(data.get("microdata", []))
        rdfa_count = len(data.get("rdfa", []))

        total_items = json_ld_count + opengraph_count + microdata_count + rdfa_count

        # Count how many types have data
        types_with_data = sum([
            1 if json_ld_count > 0 else 0,
            1 if opengraph_count > 0 else 0,
            1 if microdata_count > 0 else 0,
            1 if rdfa_count > 0 else 0,
        ])

        # No data at all
        if total_items == 0:
            return 0.0

        # Rich structured data: multiple types or many items
        if types_with_data >= 2 or total_items >= 3:
            return 1.0

        # Good data: 2 items
        if total_items >= 2:
            return 0.8

        # Single item
        # JSON-LD or microdata are more valuable than just OpenGraph
        if json_ld_count > 0 or microdata_count > 0:
            return 0.5

        # Only OpenGraph (common but less structured)
        return 0.3
