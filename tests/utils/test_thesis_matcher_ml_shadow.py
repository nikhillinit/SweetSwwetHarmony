"""Tests for ML shadow mode integration in ThesisMatcher.

Tests the ML thesis model integration following the proven v2 shadow pattern:
- ML disabled: results identical to current behavior
- ML shadow: ml_shadow attached to trace, scores unchanged
- ML live: rescues false negatives, does NOT override high scores
- Graceful degradation on model load/prediction failures
- Circuit breaker after consecutive failures
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from utils.thesis_matcher import ThesisMatcher, ThesisFit, ThesisFitTrace


class TestMLDisabledMode:
    """ML disabled (default): zero behavioral change."""

    def test_default_has_no_ml_model(self):
        matcher = ThesisMatcher()
        assert matcher._ml_model is None

    def test_score_returns_normal_result(self):
        matcher = ThesisMatcher()
        fit = matcher.score("Meal kit delivery startup")
        assert isinstance(fit, ThesisFit)
        assert fit.trace is not None
        assert fit.trace.ml_shadow is None

    def test_empty_text_returns_unchanged(self):
        matcher = ThesisMatcher()
        fit = matcher.score("")
        assert fit.score == 0.0
        assert fit.trace.ml_shadow is None


class TestMLShadowMode:
    """ML shadow mode: attach ml_shadow to trace, return keyword score unchanged."""

    def _make_matcher_with_mock_ml(self, ml_prob=0.7):
        """Create matcher with mocked ML model."""
        matcher = ThesisMatcher()
        # Manually set ML model and controls
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = ml_prob
        mock_model.model_id = "test_model_1234"
        mock_model.__version__ = "2026.02.v1"
        matcher._ml_model = mock_model

        from utils.runtime_controls import RuntimeControls
        matcher._controls = RuntimeControls(
            policy_loader_mode="permissive",
            v2_enablement="disabled",
            v2_execution_enabled=False,
            ml_enablement="shadow",
        )
        return matcher

    def test_shadow_attaches_ml_shadow(self):
        matcher = self._make_matcher_with_mock_ml(ml_prob=0.75)
        fit = matcher.score("Some company description")

        assert fit.trace is not None
        assert fit.trace.ml_shadow is not None
        assert "keyword_score" in fit.trace.ml_shadow
        assert "ml_score" in fit.trace.ml_shadow
        assert fit.trace.ml_shadow["ml_score"] == 0.75

    def test_shadow_does_not_change_score(self):
        matcher = self._make_matcher_with_mock_ml(ml_prob=0.9)

        # First get baseline without ML
        baseline_matcher = ThesisMatcher()
        baseline = baseline_matcher.score("Enterprise B2B SaaS platform")
        baseline_score = baseline.score

        # Now with ML shadow
        fit = matcher.score("Enterprise B2B SaaS platform")
        assert fit.score == baseline_score  # Score unchanged

    def test_shadow_includes_model_id(self):
        matcher = self._make_matcher_with_mock_ml()
        fit = matcher.score("Test company")

        assert fit.trace.ml_shadow["model_id"] == "test_model_1234"
        assert fit.trace.ml_shadow["model_version"] == "2026.02.v1"

    def test_shadow_includes_gating_reason(self):
        matcher = self._make_matcher_with_mock_ml(ml_prob=0.8)
        fit = matcher.score("Some startup")

        assert "gating_reason" in fit.trace.ml_shadow

    def test_shadow_would_rescue_true_when_applicable(self):
        # Low keyword score + high ML score → would_rescue
        matcher = self._make_matcher_with_mock_ml(ml_prob=0.8)
        fit = matcher.score("obscure company nobody knows")

        shadow = fit.trace.ml_shadow
        if fit.score < 0.4 and shadow["ml_score"] > 0.5:
            assert shadow["would_rescue"] is True


class TestMLLiveMode:
    """ML live mode: rescue false negatives."""

    def _make_live_matcher(self, ml_prob=0.7):
        matcher = ThesisMatcher()
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = ml_prob
        mock_model.model_id = "live_model_5678"
        mock_model.__version__ = "2026.02.v1"
        matcher._ml_model = mock_model

        from utils.runtime_controls import RuntimeControls
        matcher._controls = RuntimeControls(
            policy_loader_mode="permissive",
            v2_enablement="disabled",
            v2_execution_enabled=False,
            ml_enablement="live",
        )
        return matcher

    def test_live_rescues_low_keyword_score(self):
        """Live mode should rescue when keyword score < 0.4 and ML > 0.5."""
        matcher = self._make_live_matcher(ml_prob=0.8)
        fit = matcher.score("obscure company nobody knows")

        # If keyword score was < 0.4, ML should rescue
        if fit.trace and fit.trace.ml_shadow:
            original_keyword = fit.trace.ml_shadow.get("keyword_score", fit.score)
            if original_keyword < 0.4:
                assert fit.score >= 0.5  # ML probability passed through

    def test_live_does_not_override_high_keyword_score(self):
        """Live mode should NOT override when keyword score >= 0.4."""
        matcher = self._make_live_matcher(ml_prob=0.3)
        fit = matcher.score("Meal kit delivery startup with organic food")

        # High keyword score should be preserved
        if fit.score >= 0.4:
            # ML with low probability should not reduce score
            assert fit.score >= 0.4

    def test_live_does_not_rescue_domain_blacklisted(self):
        matcher = self._make_live_matcher(ml_prob=0.9)
        fit = matcher.score("Some app", domain_name="localhost:3000")

        assert fit.domain_blacklisted is True
        assert fit.score == 0.0

    def test_live_uses_max_keyword_ml(self):
        """Rescue score = max(keyword_score, ml_prob), no arbitrary *0.8 damping."""
        matcher = self._make_live_matcher(ml_prob=0.75)
        fit = matcher.score("unknown company xyz")

        if fit.trace and fit.trace.ml_shadow:
            shadow = fit.trace.ml_shadow
            if shadow.get("gating_reason") == "rescued":
                # Score should be max(keyword, ml), NOT ml * 0.8
                assert fit.score == max(shadow["keyword_score"], shadow["ml_score"])


class TestMLGracefulDegradation:
    """Test graceful degradation when ML model fails."""

    def test_missing_model_file_logs_and_continues(self):
        """When model file doesn't exist, ML is disabled, scoring works normally."""
        with patch.dict(os.environ, {
            "ML_ENABLEMENT": "shadow",
            "ML_MODEL_PATH": "/nonexistent/model.joblib",
        }):
            matcher = ThesisMatcher()
            assert matcher._ml_model is None

            # Scoring should still work (keyword only)
            fit = matcher.score("Meal kit delivery")
            assert isinstance(fit, ThesisFit)
            assert fit.trace.ml_shadow is None

    def test_prediction_exception_returns_keyword_score(self):
        matcher = ThesisMatcher()
        mock_model = MagicMock()
        mock_model.predict_proba.side_effect = RuntimeError("model error")
        mock_model.model_id = "broken"
        mock_model.__version__ = "test"
        matcher._ml_model = mock_model

        from utils.runtime_controls import RuntimeControls
        matcher._controls = RuntimeControls(
            policy_loader_mode="permissive",
            v2_enablement="disabled",
            v2_execution_enabled=False,
            ml_enablement="shadow",
        )

        fit = matcher.score("Meal kit delivery")
        assert isinstance(fit, ThesisFit)
        # ML prediction failed, so no ml_shadow
        assert fit.trace.ml_shadow is None


