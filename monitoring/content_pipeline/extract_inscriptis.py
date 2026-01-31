"""
Inscriptis-based HTML Content Extraction with Table/Grid Preservation

Converts HTML to text using inscriptis library which preserves:
- Table structure and column alignment
- List formatting (ordered and unordered)
- Basic block element structure

This extractor is ideal for pages where layout preservation matters,
such as pricing pages, comparison tables, and data grids.

Usage:
    from monitoring.content_pipeline.extract_inscriptis import InscriptisExtractor

    extractor = InscriptisExtractor(
        table_cell_separator="  ",  # Default double-space
        display_links=False,         # Don't show link URLs
    )
    result = extractor.extract(html)
"""

import logging
import time
from typing import Any, Dict, Optional

from inscriptis import get_text

logger = logging.getLogger(__name__)
from inscriptis.model.config import ParserConfig

from monitoring.content_pipeline.models import ExtractedContent, RepresentationType


class InscriptisExtractor:
    """
    Extract content from HTML with table/grid layout preservation.

    Uses inscriptis library for HTML-to-text conversion that maintains
    visual structure like tables, lists, and block elements.

    Attributes:
        table_cell_separator: String used to separate table cells (default: "  ")
        display_links: Whether to show link URLs in output (default: False)
        display_images: Whether to show image alt text (default: False)
        display_anchors: Whether to show anchor names (default: False)
    """

    def __init__(
        self,
        table_cell_separator: str = "  ",
        display_links: bool = False,
        display_images: bool = False,
        display_anchors: bool = False,
    ) -> None:
        """
        Initialize the InscriptisExtractor with configuration options.

        Args:
            table_cell_separator: String to separate table cells (default: "  ")
            display_links: Include link URLs in output (default: False)
            display_images: Include image alt text in output (default: False)
            display_anchors: Include anchor names in output (default: False)
        """
        self.table_cell_separator = table_cell_separator
        self.display_links = display_links
        self.display_images = display_images
        self.display_anchors = display_anchors

        # Create ParserConfig with our settings
        self._parser_config = ParserConfig(
            display_links=display_links,
            display_images=display_images,
            display_anchors=display_anchors,
            table_cell_separator=table_cell_separator,
        )

    def extract(self, html: Optional[str]) -> ExtractedContent:
        """
        Extract text content from HTML with layout preservation.

        Args:
            html: Raw HTML content to extract text from

        Returns:
            ExtractedContent with extracted text and metadata
        """
        start_time = time.perf_counter()

        # Handle empty/None HTML
        if not html or not html.strip():
            return self._empty_result(start_time)

        try:
            # Use inscriptis to convert HTML to text
            text = get_text(html, self._parser_config)

            # Calculate extraction time
            extraction_time_ms = int((time.perf_counter() - start_time) * 1000)

            # Calculate confidence based on content length
            confidence = self._calculate_confidence(text)

            return ExtractedContent(
                representation_type=RepresentationType.TEXT,
                content=text,
                extractor_name="inscriptis_v1",
                extraction_time_ms=extraction_time_ms,
                confidence=confidence,
                metadata=self._build_metadata(),
            )

        except Exception as e:
            # Log the exception for debugging
            logger.debug(f"Inscriptis extraction failed: {e}")
            return self._empty_result(start_time)

    def _empty_result(self, start_time: float) -> ExtractedContent:
        """
        Create an empty ExtractedContent result.

        Args:
            start_time: Extraction start time for timing calculation

        Returns:
            ExtractedContent with empty content
        """
        extraction_time_ms = int((time.perf_counter() - start_time) * 1000)

        return ExtractedContent(
            representation_type=RepresentationType.TEXT,
            content="",
            extractor_name="inscriptis_v1",
            extraction_time_ms=extraction_time_ms,
            confidence=0.0,
            metadata=self._build_metadata(),
        )

    def _calculate_confidence(self, text: str) -> float:
        """
        Calculate confidence score based on content length.

        Confidence levels:
        - 1.0: Normal output (>= 200 chars)
        - 0.8: Short output (50-199 chars)
        - 0.5: Very short output (< 50 chars)

        Args:
            text: Extracted text content

        Returns:
            Confidence score between 0.0 and 1.0
        """
        content_length = len(text.strip())

        if content_length < 50:
            return 0.5
        elif content_length < 200:
            return 0.8
        else:
            return 1.0

    def _build_metadata(self) -> Dict[str, Any]:
        """
        Build metadata dictionary with configuration info.

        Returns:
            Dictionary containing extractor configuration
        """
        return {
            "display_links": self.display_links,
            "display_images": self.display_images,
            "display_anchors": self.display_anchors,
            "table_cell_separator": self.table_cell_separator,
        }
