"""
Robust text extraction for signal processing.

Phase 0C: Data-Driven Tuning

This module provides source-aware text extraction with:
- Source normalization (aliases, case handling)
- Secure HTML stripping (script/style removal)
- Field merging (source-specific + default fallback)
- Entity unescaping
- Length capping for performance

Usage:
    from utils.text_extraction import extract_text

    text = extract_text(raw_data, source="hacker_news")
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

# Max text length to prevent processing massive blobs
# Cap BEFORE regex for performance
MAX_TEXT_LENGTH = 50_000  # Pre-regex cap
MAX_OUTPUT_LENGTH = 10_000  # Post-processing cap

# Source-specific field priority map (canonical keys only)
SOURCE_FIELD_MAP: Dict[str, List[str]] = {
    "hacker_news": ["title", "story_text", "text", "comment_text"],
    "producthunt": ["description", "tagline", "name"],
    "crunchbase": ["description", "short_description", "tagline"],
    "linkedin": ["about", "description", "summary"],
    "sec_edgar": ["description", "tagline", "business_summary"],
    "github": ["description", "about", "readme"],
    "g2crowd": ["description", "tagline", "category"],
    "pitchbook": ["sector", "thesis_category"],
    "openvc": ["sector"],
}

# Default fields to try for any source (fallback)
DEFAULT_FIELDS: List[str] = [
    "title",
    "description",
    "short_description",
    "about",
    "bio",
    "tagline",
    "summary",
    "overview",
    "category",
    "thesis_fit_reason",
]

# Source alias mapping (normalize to canonical form)
SOURCE_ALIASES: Dict[str, str] = {
    "product_hunt": "producthunt",
    "product_hunt_launch": "producthunt",
    "hacker-news": "hacker_news",
    "hackernews": "hacker_news",
    "hn": "hacker_news",
}


def normalize_source(source: Optional[str]) -> str:
    """
    Normalize source identifier to canonical form.

    - Lowercase
    - Replace hyphens with underscores
    - Apply alias mapping

    Args:
        source: Raw source identifier

    Returns:
        Canonical source key
    """
    if not source:
        return "_default"

    # Lowercase and normalize separators
    normalized = source.lower().replace("-", "_")

    # Apply alias mapping
    return SOURCE_ALIASES.get(normalized, normalized)


def get_fields(source: str) -> List[str]:
    """
    Get fields to extract for a source, merging source-specific + default.

    This prevents "missing one field -> empty text" by always including
    default fields as fallback.

    Args:
        source: Source identifier (will be normalized)

    Returns:
        List of field names to try (deduped, preserving order)
    """
    normalized = normalize_source(source)
    source_fields = SOURCE_FIELD_MAP.get(normalized, [])

    # Merge source-specific + default, preserving order, deduping
    all_fields = source_fields + DEFAULT_FIELDS
    return list(dict.fromkeys(all_fields))


def strip_html(text: str) -> str:
    """
    Securely remove HTML tags.

    1. Removes script and style elements and their content (security/noise)
    2. Replaces other tags with space (preserves word boundaries)

    Args:
        text: Raw text possibly containing HTML

    Returns:
        Text with HTML removed
    """
    # Remove script and style elements and their content
    text = re.sub(
        r'<(script|style)[^>]*>.*?</\1>',
        '',
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Remove remaining tags, replacing with space to preserve word boundaries
    text = re.sub(r'<[^>]+>', ' ', text)

    return text


def get_value(data: Dict[str, Any], path: str) -> str:
    """
    Safely extract string value from nested dict.

    - Supports dotted paths (e.g., 'profile.bio')
    - Handles lists of strings (joins them)
    - Ignores non-string/non-list types to avoid noise (ints, bools)

    Args:
        data: Source dictionary
        path: Key or dotted path (e.g., 'description' or 'profile.bio')

    Returns:
        Extracted string value, or empty string if not found/invalid
    """
    if not data or not path:
        return ""

    # Handle dotted paths
    if "." in path:
        keys = path.split(".")
        value: Any = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return ""
        if value is None:
            return ""
    else:
        value = data.get(path)

    if value is None:
        return ""

    if isinstance(value, str):
        return value

    # Only join lists if they contain strings
    if isinstance(value, list):
        parts = [str(v) for v in value if isinstance(v, str) and v]
        return " ".join(parts)

    # Intentionally ignore ints/bools to avoid '100' or 'true' noise
    return ""


def extract_text(
    raw_data: Dict[str, Any],
    source: str = "_default",
    strip_markup: bool = True,
) -> str:
    """
    Extract scorable text from signal raw_data.

    Features:
    - Source-specific field selection with fallback to defaults
    - Optional HTML stripping (secure, removes script/style first)
    - Entity unescaping (&amp; -> &)
    - Whitespace normalization
    - Length capping (for performance and storage)
    - NO lowercasing (defer to matcher)
    - NO company name injection (defer to caller)

    Args:
        raw_data: Signal raw_data dictionary
        source: Source API identifier (will be normalized)
        strip_markup: Whether to strip HTML tags (default: True)

    Returns:
        Cleaned text string ready for scoring
    """
    if not raw_data:
        return ""

    fields = get_fields(source)

    parts: List[str] = []
    for field in fields:
        val = get_value(raw_data, field)
        if val:
            parts.append(val)

    text = " ".join(parts)

    if not text:
        return ""

    # Cap BEFORE expensive regex operations for performance
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]

    if strip_markup:
        text = strip_html(text)
        # Unescape HTML entities (&amp; -> &, etc.)
        text = html.unescape(text)

    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Final length cap
    return text[:MAX_OUTPUT_LENGTH]
