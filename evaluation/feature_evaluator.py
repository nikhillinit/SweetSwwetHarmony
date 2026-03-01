"""Reusable promotion rubric for shadow features."""

from __future__ import annotations

from enum import Enum


class PromotionDecision(str, Enum):
    """Promotion outcomes for a shadow feature."""

    PROMOTE = "promote"
    EXTEND_SHADOW = "extend_shadow"
    KILL = "kill"
    INSUFFICIENT_DATA = "insufficient_data"


def evaluate_shadow_feature(
    feature_name: str,
    shadow_window_start: str,
    shadow_window_end: str,
    coverage_delta: float,
    convergence_impact: float,
    fp_rate_delta: float,
    stability_cv: float,
    latency_p95_ms: float,
    api_calls_per_cycle: int,
    n_entities_evaluated: int = 0,
    n_time_slices: int = 0,
    latency_slo_ms: float = 500.0,
) -> dict:
    """Evaluate a feature and return a structured recommendation.

    Decision logic (ordered):
      1. Data sufficiency gate: n_entities < 10 or n_time_slices < 3
      2. Negative impact: coverage_delta < -0.01 or convergence_impact < -0.01
      3. Signal + stability + budget + regression checks
    """
    # 1. Data sufficiency gate
    if n_entities_evaluated < 10 or n_time_slices < 3:
        return _result(
            feature_name, shadow_window_start, shadow_window_end,
            coverage_delta, convergence_impact, fp_rate_delta,
            stability_cv, latency_p95_ms, api_calls_per_cycle,
            n_entities_evaluated, n_time_slices,
            PromotionDecision.INSUFFICIENT_DATA,
            "insufficient data for evaluation",
        )

    # 2. Negative impact check
    if coverage_delta < -0.01 or convergence_impact < -0.01:
        return _result(
            feature_name, shadow_window_start, shadow_window_end,
            coverage_delta, convergence_impact, fp_rate_delta,
            stability_cv, latency_p95_ms, api_calls_per_cycle,
            n_entities_evaluated, n_time_slices,
            PromotionDecision.EXTEND_SHADOW,
            "negative impact detected — investigate before promoting",
        )

    # 3. Promotion criteria
    has_signal = coverage_delta > 0.01 or convergence_impact > 0.01
    is_stable = stability_cv < 0.5
    within_budget = latency_p95_ms <= latency_slo_ms
    no_regression = fp_rate_delta <= 0.05

    if has_signal and is_stable and within_budget and no_regression:
        recommendation = PromotionDecision.PROMOTE
        reason = "all criteria met"
    elif not has_signal and is_stable:
        recommendation = PromotionDecision.KILL
        reason = "no measurable signal"
    else:
        recommendation = PromotionDecision.EXTEND_SHADOW
        failed = []
        if not is_stable:
            failed.append("stability_cv >= 0.5")
        if not within_budget:
            failed.append(f"latency {latency_p95_ms}ms > SLO {latency_slo_ms}ms")
        if not no_regression:
            failed.append(f"fp_rate_delta {fp_rate_delta} > 0.05")
        reason = "criteria not met: " + "; ".join(failed)

    return _result(
        feature_name, shadow_window_start, shadow_window_end,
        coverage_delta, convergence_impact, fp_rate_delta,
        stability_cv, latency_p95_ms, api_calls_per_cycle,
        n_entities_evaluated, n_time_slices,
        recommendation, reason,
    )


def _result(
    feature_name, start, end,
    coverage_delta, convergence_impact, fp_rate_delta,
    stability_cv, latency_p95_ms, api_calls_per_cycle,
    n_entities_evaluated, n_time_slices,
    recommendation, reason,
):
    return {
        "feature_name": feature_name,
        "window": (start, end),
        "coverage_delta": coverage_delta,
        "convergence_impact": convergence_impact,
        "fp_rate_delta": fp_rate_delta,
        "stability_cv": stability_cv,
        "latency_p95_ms": latency_p95_ms,
        "api_calls_per_cycle": api_calls_per_cycle,
        "recommendation": recommendation.value,
        "decision_reason": reason,
        "n_entities_evaluated": n_entities_evaluated,
        "n_time_slices": n_time_slices,
    }
