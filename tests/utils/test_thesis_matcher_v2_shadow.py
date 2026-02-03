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