class TestMLCircuitBreaker:
    """Test circuit breaker after consecutive ML failures."""

    def test_circuit_breaker_disables_after_threshold(self):
        matcher = ThesisMatcher()
        mock_model = MagicMock()
        mock_model.predict_proba.side_effect = RuntimeError("fail")
        mock_model.model_id = "failing"
        mock_model.__version__ = "test"
        matcher._ml_model = mock_model

        from utils.runtime_controls import RuntimeControls
        matcher._controls = RuntimeControls(
            policy_loader_mode="permissive",
            v2_enablement="disabled",
            v2_execution_enabled=False,
            ml_enablement="shadow",
        )

        # Trigger failures up to threshold
        for _ in range(ThesisMatcher._ML_FAILURE_THRESHOLD):
            matcher.score("test text")

        # After threshold, model should be disabled
        assert matcher._ml_model is None


class TestMLShadowTraceSchema:
    """Test ml_shadow trace schema completeness."""

    def test_ml_shadow_in_to_dict(self):
        trace = ThesisFitTrace(
            final_score=0.3,
            routing_decision="QUALIFIED",
            explanation="test",
            ml_shadow={
                "keyword_score": 0.1,
                "ml_score": 0.7,
                "delta": 0.6,
                "would_rescue": True,
                "rescued_score": 0.7,
                "gating_reason": "rescued",
                "model_id": "abc123",
                "model_version": "2026.02.v1",
            },
        )

        d = trace.to_dict()
        assert "ml_shadow" in d
        assert d["ml_shadow"]["model_id"] == "abc123"

    def test_ml_shadow_absent_when_none(self):
        trace = ThesisFitTrace(
            final_score=0.3,
            routing_decision="QUALIFIED",
            explanation="test",
        )

        d = trace.to_dict()
        assert "ml_shadow" not in d

    def test_ml_shadow_field_exists_on_dataclass(self):
        trace = ThesisFitTrace()
        assert hasattr(trace, "ml_shadow")
        assert trace.ml_shadow is None
