"""
Whitespace Normalization Utilities

Provides different whitespace normalization strategies for extracted content:
- NONE: No normalization applied
- LAYOUT_PRESERVING: Preserves line breaks and indentation, cleans excessive whitespace
- AGGRESSIVE: Collapses all whitespace to single spaces (destroys structure)

The layout-preserving mode is ideal for content from inscriptis that preserves
table structure - it cleans up excessive blank lines without destroying layout.

Usage:
    from monitoring.content_pipeline.normalize import (
        NormalizationMode,
        normalize_layout_preserving,
        normalize_aggressive,
    )

    # Clean up excessive blank lines but preserve structure
    clean_text = normalize_layout_preserving(inscriptis_output)

    # Collapse all whitespace (traditional selector behavior)
    flat_text = normalize_aggressive(html_text)
"""

import re
from enum import Enum


class NormalizationMode(Enum):
    """Whitespace normalization strategies."""

    NONE = "none"
    LAYOUT_PRESERVING = "layout_preserving"
    AGGRESSIVE = "aggressive"


def normalize_layout_preserving(text: str) -> str:
    """
    Normalize whitespace while preserving layout structure.

    This function:
    - Collapses 3+ consecutive blank lines to 2 blank lines
    - Normalizes excessive inline whitespace (multiple spaces -> single space)
    - Preserves leading whitespace for indentation
    - Trims trailing whitespace on each line

    Args:
        text: Input text to normalize

    Returns:
        Normalized text with layout preserved
    """
    if not text:
        return ""

    # Process line by line to preserve structure
    lines = text.split("\n")
    normalized_lines = []

    for line in lines:
        # Preserve leading whitespace (indentation)
        # Find leading whitespace
        leading_match = re.match(r"^(\s*)", line)
        leading = leading_match.group(1) if leading_match else ""

        # Get the content without leading whitespace
        content = line[len(leading):]

        # Normalize leading whitespace: convert tabs to spaces (4 spaces per tab)
        # but preserve the amount of indentation
        if leading:
            # Count indent level: each tab = 4 spaces, each space = 1 space
            indent_count = 0
            for char in leading:
                if char == "\t":
                    indent_count += 4
                else:
                    indent_count += 1
            leading = " " * indent_count

        # Normalize inline whitespace in content (multiple spaces/tabs -> single space)
        # But only in the content part, not the leading whitespace
        content = re.sub(r"[ \t]+", " ", content)

        # Trim trailing whitespace from content
        content = content.rstrip()

        normalized_lines.append(leading + content)

    # Rejoin lines
    result = "\n".join(normalized_lines)

    # Collapse excessive blank lines (3+ blank lines -> 2)
    # A blank line is a line with only whitespace or empty
    # 3 consecutive newlines = 2 blank lines, 4 = 3 blank lines, etc.
    # We want max 2 blank lines = 3 consecutive newlines
    # Pattern: 4+ consecutive newlines -> 3 newlines
    result = re.sub(r"\n{4,}", "\n\n\n", result)

    return result


def normalize_aggressive(text: str) -> str:
    """
    Aggressively normalize whitespace by collapsing all to single spaces.

    This destroys any layout structure (tables, lists, etc.) but produces
    a clean single-line-style output.

    This function:
    - Replaces all whitespace (spaces, tabs, newlines) with single spaces
    - Trims leading and trailing whitespace

    Args:
        text: Input text to normalize

    Returns:
        Normalized text with all whitespace collapsed
    """
    if not text:
        return ""

    # Replace all whitespace (including newlines) with single space
    result = re.sub(r"\s+", " ", text)

    # Trim leading/trailing whitespace
    result = result.strip()

    return result
