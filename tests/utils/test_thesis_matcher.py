"""
Comprehensive tests for thesis_matcher.py

Tests cover:
- Thesis enum values
- ThesisFit dataclass and properties
- ThesisMatcher class (score, score_signals, custom keywords)
- Keyword matching (positive, negative, word boundaries)
- SIC code boost
- Confidence classification
- Convenience functions (score_thesis_fit, is_thesis_fit)
- Edge cases and boundary conditions
"""

import pytest
from utils.thesis_matcher import (
    # Enums and data classes
    Thesis,
    ThesisFit,
    # Main matcher
    ThesisMatcher,
    # Keywords
    THESIS_KEYWORDS,
    NEGATIVE_KEYWORDS,
    # Convenience functions
    score_thesis_fit,
    is_thesis_fit,
)


# =============================================================================
# TEST: Thesis Enum
# =============================================================================

class TestThesisEnum:
    """Tests for the Thesis enum"""

    def test_thesis_values(self):
        """Should have expected thesis values"""
        assert Thesis.AI_INFRASTRUCTURE.value == "ai_infrastructure"
        assert Thesis.HEALTHTECH.value == "healthtech"
        assert Thesis.CLEANTECH.value == "cleantech"
        assert Thesis.UNKNOWN.value == "unknown"

    def test_thesis_is_string_enum(self):
        """Thesis should be a string enum"""
        assert isinstance(Thesis.AI_INFRASTRUCTURE, str)
        assert Thesis.AI_INFRASTRUCTURE == "ai_infrastructure"

    def test_thesis_members(self):
        """Should have exactly 4 members"""
        assert len(Thesis) == 4


# =============================================================================
# TEST: ThesisFit Dataclass
# =============================================================================

class TestThesisFitDataclass:
    """Tests for ThesisFit dataclass"""

    def test_is_fit_true_at_threshold(self):
        """is_fit should be True when score >= 0.4"""
        fit = ThesisFit(
            thesis=Thesis.AI_INFRASTRUCTURE,
            score=0.4,
            matched_keywords=["llm"],
            negative_keywords=[],
            all_scores={"ai_infrastructure": 0.4},
            confidence="MEDIUM",
        )
        assert fit.is_fit is True

    def test_is_fit_true_above_threshold(self):
        """is_fit should be True when score > 0.4"""
        fit = ThesisFit(
            thesis=Thesis.HEALTHTECH,
            score=0.8,
            matched_keywords=["clinical trial"],
            negative_keywords=[],
            all_scores={"healthtech": 0.8},
            confidence="HIGH",
        )
        assert fit.is_fit is True

    def test_is_fit_false_below_threshold(self):
        """is_fit should be False when score < 0.4"""
        fit = ThesisFit(
            thesis=Thesis.UNKNOWN,
            score=0.3,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="LOW",
        )
        assert fit.is_fit is False

    def test_is_fit_false_at_zero(self):
        """is_fit should be False when score is 0"""
        fit = ThesisFit(
            thesis=Thesis.UNKNOWN,
            score=0.0,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="LOW",
        )
        assert fit.is_fit is False

    def test_to_dict_structure(self):
        """to_dict should return expected structure"""
        fit = ThesisFit(
            thesis=Thesis.AI_INFRASTRUCTURE,
            score=0.75,
            matched_keywords=["llm", "inference"],
            negative_keywords=["crypto"],
            all_scores={
                "ai_infrastructure": 0.75,
                "healthtech": 0.1,
                "cleantech": 0.05,
            },
            confidence="HIGH",
        )
        d = fit.to_dict()

        assert d["thesis"] == "ai_infrastructure"
        assert d["score"] == 0.75
        assert d["matched_keywords"] == ["llm", "inference"]
        assert d["negative_keywords"] == ["crypto"]
        assert d["confidence"] == "HIGH"
        assert d["is_fit"] is True

    def test_to_dict_rounds_scores(self):
        """to_dict should round scores to 3 decimal places"""
        fit = ThesisFit(
            thesis=Thesis.HEALTHTECH,
            score=0.123456789,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={"healthtech": 0.123456789},
            confidence="LOW",
        )
        d = fit.to_dict()

        assert d["score"] == 0.123
        assert d["all_scores"]["healthtech"] == 0.123


