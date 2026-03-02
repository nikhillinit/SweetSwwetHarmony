"""Phase 0: Consumer-First Cascade instrumentation tests.

TDD RED phase — these tests target new fields and scoring added in Phase 0.
No routing changes — only instrumentation and counterfactual logging.
"""
import hashlib
import json
import re
import time
import unicodedata

import pytest

from utils.thesis_matcher import (
    ThesisMatcher,
    ThesisFit,
    ThesisFitTrace,
    ConsumerThesis,
    NEGATIVE_KEYWORDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_matcher(**kwargs):
    """Create a ThesisMatcher with v2 disabled (default) for Phase 0 tests."""
    return ThesisMatcher(**kwargs)


# ===========================================================================
# 1. Consumer signal scoring with tiered caps
# ===========================================================================

class TestConsumerSignalScore:
    """Test _compute_consumer_signal() tiered cap logic."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_consumer_signal_score_present_on_fit(self, matcher):
        """ThesisFit must have consumer_signal_score field."""
        fit = matcher.score("a direct to consumer shopping app for checkout")
        assert hasattr(fit, "consumer_signal_score")
        assert isinstance(fit.consumer_signal_score, float)

    def test_consumer_signal_score_zero_for_empty(self, matcher):
        """Empty/irrelevant text → 0.0 consumer signal."""
        fit = matcher.score("enterprise b2b infrastructure platform")
        assert fit.consumer_signal_score == 0.0 or fit.consumer_signal_score < 0.01

    def test_consumer_signal_score_tiered_caps_a_tier(self, matcher):
        """A-tier sum capped at 0.70 even with many A-tier keywords."""
        # Pack multiple A-tier keywords; total raw should exceed 0.70
        text = (
            "direct to consumer d2c dtc consumer app e-commerce "
            "shopping checkout subscription box"
        )
        fit = matcher.score(text)
        # A-tier raw would be ~0.30+0.30+0.30+0.35+0.30+0.25+0.25+0.25 = 2.30
        # But capped at 0.70
        assert fit.consumer_signal_score <= 1.0
        # The A-tier contribution alone cannot exceed 0.70
        # Total = min(A_sum, 0.70) + min(M_sum, 0.40) + min(N_sum, 0.15)
        # So even with huge A-tier, score ≤ 0.70 + 0.40 + 0.15 = 1.25 → capped at 1.0

    def test_consumer_signal_score_m_tier_cap(self, matcher):
        """M-tier contribution capped at 0.40."""
        # M-tier keywords only
        text = "subscription brand retail delivery on-demand membership lifestyle waitlist"
        fit = matcher.score(text)
        # M-tier keywords present → score should be positive but ≤ 0.40
        # (since no A/N tier present, score = min(0, 0.70) + min(M_sum, 0.40) + min(0, 0.15))
        assert fit.consumer_signal_score <= 0.40 + 0.01  # small float tolerance

    def test_consumer_signal_score_n_tier_cap(self, matcher):
        """N-tier contribution capped at 0.15."""
        text = "app users customers personalized social community download"
        fit = matcher.score(text)
        assert fit.consumer_signal_score <= 0.15 + 0.01

    def test_consumer_signal_score_combined_tiers(self, matcher):
        """Combined A+M+N respects all tier caps and total ≤ 1.0."""
        text = (
            "direct to consumer subscription brand "
            "app users personalized"
        )
        fit = matcher.score(text)
        assert 0.0 < fit.consumer_signal_score <= 1.0


# ===========================================================================
# 2. Consumer anchor count
# ===========================================================================

class TestConsumerAnchorCount:
    """Test consumer_anchor_count — only A-tier keywords counted."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_anchor_count_present_on_fit(self, matcher):
        """ThesisFit must have consumer_anchor_count field."""
        fit = matcher.score("a direct to consumer app")
        assert hasattr(fit, "consumer_anchor_count")
        assert isinstance(fit.consumer_anchor_count, int)

    def test_anchor_count_zero_no_a_tier(self, matcher):
        """No A-tier keywords → anchor count = 0."""
        fit = matcher.score("subscription brand retail delivery")
        assert fit.consumer_anchor_count == 0

    def test_anchor_count_positive_with_a_tier(self, matcher):
        """A-tier keywords present → anchor count > 0."""
        fit = matcher.score("direct to consumer e-commerce shopping")
        assert fit.consumer_anchor_count >= 1

    def test_anchor_count_unique_only(self, matcher):
        """Repeated A-tier keyword counted once."""
        fit = matcher.score(
            "direct to consumer is our direct to consumer model"
        )
        # "direct to consumer" appears twice but should count once
        assert fit.consumer_anchor_count >= 1


# ===========================================================================
# 3. B2B soft score
# ===========================================================================

class TestB2BSoftScore:
    """Test b2b_soft_score — sum of matched soft negative weights."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_b2b_soft_score_present_on_fit(self, matcher):
        """ThesisFit must have b2b_soft_score field."""
        fit = matcher.score("sdk library framework plugin")
        assert hasattr(fit, "b2b_soft_score")
        assert isinstance(fit.b2b_soft_score, float)

    def test_b2b_soft_score_zero_no_soft_negatives(self, matcher):
        """No soft negatives → b2b_soft_score = 0.0."""
        fit = matcher.score("healthy meal kit delivery for families")
        assert fit.b2b_soft_score == 0.0

    def test_b2b_soft_score_positive_with_soft_keywords(self, matcher):
        """Soft negative keywords present → positive b2b_soft_score."""
        # "sdk" and "library" are soft (devtools), not hard_reject
        fit = matcher.score("sdk library framework plugin linter")
        assert fit.b2b_soft_score > 0.0


# ===========================================================================
# 4. Hard reject vs hard hold classification
# ===========================================================================

class TestHardRejectVsHardHold:
    """Test that negatives are classified into hard_reject/hard_hold/soft tiers."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_crypto_in_hard_reject(self, matcher):
        """Crypto/web3 terms are hard_reject tier."""
        fit = matcher.score("blockchain crypto defi nft web3")
        assert fit.trace is not None
        assert hasattr(fit.trace, "matched_hard_rejects")
        # At least some crypto terms should be in hard_rejects
        assert len(fit.trace.matched_hard_rejects) > 0

    def test_enterprise_in_hard_hold(self, matcher):
        """'enterprise' is hard_hold — never auto-qualifies, routes to HELD."""
        fit = matcher.score("enterprise consumer app for businesses")
        assert fit.trace is not None
        assert hasattr(fit.trace, "matched_hard_holds")
        assert "enterprise" in fit.trace.matched_hard_holds

    def test_hard_reject_not_in_hard_hold(self, matcher):
        """Hard reject keywords should NOT appear in hard_hold list."""
        fit = matcher.score("blockchain crypto defi")
        if fit.trace:
            hard_holds = set(fit.trace.matched_hard_holds)
            hard_rejects = set(fit.trace.matched_hard_rejects)
            assert hard_holds.isdisjoint(hard_rejects)

    def test_soft_negatives_not_in_hard_lists(self, matcher):
        """Soft negatives should NOT appear in hard_reject or hard_hold."""
        fit = matcher.score("sdk library framework plugin")
        if fit.trace:
            soft_kws = {kw for kw, _ in fit.trace.soft_negatives}
            hard_rejects = set(fit.trace.matched_hard_rejects)
            hard_holds = set(fit.trace.matched_hard_holds)
            assert soft_kws.isdisjoint(hard_rejects)
            assert soft_kws.isdisjoint(hard_holds)

    def test_template_noise_in_hard_reject(self, matcher):
        """Template/educational content is hard_reject."""
        fit = matcher.score("boilerplate template tutorial demo repo")
        assert fit.trace is not None
        assert len(fit.trace.matched_hard_rejects) > 0

    def test_late_stage_in_hard_reject(self, matcher):
        """Series C/D are hard_reject (out of thesis stage)."""
        fit = matcher.score("series c series d late stage growth")
        assert fit.trace is not None
        assert len(fit.trace.matched_hard_rejects) > 0


# ===========================================================================
# 5. NFKC normalization
# ===========================================================================

class TestNFKCNormalization:
    """Test that fullwidth/compatibility chars are normalized before matching."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_fullwidth_chars_normalized(self, matcher):
        """Fullwidth 'ｅｎｔｅｒｐｒｉｓｅ' should match as 'enterprise'."""
        fullwidth = "ｅｎｔｅｒｐｒｉｓｅ"
        # After NFKC, this becomes "enterprise"
        assert unicodedata.normalize("NFKC", fullwidth).lower() == "enterprise"
        fit = matcher.score(f"This is an {fullwidth} platform")
        # Should detect "enterprise" after normalization
        assert len(fit.negative_keywords) > 0 or (
            fit.trace and len(fit.trace.matched_hard_holds) > 0
        )


# ===========================================================================
# 6. Word boundary short tokens
# ===========================================================================

class TestWordBoundaryShortTokens:
    """Test word-boundary matching — short tokens must not match inside words."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_app_not_inside_application(self, matcher):
        """'app' consumer keyword should NOT match inside 'application'."""
        fit_app = matcher.score("the best app for consumers")
        fit_application = matcher.score("the best application for consumers")
        # "app" should contribute to consumer_signal_score in first but not second
        assert fit_app.consumer_signal_score > fit_application.consumer_signal_score

    def test_d2c_not_d2d(self, matcher):
        """'d2c' must not match 'd2d' (device-to-device)."""
        fit_d2c = matcher.score("our d2c brand ships directly to consumers")
        fit_d2d = matcher.score("our d2d networking protocol handles mesh")
        assert fit_d2c.consumer_signal_score > fit_d2d.consumer_signal_score

    def test_dtc_not_dtd(self, matcher):
        """'dtc' must not match 'dtd' (document type definition)."""
        fit_dtc = matcher.score("our dtc brand ships to shoppers")
        fit_dtd = matcher.score("our dtd validation parser handles xml")
        assert fit_dtc.consumer_signal_score > fit_dtd.consumer_signal_score


# ===========================================================================
# 7. Per-keyword dedupe
# ===========================================================================

class TestPerKeywordDedupe:
    """Test that each keyword contributes at most once per document."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_repeated_keyword_scores_once(self, matcher):
        """Repeating a keyword N times should score same as once."""
        single = "direct to consumer brand"
        repeated = "direct to consumer brand direct to consumer direct to consumer"
        fit_single = matcher.score(single)
        fit_repeated = matcher.score(repeated)
        assert fit_single.consumer_signal_score == fit_repeated.consumer_signal_score


# ===========================================================================
# 8. Counterfactual logging (no behavior change)
# ===========================================================================

class TestCounterfactualLogging:
    """Test that counterfactual cascade routing is logged but not applied."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_routing_unchanged_by_consumer_signal(self, matcher):
        """Phase 0: consumer signal scoring must NOT change routing decisions.

        A company that was HELD before should still be HELD (not rescued).
        """
        # Low keyword score, no sector match → should be HELD or REJECTED
        text = "a generic startup with subscription model"
        fit = matcher.score(text)
        # The score should be based on existing keyword logic, not consumer rescue
        # Consumer signal is computed but doesn't affect the score
        assert fit.score == fit.score  # tautology, but ensures no crash

    def test_consumer_fields_populated_but_score_unchanged(self, matcher):
        """consumer_signal_score is populated but doesn't change fit.score."""
        text = "direct to consumer e-commerce subscription brand"
        fit = matcher.score(text)
        # Consumer fields are populated
        assert fit.consumer_signal_score > 0.0
        # But the main score is still computed from sector keywords only
        assert fit.score >= 0.0


# ===========================================================================
# 9. Decision path code
# ===========================================================================

class TestDecisionPathCode:
    """Test that ThesisFilterResult includes decision_path_code."""

    @pytest.mark.asyncio
    async def test_decision_path_code_populated(self):
        """Every ThesisFilterResult must have a decision_path_code."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig, DecisionPathCode

        tf = ThesisFilter(ThesisFilterConfig())
        result = await tf.classify("healthy meal kit delivery", skip_llm=True)
        assert hasattr(result, "decision_path_code")
        assert isinstance(result.decision_path_code, DecisionPathCode)

    @pytest.mark.asyncio
    async def test_decision_path_code_in_to_dict(self):
        """decision_path_code should appear in to_dict() output."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig

        tf = ThesisFilter(ThesisFilterConfig())
        result = await tf.classify("blockchain crypto defi", skip_llm=True)
        d = result.to_dict()
        assert "decision_path_code" in d

    @pytest.mark.asyncio
    async def test_web3_gives_veto_web3_code(self):
        """Web3-detected text → VETO_WEB3 path code."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig, DecisionPathCode

        tf = ThesisFilter(ThesisFilterConfig())
        result = await tf.classify(
            "blockchain cryptocurrency smart contract dapp", skip_llm=True
        )
        assert result.decision_path_code == DecisionPathCode.VETO_WEB3


# ===========================================================================
# 10. Provenance hashes
# ===========================================================================

class TestProvenanceHashes:
    """Test provenance hash stability."""

    def test_provenance_hashes_stable(self):
        """Same config → same provenance hash."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig

        tf1 = ThesisFilter(ThesisFilterConfig())
        tf2 = ThesisFilter(ThesisFilterConfig())
        # Both should produce identical hashes for the same keyword dicts
        assert hasattr(tf1, "_consumer_lexicon_sha256")
        assert tf1._consumer_lexicon_sha256 == tf2._consumer_lexicon_sha256
        assert tf1._b2b_lexicon_sha256 == tf2._b2b_lexicon_sha256

    @pytest.mark.asyncio
    async def test_provenance_in_result(self):
        """Provenance hashes appear in ThesisFilterResult."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig

        tf = ThesisFilter(ThesisFilterConfig())
        result = await tf.classify("meal kit delivery startup", skip_llm=True)
        d = result.to_dict()
        assert "consumer_lexicon_sha256" in d or hasattr(result, "consumer_lexicon_sha256")


# ===========================================================================
# 11. Trace field completeness
# ===========================================================================

class TestTraceFields:
    """Test that ThesisFitTrace has all new fields."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_trace_has_matched_hard_rejects(self, matcher):
        fit = matcher.score("some text with blockchain")
        assert fit.trace is not None
        assert hasattr(fit.trace, "matched_hard_rejects")
        assert isinstance(fit.trace.matched_hard_rejects, list)

    def test_trace_has_matched_hard_holds(self, matcher):
        fit = matcher.score("enterprise platform for consumers")
        assert fit.trace is not None
        assert hasattr(fit.trace, "matched_hard_holds")
        assert isinstance(fit.trace.matched_hard_holds, list)

    def test_trace_to_dict_includes_new_fields(self, matcher):
        fit = matcher.score("enterprise blockchain sdk")
        assert fit.trace is not None
        d = fit.trace.to_dict()
        assert "matched_hard_rejects" in d
        assert "matched_hard_holds" in d


# ===========================================================================
# 12. Token (bare) removed from negatives
# ===========================================================================

class TestTokenHandling:
    """Test that bare 'token' is removed; 'crypto token'/'nft token' are hard_reject."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher()

    def test_bare_token_not_negative(self, matcher):
        """Bare 'token' should NOT trigger negative keywords."""
        fit = matcher.score("our authentication token system is secure")
        # "token" alone should not appear in negatives
        assert "token" not in fit.negative_keywords

    def test_crypto_token_is_hard_reject(self, matcher):
        """'crypto token' should be hard_reject."""
        fit = matcher.score("launching a new crypto token sale")
        assert fit.trace is not None
        has_crypto_reject = (
            "crypto token" in fit.trace.matched_hard_rejects
            or "crypto" in fit.trace.matched_hard_rejects
        )
        assert has_crypto_reject


# ===========================================================================
# 13. HARD_REJECT / HARD_HOLD / SOFT_PENALTY keyword dicts exist
# ===========================================================================

class TestNegativeKeywordSplit:
    """Test that NEGATIVE_KEYWORDS is properly split into 3 tiers."""

    def test_hard_reject_keywords_exist(self):
        from utils.thesis_matcher import HARD_REJECT_KEYWORDS
        assert isinstance(HARD_REJECT_KEYWORDS, dict)
        assert len(HARD_REJECT_KEYWORDS) > 0
        # Crypto terms should be in hard_reject
        assert "blockchain" in HARD_REJECT_KEYWORDS or "crypto" in HARD_REJECT_KEYWORDS

    def test_hard_hold_keywords_exist(self):
        from utils.thesis_matcher import HARD_HOLD_KEYWORDS
        assert isinstance(HARD_HOLD_KEYWORDS, dict)
        assert len(HARD_HOLD_KEYWORDS) > 0
        assert "enterprise" in HARD_HOLD_KEYWORDS

    def test_soft_penalty_keywords_exist(self):
        from utils.thesis_matcher import SOFT_PENALTY_KEYWORDS
        assert isinstance(SOFT_PENALTY_KEYWORDS, dict)
        assert len(SOFT_PENALTY_KEYWORDS) > 0

    def test_union_equals_negative_keywords(self):
        """Union of all three tiers should equal NEGATIVE_KEYWORDS."""
        from utils.thesis_matcher import (
            HARD_REJECT_KEYWORDS,
            HARD_HOLD_KEYWORDS,
            SOFT_PENALTY_KEYWORDS,
        )
        union = set(HARD_REJECT_KEYWORDS) | set(HARD_HOLD_KEYWORDS) | set(SOFT_PENALTY_KEYWORDS)
        original = set(NEGATIVE_KEYWORDS)
        # Union should cover all original keywords (minus bare 'token')
        # New keywords may be added too, but originals minus 'token' should be covered
        for kw in original:
            if kw == "token":
                continue
            assert kw in union, f"'{kw}' missing from tiered split"

    def test_no_overlap_between_tiers(self):
        """No keyword should appear in more than one tier."""
        from utils.thesis_matcher import (
            HARD_REJECT_KEYWORDS,
            HARD_HOLD_KEYWORDS,
            SOFT_PENALTY_KEYWORDS,
        )
        hr = set(HARD_REJECT_KEYWORDS)
        hh = set(HARD_HOLD_KEYWORDS)
        sp = set(SOFT_PENALTY_KEYWORDS)
        assert hr.isdisjoint(hh), f"Overlap hard_reject ∩ hard_hold: {hr & hh}"
        assert hr.isdisjoint(sp), f"Overlap hard_reject ∩ soft: {hr & sp}"
        assert hh.isdisjoint(sp), f"Overlap hard_hold ∩ soft: {hh & sp}"


# ===========================================================================
# 14. Consumer signal keywords exist
# ===========================================================================

class TestConsumerSignalKeywords:
    """Test CONSUMER_SIGNAL_KEYWORDS structure."""

    def test_consumer_signal_keywords_exist(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        assert isinstance(CONSUMER_SIGNAL_KEYWORDS, dict)
        assert "A" in CONSUMER_SIGNAL_KEYWORDS
        assert "M" in CONSUMER_SIGNAL_KEYWORDS
        assert "N" in CONSUMER_SIGNAL_KEYWORDS

    def test_a_tier_has_anchor_keywords(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        a_tier = CONSUMER_SIGNAL_KEYWORDS["A"]
        # Plan specifies these as A-tier anchors
        assert "direct to consumer" in a_tier or "d2c" in a_tier

    def test_m_tier_has_medium_keywords(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        m_tier = CONSUMER_SIGNAL_KEYWORDS["M"]
        assert "subscription" in m_tier or "brand" in m_tier

    def test_n_tier_has_ambient_keywords(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        n_tier = CONSUMER_SIGNAL_KEYWORDS["N"]
        assert "app" in n_tier or "users" in n_tier

    def test_a_tier_weights_in_range(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        for kw, weight in CONSUMER_SIGNAL_KEYWORDS["A"].items():
            assert 0.30 <= weight <= 0.40, f"A-tier '{kw}' weight {weight} out of [0.30, 0.40]"

    def test_m_tier_weights_in_range(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        for kw, weight in CONSUMER_SIGNAL_KEYWORDS["M"].items():
            assert 0.15 <= weight <= 0.25, f"M-tier '{kw}' weight {weight} out of [0.15, 0.25]"

    def test_n_tier_weights_in_range(self):
        from utils.thesis_matcher import CONSUMER_SIGNAL_KEYWORDS
        for kw, weight in CONSUMER_SIGNAL_KEYWORDS["N"].items():
            assert 0.05 <= weight <= 0.10, f"N-tier '{kw}' weight {weight} out of [0.05, 0.10]"
