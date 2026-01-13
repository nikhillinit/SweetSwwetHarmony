"""Tests for Consumer thesis keyword matcher."""
import pytest
from utils.thesis_matcher import (
    ThesisMatcher,
    ThesisFit,
    ConsumerThesis,
    CONSUMER_KEYWORDS,
    NEGATIVE_KEYWORDS,
)


class TestConsumerThesisEnum:
    """Test ConsumerThesis enum values."""

    def test_enum_has_consumer_cpg(self):
        assert ConsumerThesis.CONSUMER_CPG.value == "consumer_cpg"

    def test_enum_has_consumer_health_tech(self):
        assert ConsumerThesis.CONSUMER_HEALTH_TECH.value == "consumer_health_tech"

    def test_enum_has_travel_hospitality(self):
        assert ConsumerThesis.TRAVEL_HOSPITALITY.value == "travel_hospitality"

    def test_enum_has_consumer_marketplace(self):
        assert ConsumerThesis.CONSUMER_MARKETPLACE.value == "consumer_marketplace"

    def test_enum_has_unknown(self):
        assert ConsumerThesis.UNKNOWN.value == "unknown"


class TestConsumerKeywords:
    """Test keyword definitions."""

    def test_cpg_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_CPG in CONSUMER_KEYWORDS
        assert "meal kit" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_CPG]

    def test_health_tech_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_HEALTH_TECH in CONSUMER_KEYWORDS
        assert "fitness app" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_HEALTH_TECH]

    def test_travel_keywords_exist(self):
        assert ConsumerThesis.TRAVEL_HOSPITALITY in CONSUMER_KEYWORDS
        assert "travel booking" in CONSUMER_KEYWORDS[ConsumerThesis.TRAVEL_HOSPITALITY]

    def test_marketplace_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_MARKETPLACE in CONSUMER_KEYWORDS
        assert "marketplace" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_MARKETPLACE]


class TestNegativeKeywords:
    """Test negative/exclusion keywords."""

    def test_enterprise_is_negative(self):
        assert "enterprise" in NEGATIVE_KEYWORDS

    def test_b2b_is_negative(self):
        assert "b2b" in NEGATIVE_KEYWORDS

    def test_crypto_is_negative(self):
        assert "crypto" in NEGATIVE_KEYWORDS

    def test_blockchain_is_negative(self):
        assert "blockchain" in NEGATIVE_KEYWORDS


class TestThesisFitDataclass:
    """Test ThesisFit result dataclass."""

    def test_is_fit_true_when_score_above_threshold(self):
        fit = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.7,
            matched_keywords=["meal kit"],
            negative_keywords=[],
            all_scores={},
            confidence="HIGH",
        )
        assert fit.is_fit is True

    def test_is_fit_false_when_score_below_threshold(self):
        fit = ThesisFit(
            thesis=ConsumerThesis.UNKNOWN,
            score=0.2,
            matched_keywords=[],
            negative_keywords=["enterprise"],
            all_scores={},
            confidence="LOW",
        )
        assert fit.is_fit is False


class TestThesisMatcherScoring:
    """Test ThesisMatcher scoring logic."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_cpg_description_scores_cpg(self, matcher):
        fit = matcher.score("We make healthy meal kits delivered to your door")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        assert fit.score >= 0.5

    def test_health_tech_description_scores_health_tech(self, matcher):
        fit = matcher.score("A fitness app for tracking your workouts and wellness")
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.5

    def test_travel_description_scores_travel(self, matcher):
        fit = matcher.score("Travel booking platform for unique hotel experiences")
        assert fit.thesis == ConsumerThesis.TRAVEL_HOSPITALITY
        assert fit.score >= 0.5

    def test_marketplace_description_scores_marketplace(self, matcher):
        fit = matcher.score("Consumer marketplace connecting buyers and sellers")
        assert fit.thesis == ConsumerThesis.CONSUMER_MARKETPLACE
        assert fit.score >= 0.5

    def test_negative_keywords_reduce_score(self, matcher):
        fit = matcher.score("Enterprise B2B SaaS platform for developers")
        assert fit.score < 0.4
        assert "enterprise" in fit.negative_keywords or "b2b" in fit.negative_keywords

    def test_empty_text_returns_unknown(self, matcher):
        fit = matcher.score("")
        assert fit.thesis == ConsumerThesis.UNKNOWN
        assert fit.score == 0.0

    def test_confidence_high_when_score_above_07(self, matcher):
        fit = matcher.score("Premium skincare brand with d2c subscription model for beauty products")
        assert fit.confidence == "HIGH" or fit.score >= 0.7

    def test_confidence_low_when_score_below_04(self, matcher):
        fit = matcher.score("Random unrelated text about nothing")
        assert fit.confidence == "LOW"