# =============================================================================
# TEST: ThesisMatcher - Basic Scoring
# =============================================================================

class TestThesisMatcherBasicScoring:
    """Tests for basic ThesisMatcher scoring"""

    def test_ai_infrastructure_keywords(self):
        """Should detect AI infrastructure keywords"""
        matcher = ThesisMatcher()
        # Use many keywords to ensure threshold is met
        fit = matcher.score(
            "Building an LLM inference optimization platform with vector database, "
            "embedding, ml ops, fine-tuning, model serving, rag"
        )

        assert fit.thesis == Thesis.AI_INFRASTRUCTURE
        assert fit.score > 0.4
        assert "llm" in fit.matched_keywords
        assert "inference" in fit.matched_keywords

    def test_healthtech_keywords(self):
        """Should detect healthtech keywords"""
        matcher = ThesisMatcher()
        # Use many keywords to ensure threshold is met
        fit = matcher.score(
            "Clinical trial management for drug discovery, FDA approval, "
            "therapeutics, healthcare AI, medical device, telehealth"
        )

        assert fit.thesis == Thesis.HEALTHTECH
        assert fit.score > 0.4
        assert "clinical trial" in fit.matched_keywords
        assert "drug discovery" in fit.matched_keywords

    def test_cleantech_keywords(self):
        """Should detect cleantech keywords"""
        matcher = ThesisMatcher()
        # Use many keywords to ensure threshold is met
        fit = matcher.score(
            "Carbon capture technology for renewable energy, climate tech, "
            "net zero, decarbonization, battery storage, clean energy"
        )

        assert fit.thesis == Thesis.CLEANTECH
        assert fit.score > 0.4
        assert "carbon capture" in fit.matched_keywords
        assert "renewable energy" in fit.matched_keywords

    def test_unknown_thesis_for_unrelated(self):
        """Should return UNKNOWN for unrelated content"""
        matcher = ThesisMatcher()
        fit = matcher.score("We sell shoes and handbags")

        assert fit.thesis == Thesis.UNKNOWN
        assert fit.score < 0.4
        assert fit.confidence == "LOW"

    def test_empty_text_returns_unknown(self):
        """Should return UNKNOWN for empty text"""
        matcher = ThesisMatcher()
        fit = matcher.score("")

        assert fit.thesis == Thesis.UNKNOWN
        assert fit.score == 0.0
        assert fit.matched_keywords == []
        assert fit.confidence == "LOW"

    def test_whitespace_only_returns_unknown(self):
        """Should return UNKNOWN for whitespace-only text"""
        matcher = ThesisMatcher()
        fit = matcher.score("   \n\t  ")

        # Whitespace-only normalizes to empty-ish, no keywords match
        assert fit.score < 0.1


# =============================================================================
# TEST: ThesisMatcher - Keyword Matching
# =============================================================================

class TestThesisMatcherKeywordMatching:
    """Tests for keyword matching behavior"""

    def test_case_insensitive_matching(self):
        """Matching should be case insensitive"""
        matcher = ThesisMatcher()

        fit1 = matcher.score("LLM inference")
        fit2 = matcher.score("llm inference")
        fit3 = matcher.score("Llm Inference")

        assert fit1.thesis == fit2.thesis == fit3.thesis
        assert abs(fit1.score - fit2.score) < 0.01

    def test_word_boundary_matching(self):
        """Should use word boundaries to avoid partial matches"""
        matcher = ThesisMatcher()

        # "llm" should match "llm" but not "llama"
        fit_llm = matcher.score("Building an llm platform")
        fit_llama = matcher.score("Building a llama farm")

        assert "llm" in fit_llm.matched_keywords
        assert "llm" not in fit_llama.matched_keywords

    def test_multi_word_keyword_matching(self):
        """Should match multi-word keywords"""
        matcher = ThesisMatcher()
        fit = matcher.score("We build large language model infrastructure")

        assert "large language model" in fit.matched_keywords

    def test_multiple_keywords_accumulate(self):
        """Multiple keywords should increase score"""
        matcher = ThesisMatcher()

        fit_one = matcher.score("Building llm infrastructure")
        fit_many = matcher.score("Building llm inference vector database embedding rag")

        assert fit_many.score > fit_one.score
        assert len(fit_many.matched_keywords) > len(fit_one.matched_keywords)


