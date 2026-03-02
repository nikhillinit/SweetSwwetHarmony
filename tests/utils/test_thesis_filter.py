"""Tests for ThesisFilter - combines keyword + LLM classification."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from utils.thesis_filter import (
    ThesisFilter,
    ThesisFilterConfig,
    ThesisFilterResult,
    RoutingDecision,
)


class TestRoutingDecision:
    """Test routing decision enum."""

    def test_qualified_value(self):
        assert RoutingDecision.QUALIFIED.value == "qualified"

    def test_held_value(self):
        assert RoutingDecision.HELD.value == "held"

    def test_rejected_value(self):
        assert RoutingDecision.REJECTED.value == "rejected"


class TestThesisFilterConfig:
    """Test filter configuration."""

    def test_default_hold_threshold(self):
        config = ThesisFilterConfig()
        assert config.hold_threshold == 0.3

    def test_default_skip_llm_threshold(self):
        config = ThesisFilterConfig()
        assert config.skip_llm_if_keyword_below == 0.2

    def test_default_keyword_high_threshold(self):
        config = ThesisFilterConfig()
        assert config.keyword_high_threshold == 0.7

    def test_default_keyword_low_threshold(self):
        config = ThesisFilterConfig()
        assert config.keyword_low_threshold == 0.4

    def test_default_high_boost(self):
        config = ThesisFilterConfig()
        assert config.high_boost == 0.08

    def test_default_low_penalty(self):
        config = ThesisFilterConfig()
        assert config.low_penalty == -0.08

    def test_default_negative_keyword_penalty(self):
        config = ThesisFilterConfig()
        assert config.negative_keyword_penalty == -0.12

    def test_custom_config_values(self):
        config = ThesisFilterConfig(
            hold_threshold=0.4,
            skip_llm_if_keyword_below=0.3,
        )
        assert config.hold_threshold == 0.4
        assert config.skip_llm_if_keyword_below == 0.3


class TestThesisFilterResult:
    """Test filter result dataclass."""

    def test_routing_decision_qualified(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            llm_score=0.75,
            llm_category="consumer_cpg",
            confidence_adjustment=0.08,
        )
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.keyword_score == 0.6
        assert result.llm_score == 0.75

    def test_routing_decision_held(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.HELD,
            keyword_score=0.3,
            llm_score=0.25,
        )
        assert result.routing == RoutingDecision.HELD

    def test_routing_decision_rejected(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.REJECTED,
            keyword_score=0.1,
            negative_keywords=["enterprise", "b2b"],
        )
        assert result.routing == RoutingDecision.REJECTED

    def test_to_dict_returns_dict(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            llm_score=0.75,
            llm_category="consumer_cpg",
            confidence_adjustment=0.08,
        )
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["routing"] == "qualified"
        assert d["keyword_score"] == 0.6
        assert d["llm_score"] == 0.75

    def test_to_dict_handles_none_values(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.HELD,
            keyword_score=0.3,
        )
        d = result.to_dict()
        assert d["llm_score"] is None
        assert d["llm_category"] is None

    # Phase B: Test new fields
    def test_intent_phrases_matched_default_empty(self):
        """Intent phrases matched defaults to empty list."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
        )
        assert result.intent_phrases_matched == []

    def test_intent_phrases_matched_populated(self):
        """Intent phrases matched can be populated."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.7,
            intent_phrases_matched=["join waitlist", "pricing"],
        )
        assert result.intent_phrases_matched == ["join waitlist", "pricing"]

    def test_domain_match_default_false(self):
        """Domain match defaults to False."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
        )
        assert result.domain_match is False

    def test_domain_match_true(self):
        """Domain match can be set to True."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.7,
            domain_match=True,
        )
        assert result.domain_match is True

    def test_domain_blacklisted_default_false(self):
        """Domain blacklisted defaults to False."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
        )
        assert result.domain_blacklisted is False

    def test_domain_blacklisted_true(self):
        """Domain blacklisted can be set to True."""
        result = ThesisFilterResult(
            routing=RoutingDecision.REJECTED,
            keyword_score=0.0,
            domain_blacklisted=True,
        )
        assert result.domain_blacklisted is True

    def test_to_dict_includes_phase_b_fields(self):
        """to_dict includes Phase B fields."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.7,
            intent_phrases_matched=["join waitlist"],
            domain_match=True,
            domain_blacklisted=False,
        )
        d = result.to_dict()
        assert "intent_phrases_matched" in d
        assert "domain_match" in d
        assert "domain_blacklisted" in d
        assert d["intent_phrases_matched"] == ["join waitlist"]
        assert d["domain_match"] is True
        assert d["domain_blacklisted"] is False


class TestThesisFilterRouting:
    """Test routing logic."""

    @pytest.fixture
    def filter_instance(self):
        return ThesisFilter(ThesisFilterConfig())

    @pytest.mark.asyncio
    async def test_excluded_category_is_rejected(self, filter_instance):
        """LLM category=excluded should route to REJECTED."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=False,
            thesis_fit_score=0.1,
            category="excluded",
            rationale="B2B enterprise software",
        )
        filter_instance._llm_classifier = mock_llm

        # Use text with keyword score >= 0.2 so LLM is actually invoked
        result = await filter_instance.classify("Healthy food delivery startup")
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_low_score_is_held(self, filter_instance):
        """LLM score < 0.3 should route to HELD."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=False,
            thesis_fit_score=0.2,
            category="other",
            rationale="Unclear fit",
        )
        filter_instance._llm_classifier = mock_llm

        result = await filter_instance.classify("Random unrelated company")
        assert result.routing == RoutingDecision.HELD

    @pytest.mark.asyncio
    async def test_good_score_is_qualified(self, filter_instance):
        """LLM score >= 0.3 should route to QUALIFIED."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=True,
            thesis_fit_score=0.75,
            category="consumer_cpg",
            rationale="Meal kit delivery fits consumer thesis",
        )
        filter_instance._llm_classifier = mock_llm

        result = await filter_instance.classify("Healthy meal kit delivery startup")
        assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_keyword_only_mode_skips_llm(self, filter_instance):
        """skip_llm=True should not call LLM classifier."""
        mock_llm = AsyncMock()
        filter_instance._llm_classifier = mock_llm

        result = await filter_instance.classify(
            "Healthy meal kit delivery startup",
            skip_llm=True,
        )
        mock_llm.classify.assert_not_called()
        assert result.llm_skipped is True

    @pytest.mark.asyncio
    async def test_low_keyword_score_skips_llm(self, filter_instance):
        """Very low keyword score should skip LLM (obvious non-fit)."""
        mock_llm = AsyncMock()
        filter_instance._llm_classifier = mock_llm

        # Text that won't match any keywords
        result = await filter_instance.classify("xyz random text nothing here")
        mock_llm.classify.assert_not_called()
        assert result.llm_skipped is True

    @pytest.mark.asyncio
    async def test_hard_hold_keywords_with_skip_llm_held(self, filter_instance):
        """Hard-hold keywords (enterprise/b2b) route to HELD (ADR-1: 3-tier model)."""
        result = await filter_instance.classify(
            "Enterprise B2B SaaS platform for developers",
            skip_llm=True,
        )
        assert result.routing == RoutingDecision.HELD
        assert len(result.negative_keywords) > 0


