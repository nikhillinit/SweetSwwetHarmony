"""Tests for the thesis LLM eval-gate helper."""

from __future__ import annotations

from utils.thesis_eval_gate import build_eval_gate_artifact
from utils.thesis_evaluator import ClassMetrics, EvaluationComparison, ThesisEvaluationResult


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
    )

    assert artifact["decision"] == "no_go"
    assert artifact["authorized_changes"] == []
    assert artifact["deferred_changes"] == ["add structured decomposition fields"]
    assert artifact["blocked_reasons"]


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
    )

    assert artifact["decision"] == "go"
    assert artifact["authorized_changes"] == ["add structured decomposition fields"]
    assert artifact["blocked_reasons"] == []


def test_eval_gate_blocks_on_llm_execution_errors():
    llm_result = _result(evaluator_type="llm", accuracy=0.97)
    llm_result.errors = ["Sample x: GOOGLE_API_KEY not set"]
    comparison = EvaluationComparison(
        keyword_result=_result(evaluator_type="keyword", accuracy=0.8),
        llm_result=llm_result,
        accuracy_delta=0.17,
        per_class_deltas={},
    )

    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=["add structured decomposition fields"],
    )

    assert artifact["decision"] == "no_go"
    assert artifact["authorized_changes"] == []
    assert artifact["blocked_reasons"]