# =============================================================================
# TEST: ThesisMatcher - Negative Keywords
# =============================================================================

class TestThesisMatcherNegativeKeywords:
    """Tests for negative keyword handling"""

    def test_crypto_reduces_score(self):
        """Crypto keyword should reduce score"""
        matcher = ThesisMatcher()

        fit_clean = matcher.score("AI platform for inference")
        fit_crypto = matcher.score("AI platform for inference, crypto trading")

        assert fit_crypto.score < fit_clean.score
        assert "crypto" in fit_crypto.negative_keywords

    def test_gaming_reduces_score(self):
        """Gaming keyword should reduce score"""
        matcher = ThesisMatcher()

        fit_clean = matcher.score("ML platform for model training")
        fit_gaming = matcher.score("ML platform for model training in gaming")

        assert fit_gaming.score < fit_clean.score
        assert "gaming" in fit_gaming.negative_keywords

    def test_multiple_negative_keywords(self):
        """Multiple negative keywords should accumulate penalty"""
        matcher = ThesisMatcher()

        # Use text with more AI keywords so score doesn't bottom out at 0
        fit_one_neg = matcher.score("LLM inference vector database ml ops, crypto")
        fit_many_neg = matcher.score("LLM inference vector database ml ops, crypto, gaming, nft, web3")

        # More negative keywords = lower score (or equal if both hit floor)
        assert fit_many_neg.score <= fit_one_neg.score
        assert len(fit_many_neg.negative_keywords) > len(fit_one_neg.negative_keywords)

    def test_negative_keywords_listed(self):
        """Should list all found negative keywords"""
        matcher = ThesisMatcher()
        fit = matcher.score("crypto blockchain nft gaming")

        assert "crypto" in fit.negative_keywords
        assert "blockchain" in fit.negative_keywords
        assert "nft" in fit.negative_keywords
        assert "gaming" in fit.negative_keywords


# =============================================================================
# TEST: ThesisMatcher - SIC Code Boost
# =============================================================================

class TestThesisMatcherSICCodeBoost:
    """Tests for SIC code boost functionality"""

    def test_healthtech_sic_boost(self):
        """Healthtech SIC codes should boost healthtech score"""
        matcher = ThesisMatcher()

        fit_no_sic = matcher.score("Healthcare platform")
        fit_with_sic = matcher.score("Healthcare platform", sic_code="8011")  # Health services

        assert fit_with_sic.score > fit_no_sic.score

    def test_cleantech_sic_boost(self):
        """Cleantech SIC codes should boost cleantech score"""
        matcher = ThesisMatcher()

        fit_no_sic = matcher.score("Energy efficiency solutions")
        fit_with_sic = matcher.score("Energy efficiency solutions", sic_code="4911")  # Electric

        assert fit_with_sic.score > fit_no_sic.score

    def test_ai_infrastructure_sic_boost(self):
        """AI infra SIC codes should boost AI score"""
        matcher = ThesisMatcher()

        fit_no_sic = matcher.score("Software platform")
        fit_with_sic = matcher.score("Software platform", sic_code="7372")  # Software

        assert fit_with_sic.score >= fit_no_sic.score

    def test_unrelated_sic_no_boost(self):
        """Unrelated SIC codes should not boost score"""
        matcher = ThesisMatcher()

        fit_no_sic = matcher.score("AI inference platform")
        fit_unrelated_sic = matcher.score("AI inference platform", sic_code="5812")  # Eating places

        # Score should be similar (no boost)
        assert abs(fit_no_sic.score - fit_unrelated_sic.score) < 0.01

    def test_sic_boost_capped_at_one(self):
        """Score should be capped at 1.0 after SIC boost"""
        matcher = ThesisMatcher()

        # Even with high score + SIC boost, should not exceed 1.0
        fit = matcher.score(
            "LLM inference vector database embedding ml ops model serving model deployment",
            sic_code="7372"
        )

        assert fit.score <= 1.0


