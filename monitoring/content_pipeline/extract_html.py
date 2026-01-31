"""
Parsel-based HTML Content Extraction

Extracts text content from HTML using CSS and XPath selectors.
Parsel is Scrapy's HTML/XML parsing library built on lxml.

Features:
- CSS selector support (default)
- XPath selector support (prefix with "xpath:")
- Selector fallback (try selectors in order)
- Element removal (remove noise before extraction)
- Whitespace normalization (configurable: aggressive or layout-preserving)
- Configurable fallback chains with FallbackConfig
"""

import re
import time
from typing import List, Optional, TYPE_CHECKING

from parsel import Selector

from monitoring.content_pipeline.models import ExtractedContent, RepresentationType
from monitoring.content_pipeline.normalize import (
    NormalizationMode,
    normalize_aggressive,
    normalize_layout_preserving,
)

if TYPE_CHECKING:
    from monitoring.content_pipeline.config import FallbackConfig


# XPath selector prefix
XPATH_PREFIX = "xpath:"


class SelectorExtractor:
    """
    Extract content from HTML using CSS/XPath selectors.

    Uses parsel (Scrapy's parsing library) for selector evaluation.
    Selectors are tried in order; first match is used.

    Attributes:
        normalization_mode: Whitespace normalization strategy to use.
            - AGGRESSIVE (default): Collapse all whitespace to single spaces
            - LAYOUT_PRESERVING: Preserve line breaks but clean up excessive whitespace
            - NONE: No normalization applied
    """

    def __init__(
        self, normalization_mode: NormalizationMode = NormalizationMode.AGGRESSIVE
    ) -> None:
        """
        Initialize the SelectorExtractor with configuration options.

        Args:
            normalization_mode: Whitespace normalization strategy (default: AGGRESSIVE)
        """
        self.normalization_mode = normalization_mode

    def extract(
        self,
        html: Optional[str],
        selectors: List[str],
        remove_selectors: Optional[List[str]] = None,
        fallback_config: Optional["FallbackConfig"] = None,
    ) -> ExtractedContent:
        """
        Extract content using CSS selectors with configurable fallback behavior.

        Args:
            html: Raw HTML content
            selectors: List of CSS selectors to try (in order)
            remove_selectors: Optional selectors for elements to remove (noise)
            fallback_config: Optional FallbackConfig for controlling fallback behavior

        Returns:
            ExtractedContent with extracted text and metadata about fallback behavior
        """
        start_time = time.perf_counter()

        # Handle empty/None HTML
        if not html or not html.strip():
            return self._empty_result(start_time)

        # Handle empty selectors
        if not selectors:
            return self._empty_result(start_time)

        # Parse HTML with parsel
        sel = Selector(text=html)

        # Remove unwanted elements before extraction
        if remove_selectors:
            sel = self._remove_elements(sel, remove_selectors)

        # If no fallback_config, use legacy behavior
        if fallback_config is None:
            return self._extract_legacy(sel, selectors, start_time)

        # Build selector list with body fallback if configured
        effective_selectors = list(selectors)
        if fallback_config.always_include_body and "body" not in effective_selectors:
            effective_selectors.append("body")

        selectors_tried: List[str] = []
        fallback_triggered = False

        # Try selectors in order with fallback chain logic
        for idx, selector in enumerate(effective_selectors):
            selectors_tried.append(selector)

            # Check if element exists
            element_exists = self._selector_matches(sel, selector)

            if not element_exists:
                # Selector doesn't match - try next
                fallback_triggered = True if idx > 0 else fallback_triggered
                continue

            # Element exists - try to extract content
            content, _ = self._try_selector(sel, selector)

            # Handle empty content based on fallback_on_empty
            if not content or not content.strip():
                if not fallback_config.fallback_on_empty:
                    # Stop here even if empty
                    extraction_time = int((time.perf_counter() - start_time) * 1000)
                    return ExtractedContent(
                        representation_type=RepresentationType.TEXT,
                        content="",
                        extractor_name="parsel_v1",
                        extraction_time_ms=extraction_time,
                        confidence=0.5,
                        metadata={
                            "selector_used": selector,
                            "selector_index": idx,
                            "selectors_tried": selectors_tried,
                            "fallback_triggered": fallback_triggered,
                        },
                    )
                # fallback_on_empty=True, try next selector
                fallback_triggered = True
                continue

            # Normalize whitespace
            content = self._normalize_whitespace(content)

            # Check min_chars threshold
            if len(content) < fallback_config.min_chars:
                # Content too short - try next selector
                fallback_triggered = True
                continue

            # Success! Calculate confidence
            extraction_time = int((time.perf_counter() - start_time) * 1000)
            confidence = self._calculate_confidence(
                content=content,
                selector_used=selector,
                is_body_fallback=(selector == "body" and selector not in selectors),
            )

            return ExtractedContent(
                representation_type=RepresentationType.TEXT,
                content=content,
                extractor_name="parsel_v1",
                extraction_time_ms=extraction_time,
                confidence=confidence,
                metadata={
                    "selector_used": selector,
                    "selector_index": idx,
                    "selectors_tried": selectors_tried,
                    "fallback_triggered": idx > 0 or fallback_triggered,
                },
            )

        # No selector produced adequate content
        extraction_time = int((time.perf_counter() - start_time) * 1000)
        return ExtractedContent(
            representation_type=RepresentationType.TEXT,
            content="",
            extractor_name="parsel_v1",
            extraction_time_ms=extraction_time,
            confidence=0.0,
            metadata={
                "selector_used": None,
                "selectors_tried": selectors_tried,
                "fallback_triggered": True,
            },
        )

    def _extract_legacy(
        self, sel: Selector, selectors: List[str], start_time: float
    ) -> ExtractedContent:
        """
        Legacy extraction behavior (no FallbackConfig).

        Preserves backward compatibility with original extract() behavior.

        Args:
            sel: Parsel Selector object
            selectors: List of CSS/XPath selectors to try
            start_time: Extraction start time for timing

        Returns:
            ExtractedContent with extracted text
        """
        for idx, selector in enumerate(selectors):
            content, used_selector = self._try_selector(sel, selector)
            if content:
                # Normalize whitespace
                content = self._normalize_whitespace(content)
                extraction_time = int((time.perf_counter() - start_time) * 1000)

                return ExtractedContent(
                    representation_type=RepresentationType.TEXT,
                    content=content,
                    extractor_name="parsel_v1",
                    extraction_time_ms=extraction_time,
                    metadata={
                        "selector_used": selector,
                        "selector_index": idx,
                    },
                )

        # No selector matched
        return self._empty_result(start_time)

    def _selector_matches(self, sel: Selector, selector: str) -> bool:
        """
        Check if a selector matches any elements in the document.

        Args:
            sel: Parsel Selector object
            selector: CSS or XPath selector

        Returns:
            True if selector matches at least one element
        """
        try:
            if selector.startswith(XPATH_PREFIX):
                xpath = selector[len(XPATH_PREFIX):]
                matches = sel.xpath(xpath)
            else:
                matches = sel.css(selector)
            return len(matches) > 0
        except Exception:
            return False

    def _calculate_confidence(
        self, content: str, selector_used: str, is_body_fallback: bool
    ) -> float:
        """
        Calculate confidence score based on extraction quality.

        Confidence levels:
        - 1.0: Good match (adequate content, not a body fallback)
        - 0.7: Body fallback (had to fall back to body selector)
        - 0.5: Very short content (< 100 chars after all selectors tried)

        Args:
            content: Extracted text content
            selector_used: The selector that produced the content
            is_body_fallback: Whether we fell back to body selector

        Returns:
            Confidence score between 0.0 and 1.0
        """
        # Very short content always gets low confidence
        if len(content) < 100:
            return 0.5

        # Body fallback gets medium confidence
        if is_body_fallback:
            return 0.7

        # Good match
        return 1.0

    def _try_selector(
        self, sel: Selector, selector: str
    ) -> tuple[str, Optional[str]]:
        """
        Try a single selector and return extracted text.

        Args:
            sel: Parsel Selector object
            selector: CSS or XPath selector (XPath prefixed with "xpath:")

        Returns:
            Tuple of (extracted_text, selector_used) or ("", None) if no match
        """
        try:
            if selector.startswith(XPATH_PREFIX):
                # XPath selector
                xpath = selector[len(XPATH_PREFIX):]
                matches = sel.xpath(xpath)
            else:
                # CSS selector
                matches = sel.css(selector)

            if matches:
                # Get text from first matching element
                # Use getall() to get all text, then join
                text_parts = matches[0].xpath(".//text()").getall()
                text = " ".join(text_parts)
                if text.strip():
                    return text, selector

        except Exception:
            # Selector parsing errors are handled gracefully
            pass

        return "", None

    def _remove_elements(
        self, sel: Selector, remove_selectors: List[str]
    ) -> Selector:
        """
        Remove elements matching remove_selectors from the document.

        Since parsel Selectors are read-only, we need to manipulate
        the underlying lxml tree and create a new Selector.

        Args:
            sel: Original Selector
            remove_selectors: List of CSS selectors for elements to remove

        Returns:
            New Selector with elements removed
        """
        # Get the root element from parsel's underlying lxml tree
        root = sel.root

        # Remove elements for each selector
        for remove_sel in remove_selectors:
            try:
                if remove_sel.startswith(XPATH_PREFIX):
                    xpath = remove_sel[len(XPATH_PREFIX):]
                    elements = root.xpath(xpath)
                else:
                    # Convert CSS to XPath for lxml
                    from cssselect import GenericTranslator
                    translator = GenericTranslator()
                    xpath = translator.css_to_xpath(remove_sel)
                    elements = root.xpath(xpath)

                # Remove each matched element
                for elem in elements:
                    parent = elem.getparent()
                    if parent is not None:
                        parent.remove(elem)

            except Exception:
                # Ignore selector errors
                pass

        # Create new Selector from modified tree
        from lxml import etree
        html_str = etree.tostring(root, encoding="unicode", method="html")
        return Selector(text=html_str)

    def _normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace in extracted text based on configured mode.

        Modes:
        - AGGRESSIVE: Collapse all whitespace to single spaces (default)
        - LAYOUT_PRESERVING: Preserve line breaks, clean excessive whitespace
        - NONE: Return text unchanged

        Args:
            text: Raw extracted text

        Returns:
            Normalized text
        """
        if self.normalization_mode == NormalizationMode.NONE:
            return text
        elif self.normalization_mode == NormalizationMode.LAYOUT_PRESERVING:
            return normalize_layout_preserving(text)
        else:
            # Default: AGGRESSIVE
            return normalize_aggressive(text)

    def _empty_result(self, start_time: float) -> ExtractedContent:
        """
        Create an empty ExtractedContent result.

        Args:
            start_time: Extraction start time for timing

        Returns:
            ExtractedContent with empty content
        """
        extraction_time = int((time.perf_counter() - start_time) * 1000)
        return ExtractedContent(
            representation_type=RepresentationType.TEXT,
            content="",
            extractor_name="parsel_v1",
            extraction_time_ms=extraction_time,
            metadata={"selector_used": None},
        )
