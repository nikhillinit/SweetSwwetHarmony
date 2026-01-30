"""
Parsel-based HTML Content Extraction

Extracts text content from HTML using CSS and XPath selectors.
Parsel is Scrapy's HTML/XML parsing library built on lxml.

Features:
- CSS selector support (default)
- XPath selector support (prefix with "xpath:")
- Selector fallback (try selectors in order)
- Element removal (remove noise before extraction)
- Whitespace normalization
"""

import re
import time
from typing import List, Optional

from parsel import Selector

from monitoring.content_pipeline.models import ExtractedContent, RepresentationType


# XPath selector prefix
XPATH_PREFIX = "xpath:"


class SelectorExtractor:
    """
    Extract content from HTML using CSS/XPath selectors.

    Uses parsel (Scrapy's parsing library) for selector evaluation.
    Selectors are tried in order; first match is used.
    """

    def extract(
        self,
        html: Optional[str],
        selectors: List[str],
        remove_selectors: Optional[List[str]] = None,
    ) -> ExtractedContent:
        """
        Extract content using CSS selectors.

        Args:
            html: Raw HTML content
            selectors: List of CSS selectors to try (in order)
            remove_selectors: Optional selectors for elements to remove (noise)

        Returns:
            ExtractedContent with extracted text
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

        # Try selectors in order
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
        Normalize whitespace in extracted text.

        - Collapse multiple spaces to single space
        - Collapse multiple newlines
        - Trim leading/trailing whitespace

        Args:
            text: Raw extracted text

        Returns:
            Normalized text
        """
        # Replace multiple whitespace (including newlines) with single space
        text = re.sub(r"\s+", " ", text)
        # Trim leading/trailing whitespace
        text = text.strip()
        return text

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