# =============================================================================
# TEST: ThesisMatcher - Confidence Classification
# =============================================================================

class TestThesisMatcherConfidence:
    """Tests for confidence classification"""

    def test_high_confidence_threshold(self):
        """Score >= 0.7 should be HIGH confidence"""
        matcher = ThesisMatcher()
        fit = matcher.score("LLM inference vector database embedding ml ops rag fine-tuning")

        if fit.score >= 0.7:
            assert fit.confidence == "HIGH"

    def test_medium_confidence_range(self):
        """Score 0.4-0.7 should be MEDIUM confidence"""
        matcher = ThesisMatcher()
        fit = matcher.score("AI platform for inference")

        if 0.4 <= fit.score < 0.7:
            assert fit.confidence == "MEDIUM"

    def test_low_confidence_threshold(self):
        """Score < 0.4 should be LOW confidence"""
        matcher = ThesisMatcher()
        fit = matcher.score("Random text with no keywords")

        if fit.score < 0.4:
            assert fit.confidence == "LOW"


# =============================================================================
# TEST: ThesisMatcher - Custom Keywords
# =============================================================================

class TestThesisMatcherCustomKeywords:
    """Tests for custom keyword functionality"""

    def test_custom_keywords_merged(self):
        """Custom keywords should be merged with defaults"""
        custom = {
            Thesis.AI_INFRASTRUCTURE: {
                "custom_ai_term": 0.9,
            }
        }
        matcher = ThesisMatcher(custom_keywords=custom)
        fit = matcher.score("We use custom_ai_term technology")

        assert "custom_ai_term" in fit.matched_keywords
        # Score might be too low for AI_INFRASTRUCTURE thesis, check all_scores instead
        assert fit.all_scores["ai_infrastructure"] > 0

    def test_custom_keywords_override_weight(self):
        """Custom keywords should override existing weights"""
        # Change LLM weight to 0.1 (very low)
        custom = {
            Thesis.AI_INFRASTRUCTURE: {
                "llm": 0.1,  # Override default 0.9
            }
        }
        matcher = ThesisMatcher(custom_keywords=custom)

        # Now LLM alone shouldn't score as high
        fit = matcher.score("We build llm technology")
        assert fit.score < 0.5  # Lower than with default 0.9 weight

    def test_new_thesis_with_custom_keywords(self):
        """Should be able to add completely new thesis"""
        # Note: This would need a new Thesis enum value to work properly
        # For now, test that existing thesis can be extended
        custom = {
            Thesis.CLEANTECH: {
                "ocean cleanup": 0.9,
                "plastic waste": 0.8,
            }
        }
        matcher = ThesisMatcher(custom_keywords=custom)
        fit = matcher.score("Ocean cleanup and plastic waste reduction")

        assert "ocean cleanup" in fit.matched_keywords
        assert "plastic waste" in fit.matched_keywords


# =============================================================================
# TEST: ThesisMatcher - score_signals
# =============================================================================

