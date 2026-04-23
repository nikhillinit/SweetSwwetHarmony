"""Tests for the thesis LLM eval-gate helper."""

from __future__ import annotations

from utils.thesis_eval_gate import build_eval_gate_artifact, build_rebaseline_artifact
from utils.thesis_evaluator import ClassMetrics, EvaluationComparison, ThesisEvaluationResult


BENCHMARK_PROVENANCE = {
    "benchmark_id": "thesis_llm_golden_set",
    "benchmark_version": "2026-04-05.v2",
    "benchmark_fingerprint": "fingerprint",
    "benchmark_manifest_path": "tests/fixtures/thesis_llm_golden_set.manifest.json",
    "benchmark_sample_count": 64,
    "ambiguous_scenarios": [
        "b2b_in_disguise",
        "ad_supported",
        "employer_sponsored",
        "two_sided_marketplace",
        "gig_economy",
        "creator_tools",
    ],
    "scenario_counts": {
        "clear_consumer": 10,
        "clear_b2b": 10,
        "b2b_in_disguise": 11,
    },
}


def _result(*, evaluator_type: str, accuracy: float) -> ThesisEvaluationResult:
    return ThesisEvaluationResult(
        run_id=f"{evaluator_type}-run",
        evaluator_type=evaluator_type,
        dataset_path="tests/fixtures/thesis_llm_golden_set.jsonl",
        total_samples=30,
        accuracy=accuracy,
        per_class_metrics={
            "QUALIFIED": ClassMetrics(precision=1.0, recall=1.0, f1=1.0, support=10),
            "HELD": ClassMetrics(precision=1.0, recall=1.0, f1=1.0, support=10),
            "REJECTED": ClassMetrics(precision=1.0, recall=1.0, f1=1.0, support=10),
        },
        confusion_matrix={},
        timestamp="2026-04-04T00:00:00Z",
    )


def test_eval_gate_blocks_without_llm_result():
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.8),
        llm_result=None,
        accuracy_delta=None,
        per_class_deltas={},
    )

    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=["add structured decomposition fields"],
        benchmark_provenance=BENCHMARK_PROVENANCE,
    )

    assert artifact["decision"] == "no_go"
    assert artifact["authorized_changes"] == []
    assert artifact["deferred_changes"] == ["add structured decomposition fields"]
    assert artifact["blocked_reasons"]
    assert artifact["benchmark_id"] == "thesis_llm_golden_set"


def test_eval_gate_authorizes_changes_when_llm_clears_threshold():
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.8),
        llm_result=_result(evaluator_type="llm", accuracy=0.93),
        accuracy_delta=0.13,
        per_class_deltas={},
    )

    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=["add structured decomposition fields"],
        benchmark_provenance=BENCHMARK_PROVENANCE,
    )

    assert artifact["decision"] == "go"
    assert artifact["authorized_changes"] == ["add structured decomposition fields"]
    assert artifact["blocked_reasons"] == []


def test_eval_gate_blocks_on_llm_execution_errors():
    llm_result = _result(evaluator_type="llm", accuracy=0.97)
    llm_result.accuracy = None
    llm_result.errors = ["Sample x: GOOGLE_API_KEY not set"]
    llm_result.run_state = "blocked_execution"
    llm_result.llm_execution_error_count = 30
    llm_result.attempted_sample_count = 1
    llm_result.blocked_reason = (
        "LLM preflight hit Gemini rate limiting/quota before the full benchmark "
        "evaluation; keep the gate blocked until quota recovers or billing changes."
    )
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.8),
        llm_result=llm_result,
        accuracy_delta=0.17,
        per_class_deltas={},
    )

    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=["add structured decomposition fields"],
        benchmark_provenance=BENCHMARK_PROVENANCE,
    )

    assert artifact["decision"] == "no_go"
    assert artifact["authorized_changes"] == []
    assert artifact["blocked_reasons"]
    assert artifact["run_state"] == "blocked_execution"
    assert artifact["llm_execution_error_count"] == 30
    assert artifact["llm_attempted_sample_count"] == 1
    assert artifact["llm_accuracy"] is None
    assert artifact["accuracy_delta"] is None
    assert "rate limiting/quota" in artifact["blocked_reasons"][0].lower()


def test_eval_gate_healthy_run_preserves_quality_fields():
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.8),
        llm_result=_result(evaluator_type="llm", accuracy=0.93),
        accuracy_delta=0.13,
        per_class_deltas={},
    )

    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=["add structured decomposition fields"],
        benchmark_provenance=BENCHMARK_PROVENANCE,
    )

    assert artifact["llm_accuracy"] == 0.93
    assert artifact["accuracy_delta"] == 0.13
    assert "run_state" not in artifact


def test_build_rebaseline_artifact_defaults_to_keep_threshold():
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.4),
        llm_result=_result(evaluator_type="llm", accuracy=0.93),
        accuracy_delta=0.53,
        per_class_deltas={},
    )
    llm_records = [
        {"scenario": "clear_consumer", "match": True},
        {"scenario": "clear_b2b", "match": True},
        {"scenario": "b2b_in_disguise", "match": True},
        {"scenario": "b2b_in_disguise", "match": False},
        {"scenario": "ad_supported", "match": True},
        {"scenario": "employer_sponsored", "match": True},
    ]

    artifact = build_rebaseline_artifact(
        comparison,
        benchmark_provenance=BENCHMARK_PROVENANCE,
        llm_records=llm_records,
        previous_summary={"total_samples": 40},
    )

    assert artifact["pre_expansion_sample_count"] == 40
    assert artifact["post_expansion_sample_count"] == 64
    assert artifact["recommendation"] == "keep_0_90"
    assert artifact["benchmark_id"] == "thesis_llm_golden_set"


def test_build_rebaseline_artifact_marks_blocked_execution():
    llm_result = _result(evaluator_type="llm", accuracy=0.93)
    llm_result.accuracy = None
    llm_result.run_state = "blocked_execution"
    llm_result.llm_execution_error_count = 30
    llm_result.attempted_sample_count = 1
    llm_result.blocked_reason = (
        "LLM preflight hit Gemini rate limiting/quota before the full benchmark "
        "evaluation; keep the gate blocked until quota recovers or billing changes."
    )
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.4),
        llm_result=llm_result,
        accuracy_delta=0.53,
        per_class_deltas={},
    )

    artifact = build_rebaseline_artifact(
        comparison,
        benchmark_provenance=BENCHMARK_PROVENANCE,
        llm_records=[
            {"scenario": "clear_consumer", "match": False},
            {"scenario": "clear_b2b", "match": False},
        ],
    )

    assert artifact["run_state"] == "blocked_execution"
    assert artifact["llm_execution_error_count"] == 30
    assert artifact["llm_attempted_sample_count"] == 1
    assert artifact["overall_llm_accuracy"] is None
    assert artifact["ambiguous_slice_accuracy"] is None
    assert artifact["per_class_metrics"] == {}
    assert artifact["per_scenario_metrics"] == {}
    assert artifact["recommendation"] == "blocked_execution"
    assert artifact["clear_control_miss_count"] is None
    assert "rate limiting/quota" in artifact["justification"][0].lower()
