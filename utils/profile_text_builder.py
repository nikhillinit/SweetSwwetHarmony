"""
ProfileTextBuilder - Builds embedding input text from company profiles.

Creates a labeled template format for Gemini embeddings:
    Company: {company_name}
    Problem: {problem_solved}
    Customer: {target_customer}
    Business model: {business_model}
    Category: {category_hints}

Sprint 4: Similar Companies feature.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from profilers.url_profiler import ProfileExtractionResult, ExtractedField


# =============================================================================
# PROTOCOLS (for duck typing)
# =============================================================================

class ExtractedFieldLike(Protocol):
    """Protocol for ExtractedField-like objects."""
    value: str


class ProfileExtractionResultLike(Protocol):
    """Protocol for ProfileExtractionResult-like objects."""
    problem_solved: Optional[ExtractedFieldLike]
    target_customer: Optional[ExtractedFieldLike]
    business_model: Optional[ExtractedFieldLike]
    pricing_model: Optional[ExtractedFieldLike]
    company_name: Optional[ExtractedFieldLike]
    category_hints: List[str]


# =============================================================================
# PROFILE TEXT BUILDER
# =============================================================================

@dataclass
class ProfileTextBuilder:
    """
    Builds embedding input text from company profiles.

    Uses a labeled template format that:
    1. Provides context for the embedding model
    2. Is deterministic for hash-based staleness detection
    3. Handles missing fields gracefully
    """

    # Template with labeled fields (order matters for consistency)
    TEMPLATE = """Company: {company_name}
Problem: {problem_solved}
Customer: {target_customer}
Business model: {business_model}
Category: {category_hints}"""

    # Minimum length for a "thick" profile (chars)
    MIN_PROFILE_LENGTH = 200

    def build(self, profile: ProfileExtractionResultLike) -> str:
        """
        Build embedding input text from a ProfileExtractionResult.

        Args:
            profile: ProfileExtractionResult or compatible object

        Returns:
            Formatted text string for embedding
        """
        return self.TEMPLATE.format(
            company_name=self._extract_value(profile.company_name),
            problem_solved=self._extract_value(profile.problem_solved),
            target_customer=self._extract_value(profile.target_customer),
            business_model=self._extract_value(profile.business_model),
            category_hints=", ".join(profile.category_hints) if profile.category_hints else "",
        )

    def build_from_dict(self, profile_dict: Dict[str, Any]) -> str:
        """
        Build embedding input text from a dictionary.

        Args:
            profile_dict: Dictionary with profile fields

        Returns:
            Formatted text string for embedding
        """
        return self.TEMPLATE.format(
            company_name=profile_dict.get("company_name", "") or "",
            problem_solved=profile_dict.get("problem_solved", "") or "",
            target_customer=profile_dict.get("target_customer", "") or "",
            business_model=profile_dict.get("business_model", "") or "",
            category_hints=", ".join(profile_dict.get("category_hints", []) or []),
        )

    def compute_hash(self, text: str) -> str:
        """
        Compute SHA256 hash of text for staleness detection.

        Args:
            text: Text to hash

        Returns:
            64-character hex digest
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def is_thin_profile(self, text: str) -> bool:
        """
        Check if profile text is too sparse for reliable similarity matching.

        A profile is "thin" if:
        1. Total length < 200 characters, OR
        2. Both problem and customer fields are empty

        Args:
            text: Built profile text

        Returns:
            True if profile is thin, False otherwise
        """
        # Check total length
        if len(text) < self.MIN_PROFILE_LENGTH:
            return True

        # Check for missing key fields
        problem_empty = self._field_is_empty(text, "Problem:")
        customer_empty = self._field_is_empty(text, "Customer:")

        if problem_empty and customer_empty:
            return True

        return False

    def get_preview(self, text: str, max_length: int = 512) -> str:
        """
        Get a truncated preview of the text for debugging.

        Args:
            text: Full text
            max_length: Maximum length (including ellipsis)

        Returns:
            Truncated text with ellipsis if needed
        """
        if len(text) <= max_length:
            return text

        return text[: max_length - 3] + "..."

    def _extract_value(self, field: Optional[ExtractedFieldLike]) -> str:
        """
        Extract value from an ExtractedField, normalizing newlines.

        Args:
            field: ExtractedField or None

        Returns:
            Cleaned value string or empty string
        """
        if field is None:
            return ""

        value = field.value or ""

        # Normalize newlines to spaces
        value = re.sub(r"\s*\n\s*", " ", value)

        return value.strip()

    def _field_is_empty(self, text: str, field_label: str) -> bool:
        """
        Check if a specific field in the built text is empty.

        Args:
            text: Full built text
            field_label: Label to check (e.g., "Problem:")

        Returns:
            True if field has no content after the label
        """
        # Find the field in the text
        pattern = rf"{re.escape(field_label)}\s*(.*?)(?:\n|$)"
        match = re.search(pattern, text)

        if not match:
            return True

        value = match.group(1).strip()
        return len(value) == 0


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def build_profile_text(profile: Union[ProfileExtractionResultLike, Dict[str, Any]]) -> str:
    """
    Convenience function to build profile text.

    Args:
        profile: ProfileExtractionResult, compatible object, or dictionary

    Returns:
        Formatted text string for embedding
    """
    builder = ProfileTextBuilder()

    if isinstance(profile, dict):
        return builder.build_from_dict(profile)

    return builder.build(profile)


def compute_profile_hash(profile: Union[ProfileExtractionResultLike, Dict[str, Any]]) -> str:
    """
    Convenience function to compute profile hash.

    Args:
        profile: ProfileExtractionResult, compatible object, or dictionary

    Returns:
        SHA256 hex digest of the profile text
    """
    builder = ProfileTextBuilder()
    text = build_profile_text(profile)
    return builder.compute_hash(text)