class TestConfidenceAdjustment:
    """Test confidence adjustment calculation."""

    @pytest.fixture
    def filter_instance(self):
        return ThesisFilter(ThesisFilterConfig())

    def test_high_keyword_score_positive_adjustment(self, filter_instance):
        """Keyword score >= 0.7 should give +0.08 adjustment."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.75,
            negative_keywords=[],
        )
        assert adjustment == 0.08

    def test_low_keyword_score_negative_adjustment(self, filter_instance):
        """Keyword score < 0.4 should give -0.08 adjustment."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.3,
            negative_keywords=[],
        )
        assert adjustment == -0.08

    def test_negative_keywords_extra_penalty(self, filter_instance):
        """Negative keywords should give additional -0.12 penalty."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.5,
            negative_keywords=["enterprise", "b2b"],
        )
        assert adjustment == -0.12

    def test_medium_score_no_adjustment(self, filter_instance):
        """Keyword score 0.4-0.7 with no negatives = 0 adjustment."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.5,
            negative_keywords=[],
        )
        assert adjustment == 0.0

    def test_exactly_07_is_high(self, filter_instance):
        """Keyword score exactly 0.7 should get positive boost."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.7,
            negative_keywords=[],
        )
        assert adjustment == 0.08

    def test_exactly_04_is_medium(self, filter_instance):
        """Keyword score exactly 0.4 should be medium (no adjustment)."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.4,
            negative_keywords=[],
        )
        assert adjustment == 0.0

    def test_negative_keywords_override_high_boost(self, filter_instance):
        """Negative keywords override positive boost — precedence rule."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.8,
            negative_keywords=["crypto"],
        )
        # High score would give +0.08, but negative keywords override to -0.12
        assert adjustment == -0.12

    def test_empty_negative_list_no_override(self, filter_instance):
        """Empty negative list does NOT trigger override — same as None."""
        adjustment = filter_instance._calculate_adjustment(
            keyword_score=0.3,
            negative_keywords=[],
        )
        # Low score without negatives → -0.08 (low_penalty), NOT -0.12
        assert adjustment == -0.08


class TestThesisFilterIntegration:
    """Integration tests with real keyword matcher."""

    @pytest.fixture
    def filter_instance(self):
        return ThesisFilter(ThesisFilterConfig())

    @pytest.mark.asyncio
    async def test_meal_kit_is_qualified_with_skip_llm(self, filter_instance):
        """High-scoring consumer CPG text should be QUALIFIED."""
        result = await filter_instance.classify(
            "We make healthy meal kits delivered to your door",
            skip_llm=True,
        )
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.keyword_category == "consumer_cpg"
        assert "meal kit" in result.keyword_matches or "meal kits" in result.keyword_matches

    @pytest.mark.asyncio
    async def test_fitness_app_is_qualified_with_skip_llm(self, filter_instance):
        """Health tech text should be QUALIFIED."""
        result = await filter_instance.classify(
            "A fitness app for tracking your workouts and wellness",
            skip_llm=True,
        )
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.keyword_category == "consumer_health_tech"

    @pytest.mark.asyncio
    async def test_enterprise_saas_is_held_with_skip_llm(self, filter_instance):
        """Enterprise B2B routes to HELD (ADR-1: hard_hold, not rejected)."""
        result = await filter_instance.classify(
            "Enterprise B2B SaaS platform for developers",
            skip_llm=True,
        )
        assert result.routing == RoutingDecision.HELD
        assert "enterprise" in result.negative_keywords or "b2b" in result.negative_keywords

    @pytest.mark.asyncio
    async def test_result_includes_keyword_matches(self, filter_instance):
        """Result should include matched keywords."""
        result = await filter_instance.classify(
            "Travel booking platform for unique hotel experiences",
            skip_llm=True,
        )
        assert len(result.keyword_matches) > 0

    @pytest.mark.asyncio
    async def test_company_name_included_in_scoring(self, filter_instance):
        """Company name should be included in keyword matching."""
        result = await filter_instance.classify(
            "We deliver food",
            company_name="Healthy Meal Kit Co",
            skip_llm=True,
        )
        # Company name contains "meal kit" which should boost score
        assert result.keyword_score > 0.3


class TestThesisFilterWithMockedLLM:
    """Test full two-stage flow with mocked LLM."""

    @pytest.fixture
    def filter_instance(self):
        return ThesisFilter(ThesisFilterConfig())

    @pytest.mark.asyncio
    async def test_llm_result_used_for_routing(self, filter_instance):
        """LLM result should determine final routing."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=True,
            thesis_fit_score=0.85,
            category="consumer_cpg",
            rationale="Strong consumer CPG fit",
        )
        filter_instance._llm_classifier = mock_llm

        result = await filter_instance.classify("Healthy food delivery startup")
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.llm_score == 0.85
        assert result.llm_category == "consumer_cpg"
        assert result.llm_skipped is False

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_keywords(self, filter_instance):
        """If LLM fails, should fall back to keyword-only routing."""
        mock_llm = AsyncMock()
        mock_llm.classify.side_effect = Exception("API error")
        filter_instance._llm_classifier = mock_llm

        # Use high-scoring keyword text
        result = await filter_instance.classify("Premium skincare brand with d2c model")
        # Should still route based on keywords
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.llm_score is None

    @pytest.mark.asyncio
    async def test_llm_failure_payload_falls_back_to_keywords(self, filter_instance):
        """Operational-failure payloads should fail open to keyword routing."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=False,
            thesis_fit_score=0.0,
            category="excluded",
            rationale="Classification failed: GOOGLE_API_KEY not set",
        )
        filter_instance._llm_classifier = mock_llm

        result = await filter_instance.classify("Premium skincare brand with d2c model")
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.llm_skipped is True
        assert result.llm_score is None
        assert result.llm_category is None

    @pytest.mark.asyncio
    async def test_llm_rationale_captured(self, filter_instance):
        """LLM rationale should be captured in result."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=True,
            thesis_fit_score=0.7,
            category="consumer_health_tech",
            rationale="Wellness app with strong consumer focus",
        )
        filter_instance._llm_classifier = mock_llm

        result = await filter_instance.classify("Meditation and wellness platform")
        assert result.llm_rationale == "Wellness app with strong consumer focus"


