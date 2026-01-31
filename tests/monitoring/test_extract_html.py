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


class TestFallbackConfig:
    """Test FallbackConfig dataclass."""

    def test_default_values(self):
        """FallbackConfig should have sensible defaults."""
        from monitoring.content_pipeline.config import FallbackConfig

        config = FallbackConfig()

        assert config.fallback_on_empty is True
        assert config.min_chars == 0
        assert config.always_include_body is True

    def test_custom_values(self):
        """FallbackConfig should accept custom values."""
        from monitoring.content_pipeline.config import FallbackConfig

        config = FallbackConfig(
            fallback_on_empty=False,
            min_chars=100,
            always_include_body=False,
        )

        assert config.fallback_on_empty is False
        assert config.min_chars == 100
        assert config.always_include_body is False


class TestFallbackChainBehavior:
    """Test selector fallback chain behavior with FallbackConfig."""

    def test_fallback_on_empty_tries_next_selector(self):
        """Should try next selector when current returns empty content."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <div id="empty"></div>
                <article>Article content here.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig(fallback_on_empty=True)
        result = extractor.extract(
            html,
            selectors=["#empty", "article"],
            fallback_config=config,
        )

        assert result.content == "Article content here."
        assert result.metadata["selector_used"] == "article"
        assert result.metadata["selector_index"] == 1
        assert result.metadata["selectors_tried"] == ["#empty", "article"]
        assert result.metadata["fallback_triggered"] is True

    def test_fallback_on_empty_false_uses_first_match(self):
        """Should use first matching selector even if empty when fallback_on_empty=False."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <div id="empty"></div>
                <article>Article content here.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig(fallback_on_empty=False)
        result = extractor.extract(
            html,
            selectors=["#empty", "article"],
            fallback_config=config,
        )

        # #empty exists, so it matches (even though empty)
        # Actually, the current implementation checks for non-empty content, so it will skip
        # But with fallback_on_empty=False and selector exists, behavior should differ
        # Let's verify: when fallback_on_empty=False, we stop at first *matched* element
        # This is a design choice - the selector matched but content is empty
        # The test verifies fallback_on_empty=False means "don't try next if element found"
        assert result.content == ""
        assert result.metadata.get("selector_used") == "#empty"
        assert result.metadata.get("fallback_triggered") is False

    def test_min_chars_triggers_fallback(self):
        """Should try next selector when content is below min_chars."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <div id="short">Hi</div>
                <article>This is a much longer article with enough content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig(min_chars=20)
        result = extractor.extract(
            html,
            selectors=["#short", "article"],
            fallback_config=config,
        )

        # "Hi" is 2 chars, below min_chars=20, so should fallback to article
        assert "much longer article" in result.content
        assert result.metadata["selector_used"] == "article"
        assert result.metadata["selectors_tried"] == ["#short", "article"]
        assert result.metadata["fallback_triggered"] is True

    def test_always_include_body_as_ultimate_fallback(self):
        """Should fall back to 'body' when no selector matches and always_include_body=True."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <div>Some body content here.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig(always_include_body=True)
        result = extractor.extract(
            html,
            selectors=["#nonexistent", ".missing"],
            fallback_config=config,
        )

        assert "Some body content here" in result.content
        assert result.metadata["selector_used"] == "body"
        assert result.metadata["selectors_tried"] == ["#nonexistent", ".missing", "body"]
        assert result.metadata["fallback_triggered"] is True

    def test_always_include_body_false_returns_empty(self):
        """Should return empty when no selector matches and always_include_body=False."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <div>Some body content here.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig(always_include_body=False)
        result = extractor.extract(
            html,
            selectors=["#nonexistent", ".missing"],
            fallback_config=config,
        )

        assert result.content == ""
        assert result.metadata.get("selector_used") is None
        assert result.metadata.get("selectors_tried") == ["#nonexistent", ".missing"]

    def test_selectors_tried_tracked_in_metadata(self):
        """Should track all selectors tried in metadata."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <div class="found">Content found.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig()
        result = extractor.extract(
            html,
            selectors=["#missing1", ".missing2", ".found"],
            fallback_config=config,
        )

        assert result.content == "Content found."
        assert result.metadata["selectors_tried"] == ["#missing1", ".missing2", ".found"]
        assert result.metadata["selector_used"] == ".found"

    def test_fallback_triggered_false_when_first_selector_works(self):
        """Should set fallback_triggered=False when first selector works."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <article>Article content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig()
        result = extractor.extract(
            html,
            selectors=["article", ".backup"],
            fallback_config=config,
        )

        assert result.content == "Article content."
        assert result.metadata["fallback_triggered"] is False
        assert result.metadata["selectors_tried"] == ["article"]


class TestFallbackConfidenceScoring:
    """Test confidence scoring based on fallback behavior."""

    def test_confidence_high_for_first_selector_match(self):
        """Should have high confidence (1.0) when first selector matches well."""
        from monitoring.content_pipeline.config import FallbackConfig

        # Content must be >= 100 chars for high confidence
        html = """
        <html>
            <body>
                <article>This is a good article with plenty of content to extract. It contains enough text to be considered a quality extraction result with more than one hundred characters total.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig()
        result = extractor.extract(
            html,
            selectors=["article"],
            fallback_config=config,
        )

        assert len(result.content) >= 100  # Verify test setup
        assert result.confidence == 1.0

    def test_confidence_medium_for_body_fallback(self):
        """Should have medium confidence (0.7) when falling back to body."""
        from monitoring.content_pipeline.config import FallbackConfig

        # Content must be >= 100 chars for body fallback to get 0.7 (not 0.5 for short)
        html = """
        <html>
            <body>
                <div>This is body content with plenty of text to extract here. The content needs to be long enough to pass the 100 character threshold for quality confidence scoring.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig(always_include_body=True)
        result = extractor.extract(
            html,
            selectors=["#nonexistent"],
            fallback_config=config,
        )

        assert len(result.content) >= 100  # Verify test setup
        assert result.confidence == 0.7
        assert result.metadata["selector_used"] == "body"

    def test_confidence_low_for_very_short_content(self):
        """Should have low confidence (0.5) when extracted content is very short."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <article>Short</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        # min_chars=0 so we don't skip, but confidence should still be low
        config = FallbackConfig(min_chars=0)
        result = extractor.extract(
            html,
            selectors=["article"],
            fallback_config=config,
        )

        # "Short" is 5 chars, which is < 100, so confidence should be 0.5
        assert result.confidence == 0.5

    def test_confidence_normal_for_adequate_content(self):
        """Should have normal confidence (1.0) for content >= 100 chars."""
        from monitoring.content_pipeline.config import FallbackConfig

        html = """
        <html>
            <body>
                <article>This is a much longer article that contains more than one hundred characters of actual text content for extraction purposes.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        config = FallbackConfig()
        result = extractor.extract(
            html,
            selectors=["article"],
            fallback_config=config,
        )

        assert len(result.content) >= 100
        assert result.confidence == 1.0


class TestBackwardCompatibility:
    """Test backward compatibility when FallbackConfig is not provided."""

    def test_extract_without_fallback_config(self):
        """Should work as before when fallback_config is not provided."""
        html = """
        <html>
            <body>
                <article>Article content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"])

        assert result.content == "Article content."
        assert result.metadata["selector_used"] == "article"
        # Original behavior: no selectors_tried or fallback_triggered
        # These fields may or may not be present - depends on implementation
        # But content extraction should still work

    def test_extract_with_none_fallback_config(self):
        """Should work when fallback_config is explicitly None."""
        html = """
        <html>
            <body>
                <article>Article content.</article>
            </body>
        </html>
        """
        extractor = SelectorExtractor()
        result = extractor.extract(html, selectors=["article"], fallback_config=None)

        assert result.content == "Article content."
        assert result.metadata["selector_used"] == "article"