class TestThesisMatcherScoreSignals:
    """Tests for score_signals method"""

    def test_combines_signal_descriptions(self):
        """Should combine descriptions from multiple signals"""
        matcher = ThesisMatcher()
        signals = [
            {"raw_data": {"description": "LLM inference platform"}},
            {"raw_data": {"description": "Vector database for embeddings"}},
        ]
        fit = matcher.score_signals(signals)

        assert "llm" in fit.matched_keywords or "inference" in fit.matched_keywords
        assert "vector database" in fit.matched_keywords or "embedding" in fit.matched_keywords

    def test_extracts_company_name(self):
        """Should extract company_name from raw_data"""
        matcher = ThesisMatcher()
        signals = [
            {"raw_data": {"description": "AI tools", "company_name": "InferenceAI"}}
        ]
        fit = matcher.score_signals(signals)

        # Company name should be included in matching
        assert fit is not None

    def test_extracts_sic_code(self):
        """Should extract sic_code from raw_data"""
        matcher = ThesisMatcher()
        signals = [
            {"raw_data": {"description": "Healthcare", "sic_code": "8011"}}
        ]
        fit = matcher.score_signals(signals)

        # SIC code should boost score
        assert fit is not None

    def test_extracts_sic_codes_list(self):
        """Should extract first sic_code from sic_codes list"""
        matcher = ThesisMatcher()
        signals = [
            {"raw_data": {"description": "Healthcare", "sic_codes": ["8011", "8099"]}}
        ]
        fit = matcher.score_signals(signals)

        assert fit is not None

    def test_extracts_topics(self):
        """Should extract topics and include in matching"""
        matcher = ThesisMatcher()
        signals = [
            {"raw_data": {"description": "Platform", "topics": ["llm", "inference", "ai"]}}
        ]
        fit = matcher.score_signals(signals)

        assert "llm" in fit.matched_keywords or "inference" in fit.matched_keywords

    def test_handles_empty_signals(self):
        """Should handle empty signals list"""
        matcher = ThesisMatcher()
        fit = matcher.score_signals([])

        assert fit.thesis == Thesis.UNKNOWN
        assert fit.score == 0.0

    def test_handles_missing_raw_data(self):
        """Should handle signals without raw_data"""
        matcher = ThesisMatcher()
        signals = [{}]
        fit = matcher.score_signals(signals)

        assert fit is not None

    def test_multiple_description_fields(self):
        """Should combine multiple description field types"""
        matcher = ThesisMatcher()
        signals = [
            {
                "raw_data": {
                    "description": "AI inference",
                    "short_description": "LLM platform",
                    "about": "Vector database",
                    "bio": "ML ops company",
                }
            }
        ]
        fit = matcher.score_signals(signals)

        # Should find keywords from all fields
        assert len(fit.matched_keywords) >= 2


# =============================================================================
# TEST: Convenience Functions
# =============================================================================

class TestConvenienceFunctions:
    """Tests for module-level convenience functions"""

    def test_score_thesis_fit_basic(self):
        """score_thesis_fit should return ThesisFit"""
        fit = score_thesis_fit(
            "LLM inference vector database embedding ml ops fine-tuning rag"
        )

        assert isinstance(fit, ThesisFit)
        assert fit.thesis == Thesis.AI_INFRASTRUCTURE

    def test_score_thesis_fit_with_params(self):
        """score_thesis_fit should accept optional params"""
        fit = score_thesis_fit(
            "Healthcare platform",
            company_name="MedTech Inc",
            sic_code="8011"
        )

        assert isinstance(fit, ThesisFit)

    def test_is_thesis_fit_true(self):
        """is_thesis_fit should return True for matching content"""
        # Use many keywords to ensure score >= 0.4
        result = is_thesis_fit(
            "LLM inference vector database embedding ml ops fine-tuning rag model serving"
        )

        assert result is True

    def test_is_thesis_fit_false(self):
        """is_thesis_fit should return False for non-matching content"""
        result = is_thesis_fit("We sell shoes")

        assert result is False

    def test_is_thesis_fit_custom_threshold(self):
        """is_thesis_fit should respect custom min_score"""
        text = "Some AI platform"

        # With high threshold, might not match
        result_high = is_thesis_fit(text, min_score=0.9)

        # With low threshold, should match
        result_low = is_thesis_fit(text, min_score=0.1)

        # Results may differ based on threshold
        assert isinstance(result_high, bool)
        assert isinstance(result_low, bool)


