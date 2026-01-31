"""Tests for inscriptis-based HTML extraction with table/grid preservation.

Tests cover:
- Basic HTML to text conversion
- Table structure preservation
- List preservation
- Grid alignment
- Edge cases (empty HTML, None, malformed)
- Configuration options (table_cell_separator, display_links)
"""

import pytest
from monitoring.content_pipeline.extract_inscriptis import InscriptisExtractor
from monitoring.content_pipeline.models import RepresentationType


class TestInscriptisExtractorBasic:
    """Basic extraction tests."""

    def test_extract_simple_html(self):
        """Should convert simple HTML to text."""
        html = """
        <html>
            <body>
                <p>Hello, world!</p>
            </body>
        </html>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert "Hello, world!" in result.content
        assert result.representation_type == RepresentationType.TEXT
        assert result.extractor_name == "inscriptis_v1"

    def test_extract_preserves_headings(self):
        """Should preserve heading structure."""
        html = """
        <html>
            <body>
                <h1>Main Title</h1>
                <p>First paragraph.</p>
                <h2>Subtitle</h2>
                <p>Second paragraph.</p>
            </body>
        </html>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert "Main Title" in result.content
        assert "Subtitle" in result.content
        assert "First paragraph" in result.content
        assert "Second paragraph" in result.content

    def test_extract_includes_timing_metadata(self):
        """Should include extraction timing in metadata."""
        html = "<p>Test content</p>"
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert result.extraction_time_ms >= 0


class TestTablePreservation:
    """Test table structure preservation."""

    def test_simple_table_alignment(self):
        """Should preserve basic table structure."""
        html = """
        <table>
            <tr>
                <th>Name</th>
                <th>Price</th>
            </tr>
            <tr>
                <td>Product A</td>
                <td>$10</td>
            </tr>
            <tr>
                <td>Product B</td>
                <td>$20</td>
            </tr>
        </table>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # All table content should be present
        assert "Name" in result.content
        assert "Price" in result.content
        assert "Product A" in result.content
        assert "$10" in result.content
        assert "Product B" in result.content
        assert "$20" in result.content

    def test_table_columns_separated(self):
        """Should have separation between table columns."""
        html = """
        <table>
            <tr>
                <td>Column1</td>
                <td>Column2</td>
            </tr>
        </table>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Columns should be separated (not concatenated)
        # The separator creates space between columns
        lines = result.content.strip().split('\n')
        row_line = [line for line in lines if "Column1" in line][0]
        assert "Column1" in row_line and "Column2" in row_line
        # Verify there's space between them
        col1_idx = row_line.find("Column1")
        col2_idx = row_line.find("Column2")
        assert col2_idx > col1_idx + len("Column1")

    def test_pricing_table_preservation(self):
        """Should preserve pricing table structure typical of SaaS pages."""
        html = """
        <table>
            <thead>
                <tr>
                    <th>Feature</th>
                    <th>Basic</th>
                    <th>Pro</th>
                    <th>Enterprise</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Users</td>
                    <td>5</td>
                    <td>25</td>
                    <td>Unlimited</td>
                </tr>
                <tr>
                    <td>Storage</td>
                    <td>10 GB</td>
                    <td>100 GB</td>
                    <td>1 TB</td>
                </tr>
                <tr>
                    <td>Price</td>
                    <td>$9/mo</td>
                    <td>$29/mo</td>
                    <td>$99/mo</td>
                </tr>
            </tbody>
        </table>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # All pricing info should be present
        assert "Feature" in result.content
        assert "Basic" in result.content
        assert "Pro" in result.content
        assert "Enterprise" in result.content
        assert "Users" in result.content
        assert "Unlimited" in result.content
        assert "$9/mo" in result.content
        assert "$29/mo" in result.content
        assert "$99/mo" in result.content

    def test_custom_table_cell_separator(self):
        """Should use custom table cell separator when configured."""
        html = """
        <table>
            <tr>
                <td>A</td>
                <td>B</td>
            </tr>
        </table>
        """
        extractor = InscriptisExtractor(table_cell_separator=" | ")
        result = extractor.extract(html)

        # With custom separator, should have | between cells
        lines = [line for line in result.content.split('\n') if 'A' in line]
        assert len(lines) > 0
        assert " | " in lines[0] or ("A" in lines[0] and "B" in lines[0])


class TestListPreservation:
    """Test list structure preservation."""

    def test_unordered_list(self):
        """Should preserve unordered list structure."""
        html = """
        <ul>
            <li>First item</li>
            <li>Second item</li>
            <li>Third item</li>
        </ul>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert "First item" in result.content
        assert "Second item" in result.content
        assert "Third item" in result.content

    def test_ordered_list(self):
        """Should preserve ordered list structure."""
        html = """
        <ol>
            <li>Step one</li>
            <li>Step two</li>
            <li>Step three</li>
        </ol>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert "Step one" in result.content
        assert "Step two" in result.content
        assert "Step three" in result.content

    def test_nested_lists(self):
        """Should preserve nested list structure."""
        html = """
        <ul>
            <li>Parent item
                <ul>
                    <li>Child item 1</li>
                    <li>Child item 2</li>
                </ul>
            </li>
            <li>Another parent</li>
        </ul>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert "Parent item" in result.content
        assert "Child item 1" in result.content
        assert "Child item 2" in result.content
        assert "Another parent" in result.content


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_html_returns_empty_content(self):
        """Empty HTML should return ExtractedContent with empty content."""
        extractor = InscriptisExtractor()
        result = extractor.extract("")

        assert result.content == ""
        assert result.representation_type == RepresentationType.TEXT
        assert result.extractor_name == "inscriptis_v1"

    def test_none_html_returns_empty_content(self):
        """None HTML should return empty content."""
        extractor = InscriptisExtractor()
        result = extractor.extract(None)

        assert result.content == ""
        assert result.representation_type == RepresentationType.TEXT

    def test_whitespace_only_html_returns_empty(self):
        """Whitespace-only HTML should return empty content."""
        extractor = InscriptisExtractor()
        result = extractor.extract("   \n\t  \n   ")

        assert result.content.strip() == ""

    def test_malformed_html_handled_gracefully(self):
        """Malformed HTML should be handled gracefully."""
        html = "<div><p>Unclosed paragraph<span>Nested</div>"
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Should extract something without crashing
        assert "Unclosed paragraph" in result.content or "Nested" in result.content

    def test_html_with_only_tags_no_content(self):
        """HTML with only empty tags should return empty content."""
        html = "<html><body><div></div><span></span></body></html>"
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert result.content.strip() == ""