class TestNormalizationMode:
    """Test configurable whitespace normalization modes."""

    def test_default_normalization_mode_is_aggressive(self):
        """Default normalization should be aggressive (collapse all whitespace)."""
        from monitoring.content_pipeline.normalize import NormalizationMode

        extractor = SelectorExtractor()
        assert extractor.normalization_mode == NormalizationMode.AGGRESSIVE

    def test_aggressive_mode_collapses_newlines(self):
        """Aggressive mode should collapse newlines to spaces."""
        from monitoring.content_pipeline.normalize import NormalizationMode

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
        extractor = SelectorExtractor(normalization_mode=NormalizationMode.AGGRESSIVE)
        result = extractor.extract(html, selectors=["div"])

        # Newlines should be collapsed to single space
        assert "\n" not in result.content
        assert "Line one. Line two." == result.content

    def test_layout_preserving_mode_keeps_newlines(self):
        """Layout-preserving mode should keep line structure."""
        from monitoring.content_pipeline.normalize import NormalizationMode

        html = """
        <html>
            <body>
                <div>Line one.
Line two.</div>
            </body>
        </html>
        """
        extractor = SelectorExtractor(
            normalization_mode=NormalizationMode.LAYOUT_PRESERVING
        )
        result = extractor.extract(html, selectors=["div"])

        # Line breaks should be preserved
        assert "\n" in result.content
        assert "Line one." in result.content
        assert "Line two." in result.content

    def test_layout_preserving_mode_collapses_excessive_blank_lines(self):
        """Layout-preserving mode should collapse 3+ blank lines to 2."""
        from monitoring.content_pipeline.normalize import NormalizationMode

        html = """<div>A




B</div>"""
        extractor = SelectorExtractor(
            normalization_mode=NormalizationMode.LAYOUT_PRESERVING
        )
        result = extractor.extract(html, selectors=["div"])

        # Should not have 4+ consecutive newlines
        assert "\n\n\n\n" not in result.content
        # But should still have some blank lines
        assert "\n" in result.content

    def test_none_mode_preserves_all_whitespace(self):
        """NONE mode should not modify whitespace at all."""
        from monitoring.content_pipeline.normalize import NormalizationMode

        html = "<div>Text    with     spaces</div>"
        extractor = SelectorExtractor(normalization_mode=NormalizationMode.NONE)
        result = extractor.extract(html, selectors=["div"])

        # Multiple spaces should be preserved
        assert "    " in result.content or "     " in result.content

    def test_constructor_accepts_normalization_mode(self):
        """Constructor should accept normalization_mode parameter."""
        from monitoring.content_pipeline.normalize import NormalizationMode

        extractor = SelectorExtractor(
            normalization_mode=NormalizationMode.LAYOUT_PRESERVING
        )
        assert extractor.normalization_mode == NormalizationMode.LAYOUT_PRESERVING
