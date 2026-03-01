"""Tests for the shadow feature promotion rubric."""

from evaluation.feature_evaluator import PromotionDecision, evaluate_shadow_feature


_BASE = dict(
    feature_name="test_feature",
    shadow_window_start="2026-02-01",
    shadow_window_end="2026-02-14",
    n_entities_evaluated=50,
    n_time_slices=7,
)


def test_promote_on_good_signal():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=0.05,
        convergence_impact=0.02,
        fp_rate_delta=-0.01,
        stability_cv=0.2,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.PROMOTE


def test_kill_on_no_signal():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=0.0,
        convergence_impact=0.0,
        fp_rate_delta=0.0,
        stability_cv=0.2,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.KILL


def test_extend_on_unstable():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=0.04,
        convergence_impact=0.02,
        fp_rate_delta=0.0,
        stability_cv=0.7,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.EXTEND_SHADOW


def test_extend_on_budget_breach():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=0.04,
        convergence_impact=0.02,
        fp_rate_delta=0.0,
        stability_cv=0.2,
        latency_p95_ms=600.0,
        api_calls_per_cycle=50,
    )
    assert result["recommendation"] == PromotionDecision.EXTEND_SHADOW


def test_extend_on_fp_regression():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=0.04,
        convergence_impact=0.02,
        fp_rate_delta=0.07,
        stability_cv=0.2,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.EXTEND_SHADOW


def test_insufficient_data_low_entities():
    result = evaluate_shadow_feature(
        **{**_BASE, "n_entities_evaluated": 5},
        coverage_delta=0.05,
        convergence_impact=0.02,
        fp_rate_delta=0.0,
        stability_cv=0.2,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.INSUFFICIENT_DATA


def test_insufficient_data_low_slices():
    result = evaluate_shadow_feature(
        **{**_BASE, "n_time_slices": 2},
        coverage_delta=0.05,
        convergence_impact=0.02,
        fp_rate_delta=0.0,
        stability_cv=0.2,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.INSUFFICIENT_DATA


def test_negative_impact_detected():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=-0.05,
        convergence_impact=0.02,
        fp_rate_delta=0.0,
        stability_cv=0.2,
        latency_p95_ms=120.0,
        api_calls_per_cycle=5,
    )
    assert result["recommendation"] == PromotionDecision.EXTEND_SHADOW
    assert "negative impact" in result["decision_reason"]


def test_result_contains_all_fields():
    result = evaluate_shadow_feature(
        **_BASE,
        coverage_delta=0.02,
        convergence_impact=0.0,
        fp_rate_delta=0.0,
        stability_cv=0.2,
        latency_p95_ms=100.0,
        api_calls_per_cycle=1,
    )
    expected = {
        "feature_name",
        "window",
        "coverage_delta",
        "convergence_impact",
        "fp_rate_delta",
        "stability_cv",
        "latency_p95_ms",
        "api_calls_per_cycle",
        "recommendation",
        "decision_reason",
        "n_entities_evaluated",
        "n_time_slices",
    }
    assert expected.issubset(result.keys())