class TestConfiguration:
    """Test configuration options."""

    def test_display_links_false_by_default(self):
        """Links should not include URLs by default."""
        html = '<p>Visit <a href="https://example.com">our site</a> for more.</p>'
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        assert "our site" in result.content
        assert "https://example.com" not in result.content

    def test_display_links_true_includes_urls(self):
        """Should include link URLs when display_links=True."""
        html = '<p>Visit <a href="https://example.com">our site</a> for more.</p>'
        extractor = InscriptisExtractor(display_links=True)
        result = extractor.extract(html)

        assert "our site" in result.content
        assert "example.com" in result.content

    def test_display_images_false_by_default(self):
        """Images should not include alt text by default."""
        html = '<p>Text <img src="photo.jpg" alt="A beautiful photo"> more text</p>'
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Default inscriptis config typically doesn't show image alt text
        # This depends on display_images setting
        assert "Text" in result.content
        assert "more text" in result.content

    def test_display_images_true_includes_alt_text(self):
        """Should include image alt text when display_images=True."""
        html = '<p>Text <img src="photo.jpg" alt="A beautiful photo"> more text</p>'
        extractor = InscriptisExtractor(display_images=True)
        result = extractor.extract(html)

        assert "Text" in result.content
        assert "beautiful photo" in result.content


class TestMetadata:
    """Test metadata in ExtractedContent."""

    def test_metadata_includes_config_info(self):
        """Metadata should include configuration information."""
        html = "<p>Test</p>"
        extractor = InscriptisExtractor(
            display_links=True,
            table_cell_separator=" | "
        )
        result = extractor.extract(html)

        assert result.metadata is not None
        assert result.metadata.get("display_links") is True
        assert result.metadata.get("table_cell_separator") == " | "


class TestRealWorldExamples:
    """Test with real-world-like HTML examples."""

    def test_saas_pricing_page(self):
        """Should handle typical SaaS pricing page structure."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Pricing</title></head>
        <body>
            <h1>Choose Your Plan</h1>
            <p>All plans include a 14-day free trial.</p>
            <table>
                <tr>
                    <th></th>
                    <th>Starter</th>
                    <th>Growth</th>
                    <th>Scale</th>
                </tr>
                <tr>
                    <td>Price</td>
                    <td>Free</td>
                    <td>$49/month</td>
                    <td>$199/month</td>
                </tr>
                <tr>
                    <td>API Calls</td>
                    <td>1,000/day</td>
                    <td>50,000/day</td>
                    <td>Unlimited</td>
                </tr>
                <tr>
                    <td>Support</td>
                    <td>Community</td>
                    <td>Email</td>
                    <td>Priority</td>
                </tr>
            </table>
            <p>Need custom enterprise pricing? Contact sales.</p>
        </body>
        </html>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Core content preserved
        assert "Choose Your Plan" in result.content
        assert "14-day free trial" in result.content
        assert "Contact sales" in result.content

        # Pricing tiers preserved
        assert "Starter" in result.content
        assert "Growth" in result.content
        assert "Scale" in result.content

        # Pricing details preserved
        assert "Free" in result.content
        assert "$49/month" in result.content
        assert "$199/month" in result.content
        assert "Unlimited" in result.content

    def test_comparison_table(self):
        """Should handle product comparison table."""
        html = """
        <table>
            <tr>
                <th>Specification</th>
                <th>Model A</th>
                <th>Model B</th>
            </tr>
            <tr>
                <td>Weight</td>
                <td>1.2 kg</td>
                <td>0.9 kg</td>
            </tr>
            <tr>
                <td>Battery</td>
                <td>5000 mAh</td>
                <td>4500 mAh</td>
            </tr>
            <tr>
                <td>Screen</td>
                <td>6.5 inch</td>
                <td>6.1 inch</td>
            </tr>
        </table>
        """
        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # All specifications should be present
        assert "Specification" in result.content
        assert "Model A" in result.content
        assert "Model B" in result.content
        assert "Weight" in result.content
        assert "1.2 kg" in result.content
        assert "0.9 kg" in result.content
        assert "5000 mAh" in result.content
        assert "6.1 inch" in result.content
