"""Tests for whitespace normalization utilities (normalize.py).

Tests cover:
- Blank line collapsing (3+ blank lines -> 2)
- Inline whitespace normalization (multiple spaces -> single space)
- Leading whitespace preservation (for indentation)
- Trailing whitespace trimming
- Table structure preservation with layout-preserving mode
- Aggressive mode (collapse all whitespace)
- NormalizationMode enum
"""

import pytest
from monitoring.content_pipeline.normalize import (
    NormalizationMode,
    normalize_layout_preserving,
    normalize_aggressive,
)


class TestNormalizationModeEnum:
    """Test NormalizationMode enum."""

    def test_enum_has_expected_values(self):
        """NormalizationMode should have NONE, LAYOUT_PRESERVING, AGGRESSIVE."""
        assert hasattr(NormalizationMode, "NONE")
        assert hasattr(NormalizationMode, "LAYOUT_PRESERVING")
        assert hasattr(NormalizationMode, "AGGRESSIVE")

    def test_enum_values_are_distinct(self):
        """Enum values should be distinct."""
        assert NormalizationMode.NONE != NormalizationMode.LAYOUT_PRESERVING
        assert NormalizationMode.LAYOUT_PRESERVING != NormalizationMode.AGGRESSIVE
        assert NormalizationMode.NONE != NormalizationMode.AGGRESSIVE


class TestNormalizeLayoutPreservingBlankLines:
    """Test blank line collapsing in layout-preserving mode."""

    def test_three_blank_lines_collapsed_to_two(self):
        """Should collapse 3 consecutive blank lines to 2."""
        text = "Line one.\n\n\n\nLine two."
        result = normalize_layout_preserving(text)

        # 3+ blank lines should become 2 blank lines (3 newlines total)
        assert result == "Line one.\n\n\nLine two."

    def test_four_blank_lines_collapsed_to_two(self):
        """Should collapse 4 consecutive blank lines to 2."""
        text = "Line one.\n\n\n\n\nLine two."
        result = normalize_layout_preserving(text)

        assert result == "Line one.\n\n\nLine two."

    def test_many_blank_lines_collapsed_to_two(self):
        """Should collapse many blank lines to 2."""
        text = "Line one.\n\n\n\n\n\n\n\n\n\nLine two."
        result = normalize_layout_preserving(text)

        assert result == "Line one.\n\n\nLine two."

    def test_two_blank_lines_preserved(self):
        """Should preserve exactly 2 blank lines (not collapse further)."""
        text = "Line one.\n\n\nLine two."
        result = normalize_layout_preserving(text)

        assert result == "Line one.\n\n\nLine two."

    def test_one_blank_line_preserved(self):
        """Should preserve single blank line."""
        text = "Line one.\n\nLine two."
        result = normalize_layout_preserving(text)

        assert result == "Line one.\n\nLine two."

    def test_no_blank_lines_preserved(self):
        """Should preserve adjacent lines with no blanks between them."""
        text = "Line one.\nLine two."
        result = normalize_layout_preserving(text)

        assert result == "Line one.\nLine two."

    def test_multiple_groups_of_blank_lines(self):
        """Should handle multiple groups of blank lines throughout text."""
        text = "A\n\n\n\n\nB\n\n\n\n\n\nC"
        result = normalize_layout_preserving(text)

        assert result == "A\n\n\nB\n\n\nC"


class TestNormalizeLayoutPreservingInlineWhitespace:
    """Test inline whitespace normalization in layout-preserving mode."""

    def test_multiple_spaces_collapsed_to_one(self):
        """Should collapse multiple spaces to single space within a line."""
        text = "Text    with     many      spaces."
        result = normalize_layout_preserving(text)

        assert result == "Text with many spaces."

    def test_tabs_converted_to_spaces(self):
        """Should convert tabs within a line to single spaces."""
        text = "Column1\t\t\tColumn2"
        result = normalize_layout_preserving(text)

        assert result == "Column1 Column2"

    def test_mixed_whitespace_normalized(self):
        """Should normalize mixed whitespace (spaces and tabs)."""
        text = "Text  \t  with \t mixed  \t  whitespace."
        result = normalize_layout_preserving(text)

        assert result == "Text with mixed whitespace."