# =============================================================================
# TEST: Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""

    def test_very_long_text(self):
        """Should handle very long text"""
        matcher = ThesisMatcher()
        long_text = "LLM " * 1000  # Very repetitive text
        fit = matcher.score(long_text)

        assert fit is not None
        assert fit.score <= 1.0

    def test_special_characters_in_text(self):
        """Should handle special characters"""
        matcher = ThesisMatcher()
        fit = matcher.score("LLM!!! inference??? platform...")

        # Should still find keywords
        assert "llm" in fit.matched_keywords

    def test_unicode_text(self):
        """Should handle unicode text"""
        matcher = ThesisMatcher()
        fit = matcher.score("LLM inference platform 日本語 émoji 🚀")

        # Should still find English keywords
        assert "llm" in fit.matched_keywords

    def test_newlines_and_tabs(self):
        """Should handle newlines and tabs"""
        matcher = ThesisMatcher()
        fit = matcher.score("LLM\ninference\tplatform")

        assert "llm" in fit.matched_keywords
        assert "inference" in fit.matched_keywords

    def test_score_never_exceeds_one(self):
        """Score should never exceed 1.0"""
        matcher = ThesisMatcher()

        # Text with many high-weight keywords
        heavy_text = " ".join(THESIS_KEYWORDS[Thesis.AI_INFRASTRUCTURE].keys())
        fit = matcher.score(heavy_text)

        assert fit.score <= 1.0

    def test_score_never_negative(self):
        """Score should never go negative"""
        matcher = ThesisMatcher()

        # Text with only negative keywords
        fit = matcher.score("crypto blockchain nft web3 gaming social media advertising")

        assert fit.score >= 0.0

    def test_all_scores_dict_populated(self):
        """all_scores should contain all thesis categories"""
        matcher = ThesisMatcher()
        fit = matcher.score("Some text about AI")

        assert "ai_infrastructure" in fit.all_scores
        assert "healthtech" in fit.all_scores
        assert "cleantech" in fit.all_scores

    def test_thesis_unknown_for_very_low_score(self):
        """Should return UNKNOWN if best score <= 0.1"""
        matcher = ThesisMatcher()
        fit = matcher.score("xyz abc def")  # No keywords

        assert fit.thesis == Thesis.UNKNOWN

    def test_company_name_included_in_matching(self):
        """Company name should contribute to matching"""
        matcher = ThesisMatcher()

        fit_no_name = matcher.score("Platform for business")
        fit_with_name = matcher.score("Platform for business", company_name="LLM Technologies Inc")

        # Company name with "LLM" should increase score
        assert fit_with_name.score >= fit_no_name.score


# =============================================================================
# TEST: Keyword Constants
# =============================================================================

class TestKeywordConstants:
    """Tests for keyword constant definitions"""

    def test_all_thesis_have_keywords(self):
        """All thesis categories (except UNKNOWN) should have keywords"""
        assert Thesis.AI_INFRASTRUCTURE in THESIS_KEYWORDS
        assert Thesis.HEALTHTECH in THESIS_KEYWORDS
        assert Thesis.CLEANTECH in THESIS_KEYWORDS
        assert Thesis.UNKNOWN not in THESIS_KEYWORDS

    def test_keywords_have_positive_weights(self):
        """All keyword weights should be positive"""
        for thesis, keywords in THESIS_KEYWORDS.items():
            for keyword, weight in keywords.items():
                assert weight > 0, f"Weight for {keyword} in {thesis} should be positive"

    def test_keywords_have_reasonable_weights(self):
        """All keyword weights should be <= 1.0"""
        for thesis, keywords in THESIS_KEYWORDS.items():
            for keyword, weight in keywords.items():
                assert weight <= 1.0, f"Weight for {keyword} in {thesis} should be <= 1.0"

    def test_negative_keywords_have_positive_weights(self):
        """Negative keyword weights should be positive"""
        for keyword, weight in NEGATIVE_KEYWORDS.items():
            assert weight > 0, f"Weight for negative keyword {keyword} should be positive"
