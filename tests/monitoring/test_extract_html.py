"""Tests for parsel-based HTML extraction (extract_html.py).

Tests cover:
- CSS selector extraction
- XPath selector extraction
- Selector fallback behavior (try in order)
- Element removal before extraction
- Text normalization (whitespace collapse)
- Edge cases (empty HTML, no matches, invalid HTML)
"""

import pytest
from monitoring.content_pipeline.extract_html import SelectorExtractor
from monitoring.content_pipeline.models import RepresentationType


class TestSelectorExtractorBasic:
    """Basic extraction tests."""

    def test_extract_with_single_css_selector(self):
        """Should extract text from element matching CSS selector."""
        html = """
        <html>
            <body>
                <article>This is the main content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"])

        assert result.content == "This is the main content."
        assert result.representation_type == RepresentationType.TEXT
        assert result.extractor_name == "parsel_v1"
        assert result.metadata["selector_used"] == "article"
        assert result.metadata["selector_index"] == 0

    def test_extract_with_class_selector(self):
        """Should extract text from element matching class selector."""
        html = """
        <html>
            <body>
                <div class="content">Main content here.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=[".content"])

        assert result.content == "Main content here."
        assert result.metadata["selector_used"] == ".content"

    def test_extract_with_id_selector(self):
        """Should extract text from element matching ID selector."""
        html = """
        <html>
            <body>
                <div id="main">ID-based content.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["#main"])

        assert result.content == "ID-based content."
        assert result.metadata["selector_used"] == "#main"


class TestSelectorFallback:
    """Test selector fallback behavior."""

    def test_uses_first_matching_selector(self):
        """Should use the first selector that matches."""
        html = """
        <html>
            <body>
                <main>Main content.</main>
                <article>Article content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["main", "article"])

        assert result.content == "Main content."
        assert result.metadata["selector_used"] == "main"
        assert result.metadata["selector_index"] == 0

    def test_falls_back_to_second_selector(self):
        """Should fall back to second selector if first doesn't match."""
        html = """
        <html>
            <body>
                <article>Article content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["main", "article"])

        assert result.content == "Article content."
        assert result.metadata["selector_used"] == "article"
        assert result.metadata["selector_index"] == 1

    def test_falls_back_through_multiple_selectors(self):
        """Should try selectors in order until one matches."""
        html = """
        <html>
            <body>
                <div class="post">Post content.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html,
            selectors=["article", "main", "#content", ".post"],
        )

        assert result.content == "Post content."
        assert result.metadata["selector_used"] == ".post"
        assert result.metadata["selector_index"] == 3


class TestXPathSupport:
    """Test XPath selector support."""

    def test_xpath_selector_with_prefix(self):
        """Should use XPath when selector has xpath: prefix."""
        html = """
        <html>
            <body>
                <div>
                    <p>Paragraph text.</p>
                </div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["xpath://div/p"])

        assert result.content == "Paragraph text."
        assert result.metadata["selector_used"] == "xpath://div/p"

    def test_xpath_selector_with_attribute(self):
        """Should support XPath with attribute filters."""
        html = """
        <html>
            <body>
                <div class="a">First div.</div>
                <div class="b">Second div.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html, selectors=['xpath://div[@class="b"]']
        )

        assert result.content == "Second div."

    def test_mix_css_and_xpath_selectors(self):
        """Should support mixing CSS and XPath selectors."""
        html = """
        <html>
            <body>
                <section>Section content.</section>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html, selectors=["article", "xpath://section"]
        )

        assert result.content == "Section content."
        assert result.metadata["selector_used"] == "xpath://section"
        assert result.metadata["selector_index"] == 1


class TestRemoveSelectors:
    """Test element removal before extraction."""

    def test_remove_single_element(self):
        """Should remove elements matching remove_selectors before extracting."""
        html = """
        <html>
            <body>
                <article>
                    <p>Main paragraph.</p>
                    <nav>Navigation links.</nav>
                </article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html, selectors=["article"], remove_selectors=["nav"]
        )

        assert "Navigation links" not in result.content
        assert "Main paragraph" in result.content

    def test_remove_multiple_elements(self):
        """Should remove all elements matching remove_selectors."""
        html = """
        <html>
            <body>
                <article>
                    <p>Content.</p>
                    <aside>Sidebar.</aside>
                    <footer>Footer.</footer>
                </article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html,
            selectors=["article"],
            remove_selectors=["aside", "footer"],
        )

        assert "Content" in result.content
        assert "Sidebar" not in result.content
        assert "Footer" not in result.content

    def test_remove_nested_elements(self):
        """Should remove nested elements matching remove_selectors."""
        html = """
        <html>
            <body>
                <main>
                    <p>Keep this.</p>
                    <div class="ads">
                        <p>Remove this ad.</p>
                    </div>
                    <p>Keep this too.</p>
                </main>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html, selectors=["main"], remove_selectors=[".ads"]
        )

        assert "Keep this" in result.content
        assert "Remove this ad" not in result.content
        assert "Keep this too" in result.content