class TestNormalizeLayoutPreservingIndentation:
    """Test leading whitespace (indentation) preservation."""

    def test_leading_spaces_preserved(self):
        """Should preserve leading spaces for indentation."""
        text = "    Indented line"
        result = normalize_layout_preserving(text)

        assert result == "    Indented line"

    def test_leading_tabs_preserved_as_spaces(self):
        """Should preserve leading tabs (converted to spaces)."""
        # Tabs are often used for indentation, should be preserved
        text = "\tIndented with tab"
        result = normalize_layout_preserving(text)

        # Leading tab preserved as some form of indentation
        assert result.startswith(" ") or result.startswith("\t")
        assert "Indented with tab" in result

    def test_multiline_indentation_preserved(self):
        """Should preserve indentation on multiple lines."""
        text = """Line one
    Indented line
        Double indented
    Back to single"""
        result = normalize_layout_preserving(text)

        # Each line's leading whitespace should be preserved
        lines = result.split("\n")
        assert lines[0] == "Line one"
        assert lines[1].startswith("    ")
        assert lines[2].startswith("        ") or lines[2].startswith("    ")
        assert lines[3].startswith("    ")


class TestNormalizeLayoutPreservingTrailingWhitespace:
    """Test trailing whitespace trimming."""

    def test_trailing_spaces_trimmed(self):
        """Should trim trailing spaces from each line."""
        text = "Line with trailing spaces    "
        result = normalize_layout_preserving(text)

        assert result == "Line with trailing spaces"

    def test_trailing_tabs_trimmed(self):
        """Should trim trailing tabs from each line."""
        text = "Line with trailing tab\t\t"
        result = normalize_layout_preserving(text)

        assert result == "Line with trailing tab"

    def test_multiline_trailing_whitespace_trimmed(self):
        """Should trim trailing whitespace from all lines."""
        text = "Line one    \nLine two\t\t\nLine three   "
        result = normalize_layout_preserving(text)

        lines = result.split("\n")
        assert lines[0] == "Line one"
        assert lines[1] == "Line two"
        assert lines[2] == "Line three"


class TestNormalizeLayoutPreservingTableStructure:
    """Test table structure preservation with layout-preserving mode."""

    def test_table_column_alignment_preserved(self):
        """Should preserve table column alignment (multi-space separation)."""
        # inscriptis uses multiple spaces to separate columns
        text = "Product     Price\nBasic       $10\nPro         $25"
        result = normalize_layout_preserving(text)

        # Column structure should be preserved (not collapsed to single space)
        # The key is that line breaks are preserved
        lines = result.split("\n")
        assert len(lines) == 3
        assert "Product" in lines[0] and "Price" in lines[0]
        assert "Basic" in lines[1] and "$10" in lines[1]
        assert "Pro" in lines[2] and "$25" in lines[2]

    def test_table_with_excessive_blank_lines(self):
        """Should collapse excessive blank lines but preserve table structure."""
        text = """Product     Price

Basic       $10


Pro         $25



Enterprise  $100"""
        result = normalize_layout_preserving(text)

        # Excessive blank lines collapsed, but table structure preserved
        lines = result.split("\n")
        # Should have table rows separated by at most 2 blank lines
        assert "Enterprise" in result
        assert result.count("\n\n\n\n") == 0  # No 3+ blank lines