# =============================================================================
# Phase B Tests: Domain Matching and Intent Phrases in ThesisFilter
# =============================================================================


# =============================================================================
# Phase 0B-3 Tests: v2_shadow wiring through ThesisFilterResult
# =============================================================================


class TestThesisFilterResultV2Shadow:
    """Test v2_shadow field on ThesisFilterResult."""

    def test_v2_shadow_field_exists_and_defaults_to_none(self):
        """ThesisFilterResult should have v2_shadow field defaulting to None."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
        )
        assert hasattr(result, "v2_shadow")
        assert result.v2_shadow is None

    def test_v2_shadow_can_be_populated(self):
        """v2_shadow can be set with shadow diff data."""
        shadow_diff = {
            "v1": {"score": 0.5, "routing": "QUALIFIED"},
            "v2": {"score": 0.45, "routing": "QUALIFIED"},
            "delta_score": -0.05,
            "would_change_routing": False,
        }
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
            v2_shadow=shadow_diff,
        )
        assert result.v2_shadow == shadow_diff

    def test_v2_shadow_included_in_to_dict_when_present(self):
        """to_dict() should include v2_shadow when it has a value."""
        shadow_diff = {"delta_score": -0.05}
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
            v2_shadow=shadow_diff,
        )
        d = result.to_dict()
        assert "v2_shadow" in d
        assert d["v2_shadow"] == shadow_diff

    def test_v2_shadow_excluded_from_to_dict_when_none(self):
        """to_dict() should exclude v2_shadow when None."""
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
        )
        d = result.to_dict()
        # Should not include v2_shadow if None to keep output clean
        assert "v2_shadow" not in d


class TestThesisFilterV2ShadowWiring:
    """Test v2_shadow is wired from ThesisMatcher through classify()."""

    @pytest.mark.asyncio
    async def test_classify_populates_v2_shadow_from_trace(self, tmp_path):
        """classify() should populate v2_shadow from keyword_fit.trace.v2_shadow."""
        # Create policy file with different weights to trigger shadow diff
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.8\n"  # Different from v1's 0.5
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig
        from utils.thesis_matcher import ThesisMatcher
        import os

        # Create filter with custom ThesisMatcher in shadow mode
        config = ThesisFilterConfig()
        thesis_filter = ThesisFilter(config)
        thesis_filter._keyword_matcher = ThesisMatcher(
            v2_enablement="shadow",
            config_path=str(tmp_path)
        )

        result = await thesis_filter.classify(
            "enterprise food delivery startup",
            skip_llm=True,
        )

        # Should have v2_shadow populated
        assert result.v2_shadow is not None
        assert "v1" in result.v2_shadow
        assert "v2" in result.v2_shadow
        assert "delta_score" in result.v2_shadow

    @pytest.mark.asyncio
    async def test_classify_v2_shadow_none_when_disabled(self):
        """classify() should have v2_shadow=None when v2 is disabled."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig
        from utils.thesis_matcher import ThesisMatcher

        config = ThesisFilterConfig()
        thesis_filter = ThesisFilter(config)
        thesis_filter._keyword_matcher = ThesisMatcher(v2_enablement="disabled")

        result = await thesis_filter.classify(
            "enterprise food delivery startup",
            skip_llm=True,
        )

        # Should NOT have v2_shadow when disabled
        assert result.v2_shadow is None