class TestTextNormalization:
    """Test whitespace normalization in extracted text."""

    def test_collapse_multiple_spaces(self):
        """Should collapse multiple spaces into single space."""
        html = """
        <html>
            <body>
                <p>Text    with     many      spaces.</p>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["p"])

        assert result.content == "Text with many spaces."

    def test_collapse_multiple_newlines(self):
        """Should collapse multiple newlines."""
        html = """
        <html>
            <body>
                <div>
                    Line one.



                    Line two.
                </div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["div"])

        # Multiple newlines should be collapsed to single newline/space
        assert "Line one." in result.content
        assert "Line two." in result.content
        # No excessive whitespace
        assert "\n\n\n" not in result.content

    def test_trim_leading_trailing_whitespace(self):
        """Should trim leading and trailing whitespace."""
        html = """
        <html>
            <body>
                <p>   Content with padding   </p>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["p"])

        assert result.content == "Content with padding"

    def test_preserve_basic_structure(self):
        """Should preserve some structure between block elements."""
        html = """
        <html>
            <body>
                <article>
                    <h1>Title</h1>
                    <p>First paragraph.</p>
                    <p>Second paragraph.</p>
                </article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"])

        # Content should be present
        assert "Title" in result.content
        assert "First paragraph" in result.content
        assert "Second paragraph" in result.content


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_html_returns_empty_content(self):
        """Empty HTML should return ExtractedContent with empty content."""
        extractor = SelectorExtractor()
        result = extractor.extract("", selectors=["article"])

        assert result.content == ""
        assert result.representation_type == RepresentationType.TEXT
        assert result.extractor_name == "parsel_v1"

    def test_no_matching_selectors_returns_empty(self):
        """No matching selectors should return empty content."""
        html = """
        <html>
            <body>
                <div>Some content.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(
            html, selectors=["article", "main", "#nonexistent"]
        )

        assert result.content == ""

    def test_empty_selectors_list_returns_empty(self):
        """Empty selectors list should return empty content."""
        html = """
        <html>
            <body>
                <div>Content here.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=[])

        assert result.content == ""

    def test_invalid_html_handled_gracefully(self):
        """Invalid/malformed HTML should be handled gracefully."""
        # Parsel/lxml is lenient with malformed HTML
        html = "<div><p>Unclosed paragraph<span>Nested</div>"
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["div"])

        # Should still extract something
        assert "Unclosed paragraph" in result.content or "Nested" in result.content

    def test_none_html_returns_empty(self):
        """None HTML should return empty content."""
        extractor = SelectorExtractor()
        result = extractor.extract(None, selectors=["article"])

        assert result.content == ""

    def test_whitespace_only_html_returns_empty(self):
        """Whitespace-only HTML should return empty content."""
        extractor = SelectorExtractor()
        result = extractor.extract("   \n\t  \n   ", selectors=["article"])

        assert result.content == ""


class TestMetadata:
    """Test metadata in ExtractedContent."""

    def test_metadata_includes_selector_used(self):
        """Metadata should include the selector that was used."""
        html = "<article>Content</article>"
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"])

        assert "selector_used" in result.metadata
        assert result.metadata["selector_used"] == "article"

    def test_metadata_includes_selector_index(self):
        """Metadata should include the index of selector used."""
        html = "<main>Content</main>"
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article", "main"])

        assert "selector_index" in result.metadata
        assert result.metadata["selector_index"] == 1

    def test_metadata_when_no_match(self):
        """Metadata should indicate no match when selectors don't match."""
        html = "<div>Content</div>"
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"])

        # No selector_used when nothing matches
        assert result.metadata.get("selector_used") is None


class TestComplexSelectors:
    """Test complex CSS selectors."""

    def test_descendant_selector(self):
        """Should support descendant selectors."""
        html = """
        <html>
            <body>
                <article>
                    <div class="content">
                        <p>Nested content.</p>
                    </div>
                </article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article .content p"])

        assert result.content == "Nested content."

    def test_child_selector(self):
        """Should support direct child selectors."""
        html = """
        <html>
            <body>
                <div>
                    <p>Direct child.</p>
                    <div><p>Nested child.</p></div>
                </div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["div > p"])

        assert result.content == "Direct child."

    def test_attribute_selector(self):
        """Should support attribute selectors."""
        html = """
        <html>
            <body>
                <div data-type="content">Attribute match.</div>
                <div>No attribute.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=['div[data-type="content"]'])

        assert result.content == "Attribute match."


class TestExtractionTiming:
    """Test extraction timing metadata."""

    def test_extraction_time_is_recorded(self):
        """Extraction time should be recorded in metadata."""
        html = "<article>Content</article>"
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"])

        # extraction_time_ms should be set (>= 0)
        assert result.extraction_time_ms >= 0