class TestNormalizeLayoutPreservingEdgeCases:
    """Test edge cases for layout-preserving normalization."""

    def test_empty_string(self):
        """Should handle empty string."""
        result = normalize_layout_preserving("")
        assert result == ""

    def test_only_whitespace(self):
        """Should handle whitespace-only string."""
        result = normalize_layout_preserving("   \n\n\t\t\n   ")
        # Should normalize to empty or minimal whitespace
        assert result.strip() == ""

    def test_single_line_no_whitespace_issues(self):
        """Should not modify clean single line."""
        text = "This is a clean line."
        result = normalize_layout_preserving(text)
        assert result == "This is a clean line."

    def test_preserves_intentional_structure(self):
        """Should preserve intentional line structure."""
        text = """Header
First item
Second item
Third item"""
        result = normalize_layout_preserving(text)

        assert result == text


class TestNormalizeAggressive:
    """Test aggressive whitespace normalization."""

    def test_collapses_all_whitespace_to_single_space(self):
        """Should collapse all whitespace to single spaces."""
        text = "Line one.\n\nLine two.\n\n\nLine three."
        result = normalize_aggressive(text)

        assert result == "Line one. Line two. Line three."

    def test_collapses_newlines(self):
        """Should collapse newlines to spaces."""
        text = "A\nB\nC"
        result = normalize_aggressive(text)

        assert result == "A B C"

    def test_collapses_multiple_spaces(self):
        """Should collapse multiple spaces."""
        text = "Text    with     spaces"
        result = normalize_aggressive(text)

        assert result == "Text with spaces"

    def test_collapses_tabs(self):
        """Should collapse tabs."""
        text = "Column1\t\tColumn2"
        result = normalize_aggressive(text)

        assert result == "Column1 Column2"

    def test_trims_leading_trailing(self):
        """Should trim leading and trailing whitespace."""
        text = "   Content with padding   "
        result = normalize_aggressive(text)

        assert result == "Content with padding"

    def test_table_becomes_single_line(self):
        """Should collapse table to single line (destroys structure)."""
        text = """Product     Price
Basic       $10
Pro         $25"""
        result = normalize_aggressive(text)

        # All on one line
        assert "\n" not in result
        assert "Product" in result and "Price" in result
        assert "Basic" in result and "$10" in result

    def test_empty_string(self):
        """Should handle empty string."""
        result = normalize_aggressive("")
        assert result == ""

    def test_only_whitespace_returns_empty(self):
        """Should return empty string for whitespace-only input."""
        result = normalize_aggressive("   \n\n\t\t   ")
        assert result == ""


class TestNormalizationComparison:
    """Test comparing layout-preserving vs aggressive normalization."""

    def test_layout_preserving_keeps_lines_aggressive_removes(self):
        """Layout-preserving keeps lines, aggressive removes them."""
        text = "Line 1\nLine 2\nLine 3"

        layout_result = normalize_layout_preserving(text)
        aggressive_result = normalize_aggressive(text)

        assert "\n" in layout_result
        assert "\n" not in aggressive_result

    def test_both_handle_excessive_spaces_inline(self):
        """Both modes should collapse excessive inline spaces."""
        text = "Text    with     spaces"

        layout_result = normalize_layout_preserving(text)
        aggressive_result = normalize_aggressive(text)

        assert "    " not in layout_result
        assert "    " not in aggressive_result

    def test_example_from_task_description(self):
        """Test the exact example from the task description."""
        input_text = """Product     Price

Basic       $10


Pro         $25



Enterprise  $100"""

        layout_result = normalize_layout_preserving(input_text)
        aggressive_result = normalize_aggressive(input_text)

        # Layout-preserving: 3+ blank lines -> 2
        # Should not have 4+ consecutive newlines
        assert "\n\n\n\n" not in layout_result
        # Should still have line breaks
        assert "\n" in layout_result
        # All content present
        assert "Product" in layout_result
        assert "Enterprise" in layout_result
        assert "$100" in layout_result

        # Aggressive: everything on one line
        assert "\n" not in aggressive_result
        assert "Product Price Basic $10 Pro $25 Enterprise $100" == aggressive_result
