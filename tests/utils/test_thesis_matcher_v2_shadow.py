"""Tests for ThesisMatcher v2 shadow mode (Phase 0B-2)."""

import pytest


class TestThesisFitTraceV2Shadow:
    """Test v2_shadow field on ThesisFitTrace."""

    def test_v2_shadow_field_exists_and_defaults_to_none(self):
        """ThesisFitTrace should have v2_shadow field defaulting to None."""
        from utils.thesis_matcher import ThesisFitTrace

        trace = ThesisFitTrace()
        assert hasattr(trace, "v2_shadow")
        assert trace.v2_shadow is None

    def test_v2_shadow_included_in_to_dict_when_present(self):
        """to_dict() should include v2_shadow when it has a value."""
        from utils.thesis_matcher import ThesisFitTrace

        shadow_diff = {
            "v1": {"score": 0.5},
            "v2": {"score": 0.45},
            "delta_score": -0.05,
        }
        trace = ThesisFitTrace(v2_shadow=shadow_diff)
        result = trace.to_dict()

        assert "v2_shadow" in result
        assert result["v2_shadow"] == shadow_diff

    def test_v2_shadow_excluded_from_to_dict_when_none(self):
        """to_dict() should exclude v2_shadow when None."""
        from utils.thesis_matcher import ThesisFitTrace

        trace = ThesisFitTrace()
        result = trace.to_dict()

        assert "v2_shadow" not in result


class TestInternalDataclasses:
    """Test internal dataclasses for scoring decomposition."""

    def test_core_score_is_frozen(self):
        """_CoreScore should be immutable (frozen)."""
        from utils.thesis_matcher import _CoreScore, ConsumerThesis

        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": ["food"]},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=[],
            domain_match=False,
        )

        with pytest.raises(AttributeError):
            core.base_score = 0.9

    def test_penalty_result_is_frozen(self):
        """_PenaltyResult should be immutable (frozen)."""
        from utils.thesis_matcher import _PenaltyResult

        penalty = _PenaltyResult(
            matches=["enterprise"],
            raw_penalty=0.5,
            applied_penalty=0.25,
        )

        with pytest.raises(AttributeError):
            penalty.raw_penalty = 0.9

    def test_core_score_fields(self):
        """_CoreScore should have all required fields."""
        from utils.thesis_matcher import _CoreScore, ConsumerThesis

        core = _CoreScore(
            normalized="test text",
            scores={"consumer_cpg": 0.5, "consumer_health_tech": 0.3},
            all_matches={"consumer_cpg": ["food"], "consumer_health_tech": []},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=["join waitlist"],
            domain_match=True,
        )

        assert core.normalized == "test text"
        assert core.best_thesis == ConsumerThesis.CONSUMER_CPG
        assert core.base_score == 0.5
        assert core.domain_match is True

    def test_penalty_result_fields(self):
        """_PenaltyResult should have all required fields."""
        from utils.thesis_matcher import _PenaltyResult

        penalty = _PenaltyResult(
            matches=["enterprise", "b2b"],
            raw_penalty=1.0,
            applied_penalty=0.5,
        )

        assert penalty.matches == ["enterprise", "b2b"]
        assert penalty.raw_penalty == 1.0
        assert penalty.applied_penalty == 0.5


class TestComputeCore:
    """Test _compute_core() helper extraction."""

    def test_compute_core_returns_core_score(self):
        """_compute_core() should return a _CoreScore dataclass."""
        from utils.thesis_matcher import ThesisMatcher, _CoreScore

        matcher = ThesisMatcher(v2_enablement="disabled")
        normalized = "healthy meal kits delivered"
        core = matcher._compute_core(normalized, domain_name=None)

        assert isinstance(core, _CoreScore)
        assert core.normalized == normalized

    def test_compute_core_scores_all_theses(self):
        """_compute_core() should score all thesis categories."""
        from utils.thesis_matcher import ThesisMatcher, ConsumerThesis

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("healthy meal kits delivered", domain_name=None)

        # Should have scores for all theses
        assert "consumer_cpg" in core.scores
        assert "consumer_health_tech" in core.scores
        assert "travel_hospitality" in core.scores
        assert "consumer_marketplace" in core.scores

    def test_compute_core_finds_best_thesis(self):
        """_compute_core() should identify best matching thesis."""
        from utils.thesis_matcher import ThesisMatcher, ConsumerThesis

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("fitness app wellness workout", domain_name=None)

        assert core.best_thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert core.base_score > 0

    def test_compute_core_detects_intent_phrases(self):
        """_compute_core() should detect intent phrases."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("join waitlist for our app", domain_name=None)

        assert "join waitlist" in core.intent_matches

    def test_compute_core_detects_domain_patterns(self):
        """_compute_core() should detect consumer domain patterns."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("some text", domain_name="getfitness.com")

        assert core.domain_match is True

    def test_compute_core_no_domain_match_for_non_consumer_domain(self):
        """_compute_core() should not match non-consumer domains."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("some text", domain_name="example.com")

        assert core.domain_match is False


class TestFindNegativeKeywordsRefactor:
    """Test _find_negative_keywords() with keyword-only vocab parameter."""

    def test_default_path_equals_explicit_v1_vocab(self):
        """Default path should equal explicit NEGATIVE_KEYWORDS vocab."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise api platform logistics"

        default_result = matcher._find_negative_keywords(text)
        explicit_result = matcher._find_negative_keywords(text, negative_vocab=NEGATIVE_KEYWORDS)

        assert default_result == explicit_result

    def test_custom_vocab_limits_matches(self):
        """Custom vocab should limit which keywords are matched."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise crypto blockchain"

        # Full vocab would match all three
        full_result = matcher._find_negative_keywords(text)
        assert "enterprise" in full_result
        assert "crypto" in full_result

        # Subset vocab only matches what's in the subset
        subset = {"enterprise": 0.5}
        subset_result = matcher._find_negative_keywords(text, negative_vocab=subset)
        assert subset_result == ["enterprise"]

    def test_passing_string_raises_type_error(self):
        """Passing a single string instead of iterable should raise TypeError."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise platform"

        with pytest.raises(TypeError, match="iterable of keywords"):
            matcher._find_negative_keywords(text, negative_vocab="enterprise")

    def test_vocab_can_be_dict_keys(self):
        """Vocab can be a dict (iterates keys)."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise b2b platform"

        vocab_dict = {"enterprise": 0.5, "b2b": 0.5}
        result = matcher._find_negative_keywords(text, negative_vocab=vocab_dict)

        assert "enterprise" in result
        assert "b2b" in result
        assert len(result) == 2

    def test_vocab_can_be_list(self):
        """Vocab can be a list of keywords."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise b2b platform"

        vocab_list = ["enterprise"]
        result = matcher._find_negative_keywords(text, negative_vocab=vocab_list)

        assert result == ["enterprise"]