class TestThesisFilterPhaseB:
    """Test Phase B enhancements in ThesisFilter."""

    @pytest.fixture
    def filter_instance(self):
        return ThesisFilter(ThesisFilterConfig())

    @pytest.mark.asyncio
    async def test_domain_name_passed_to_matcher(self, filter_instance):
        """Domain name should be passed to underlying ThesisMatcher."""
        result = await filter_instance.classify(
            "Health tracking app",
            domain_name="getfitness.com",
            skip_llm=True,
        )
        # Should capture domain match from ThesisMatcher
        assert result.domain_match is True

    @pytest.mark.asyncio
    async def test_domain_blacklist_applied(self, filter_instance):
        """Blacklisted domains should be captured in result."""
        result = await filter_instance.classify(
            "Great meal kit startup",
            domain_name="localhost:3000",
            skip_llm=True,
        )
        assert result.domain_blacklisted is True

    @pytest.mark.asyncio
    async def test_intent_phrases_captured(self, filter_instance):
        """Intent phrases should be captured in result."""
        result = await filter_instance.classify(
            "Fitness app - join waitlist for early access",
            skip_llm=True,
        )
        assert "join waitlist" in result.intent_phrases_matched

    @pytest.mark.asyncio
    async def test_multiple_intent_phrases(self, filter_instance):
        """Multiple intent phrases should all be captured."""
        result = await filter_instance.classify(
            "Health app - join waitlist - check pricing - subscribe now",
            skip_llm=True,
        )
        assert len(result.intent_phrases_matched) >= 2

    @pytest.mark.asyncio
    async def test_get_prefix_domain_boosts_score(self, filter_instance):
        """get* domain pattern should boost score."""
        base_result = await filter_instance.classify(
            "Health tracking app",
            skip_llm=True,
        )
        domain_result = await filter_instance.classify(
            "Health tracking app",
            domain_name="getfitness.com",
            skip_llm=True,
        )
        # Domain match should boost score
        assert domain_result.keyword_score >= base_result.keyword_score

    @pytest.mark.asyncio
    async def test_try_prefix_domain_matches(self, filter_instance):
        """try* domain pattern should be detected."""
        result = await filter_instance.classify(
            "Meal delivery service",
            domain_name="tryfresh.io",
            skip_llm=True,
        )
        assert result.domain_match is True

    @pytest.mark.asyncio
    async def test_join_prefix_domain_matches(self, filter_instance):
        """join* domain pattern should be detected."""
        result = await filter_instance.classify(
            "Wellness community",
            domain_name="joinwellness.co",
            skip_llm=True,
        )
        assert result.domain_match is True

    @pytest.mark.asyncio
    async def test_regular_domain_no_match(self, filter_instance):
        """Regular domain should not trigger domain match."""
        result = await filter_instance.classify(
            "Health app",
            domain_name="healthapp.com",
            skip_llm=True,
        )
        assert result.domain_match is False

    @pytest.mark.asyncio
    async def test_staging_domain_blacklisted(self, filter_instance):
        """Staging domains should be blacklisted."""
        result = await filter_instance.classify(
            "Travel booking platform",
            domain_name="staging.myapp.com",
            skip_llm=True,
        )
        assert result.domain_blacklisted is True

    @pytest.mark.asyncio
    async def test_example_domain_blacklisted(self, filter_instance):
        """Example domains should be blacklisted."""
        result = await filter_instance.classify(
            "Marketplace for goods",
            domain_name="example.com",
            skip_llm=True,
        )
        assert result.domain_blacklisted is True

    @pytest.mark.asyncio
    async def test_to_dict_has_phase_b_fields(self, filter_instance):
        """Result.to_dict() should include Phase B fields."""
        result = await filter_instance.classify(
            "Health app - join waitlist",
            domain_name="getfitness.com",
            skip_llm=True,
        )
        d = result.to_dict()
        assert "intent_phrases_matched" in d
        assert "domain_match" in d
        assert "domain_blacklisted" in d
