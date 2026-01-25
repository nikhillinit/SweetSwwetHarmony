"""
Tests for ProfileTextBuilder - builds embedding input text from profiles.

TDD: Write failing tests first, then implement.
"""

import pytest
from dataclasses import dataclass
from typing import Optional, List


# =============================================================================
# MOCK CLASSES (to avoid circular imports during testing)
# =============================================================================

@dataclass
class MockExtractedField:
    """Mimics profilers.url_profiler.ExtractedField"""
    value: str
    short_phrase: str = ""
    confidence: float = 0.8
    evidence_snippet: Optional[str] = None
    source_url: str = ""
    extraction_method: str = "llm"


@dataclass
class MockProfileExtractionResult:
    """Mimics profilers.url_profiler.ProfileExtractionResult"""
    problem_solved: Optional[MockExtractedField] = None
    target_customer: Optional[MockExtractedField] = None
    business_model: Optional[MockExtractedField] = None
    pricing_model: Optional[MockExtractedField] = None
    company_name: Optional[MockExtractedField] = None
    category_hints: List[str] = None

    def __post_init__(self):
        if self.category_hints is None:
            self.category_hints = []


# =============================================================================
# TESTS
# =============================================================================

class TestProfileTextBuilder:
    """Tests for ProfileTextBuilder class."""

    def test_build_full_profile(self):
        """Build text from a profile with all fields populated."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        profile = MockProfileExtractionResult(
            company_name=MockExtractedField(value="Acme Foods"),
            problem_solved=MockExtractedField(value="Reduces food waste in restaurants"),
            target_customer=MockExtractedField(value="Restaurant chains and hospitality groups"),
            business_model=MockExtractedField(value="B2B_SaaS"),
            category_hints=["Consumer CPG", "Travel & Hospitality"],
        )

        result = builder.build(profile)

        assert "Company: Acme Foods" in result
        assert "Problem: Reduces food waste in restaurants" in result
        assert "Customer: Restaurant chains and hospitality groups" in result
        assert "Business model: B2B_SaaS" in result
        assert "Category: Consumer CPG, Travel & Hospitality" in result

    def test_build_partial_profile(self):
        """Build text from a profile with some fields missing."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        profile = MockProfileExtractionResult(
            company_name=MockExtractedField(value="Stealth Startup"),
            problem_solved=MockExtractedField(value="Helps consumers save money"),
            # target_customer is None
            # business_model is None
            category_hints=["Consumer Health Tech"],
        )

        result = builder.build(profile)

        assert "Company: Stealth Startup" in result
        assert "Problem: Helps consumers save money" in result
        assert "Customer:" in result  # Should have empty value
        assert "Business model:" in result  # Should have empty value
        assert "Category: Consumer Health Tech" in result

    def test_build_empty_profile(self):
        """Build text from a completely empty profile."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        profile = MockProfileExtractionResult()

        result = builder.build(profile)

        # Should still produce valid template, just with empty values
        assert "Company:" in result
        assert "Problem:" in result
        assert "Customer:" in result
        assert "Business model:" in result
        assert "Category:" in result

    def test_build_with_dict_input(self):
        """Build text from a dictionary instead of dataclass."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        profile_dict = {
            "company_name": "Dict Company",
            "problem_solved": "Solves a problem",
            "target_customer": "Consumers",
            "business_model": "D2C_ecommerce",
            "category_hints": ["Consumer Marketplace"],
        }

        result = builder.build_from_dict(profile_dict)

        assert "Company: Dict Company" in result
        assert "Problem: Solves a problem" in result


class TestHashComputation:
    """Tests for source_text_hash computation."""

    def test_compute_hash_deterministic(self):
        """Same text should produce same hash."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        text = "Company: Test\nProblem: Something"

        hash1 = builder.compute_hash(text)
        hash2 = builder.compute_hash(text)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest

    def test_compute_hash_different_for_different_text(self):
        """Different text should produce different hash."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()

        hash1 = builder.compute_hash("Text A")
        hash2 = builder.compute_hash("Text B")

        assert hash1 != hash2

    def test_compute_hash_whitespace_sensitive(self):
        """Hash should be sensitive to whitespace differences."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()

        hash1 = builder.compute_hash("Text with space")
        hash2 = builder.compute_hash("Text  with  space")

        assert hash1 != hash2


class TestThinProfileDetection:
    """Tests for thin profile detection."""

    def test_thin_profile_short_text(self):
        """Profile with < 200 chars is thin."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        short_text = "Company: X\nProblem:\nCustomer:"  # ~30 chars

        assert builder.is_thin_profile(short_text) is True

    def test_thin_profile_adequate_text(self):
        """Profile with >= 200 chars is not thin."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        long_text = "Company: Acme Foods Inc\n" \
                   "Problem: Reduces food waste in restaurants by using AI-powered inventory management\n" \
                   "Customer: Restaurant chains and hospitality groups looking to reduce costs\n" \
                   "Business model: B2B_SaaS\n" \
                   "Category: Consumer CPG, Travel & Hospitality"

        assert len(long_text) > 200
        assert builder.is_thin_profile(long_text) is False

    def test_thin_profile_missing_key_fields(self):
        """Profile missing both problem and customer is thin even if long."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        # Long but missing key fields
        text = "Company: A Very Long Company Name That Goes On And On\n" \
               "Problem:\n" \
               "Customer:\n" \
               "Business model: B2B_SaaS\n" \
               "Category: Consumer CPG, Consumer Health Tech, Travel & Hospitality, Consumer Marketplace"

        # Even though it's long, missing problem AND customer makes it thin
        assert builder.is_thin_profile(text) is True


class TestEdgeCases:
    """Edge case tests."""

    def test_special_characters_in_profile(self):
        """Handle special characters without breaking."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        profile = MockProfileExtractionResult(
            company_name=MockExtractedField(value="Caf\u00e9 & Co."),
            problem_solved=MockExtractedField(value="Makes \"amazing\" coffee (100%)"),
        )

        result = builder.build(profile)

        assert "Caf\u00e9 & Co." in result
        assert '"amazing"' in result

    def test_newlines_in_values(self):
        """Newlines in values should be normalized."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        profile = MockProfileExtractionResult(
            problem_solved=MockExtractedField(value="Line 1\nLine 2\nLine 3"),
        )

        result = builder.build(profile)

        # Newlines should be converted to spaces
        assert "Line 1 Line 2 Line 3" in result

    def test_very_long_values_truncated(self):
        """Very long values should be truncated in preview."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        long_value = "X" * 2000  # Very long

        profile = MockProfileExtractionResult(
            problem_solved=MockExtractedField(value=long_value),
        )

        result = builder.build(profile)
        preview = builder.get_preview(result, max_length=512)

        assert len(preview) <= 512
        assert preview.endswith("...")


class TestPreviewGeneration:
    """Tests for generating text previews."""

    def test_get_preview_short_text(self):
        """Short text should not be truncated."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        short_text = "This is short"

        preview = builder.get_preview(short_text, max_length=512)

        assert preview == short_text
        assert "..." not in preview

    def test_get_preview_long_text(self):
        """Long text should be truncated with ellipsis."""
        from utils.profile_text_builder import ProfileTextBuilder

        builder = ProfileTextBuilder()
        long_text = "A" * 1000

        preview = builder.get_preview(long_text, max_length=100)

        assert len(preview) == 100
        assert preview.endswith("...")
        assert preview.startswith("A" * 97)
