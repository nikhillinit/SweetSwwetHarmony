"""Utilities for producing a prompt/schema go/no-go artifact from thesis evals."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from utils.thesis_evaluator import EvaluationComparison

DEFAULT_LLM_ACCURACY_THRESHOLD = 0.90


def build_eval_gate_artifact(
    comparison: EvaluationComparison,
    *,
    threshold: float = DEFAULT_LLM_ACCURACY_THRESHOLD,
    proposed_changes: Sequence[str] | None = None,
    benchmark_provenance: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a concrete prompt/schema rollout decision artifact."""
    proposed_changes = list(proposed_changes or [])
    blocked_reasons: List[str] = []
    authorized_changes: List[str] = []
    narrowed_changes = [
        "Keep operational truthfulness/routing fixes outside the prompt/schema gate.",
        "Limit prompt/schema rollout to changes directly supported by the LLM-focused golden set.",
    ]

    llm_result = comparison.llm_result
    if llm_result is None:
        blocked_reasons.append(
            "LLM evaluation did not run successfully; keep prompt/schema expansion blocked."
        )
    else:
        if llm_result.errors:
            blocked_reasons.append(
                f"LLM evaluation reported {len(llm_result.errors)} execution errors; "
                "treat the gate as blocked until the evaluation environment is healthy."
            )
        elif llm_result.accuracy < threshold:
            blocked_reasons.append(
                f"LLM accuracy {llm_result.accuracy:.1%} is below the {threshold:.0%} gate."
            )
        if (
            not llm_result.errors
            and comparison.accuracy_delta is not None
            and comparison.accuracy_delta < 0
        ):
            blocked_reasons.append(
                f"LLM underperformed the keyword baseline by {comparison.accuracy_delta:.1%}."
            )

    if not blocked_reasons:
        authorized_changes = list(proposed_changes)

    artifact = {
        "decision": "go" if not blocked_reasons else "no_go",
        "threshold": threshold,
        "keyword_accuracy": comparison.keyword_result.accuracy,
        "llm_accuracy": llm_result.accuracy if llm_result else None,
        "accuracy_delta": comparison.accuracy_delta,
        "authorized_changes": authorized_changes,
        "narrowed_changes": narrowed_changes,
        "deferred_changes": [] if authorized_changes else list(proposed_changes),
        "blocked_reasons": blocked_reasons,
    }
    if benchmark_provenance:
        artifact.update({
            "benchmark_id": benchmark_provenance["benchmark_id"],
            "benchmark_version": benchmark_provenance["benchmark_version"],
            "benchmark_fingerprint": benchmark_provenance["benchmark_fingerprint"],
            "benchmark_manifest_path": benchmark_provenance["benchmark_manifest_path"],
        })
    return artifact


def _slice_accuracy(records: Sequence[Dict[str, Any]]) -> float | None:
    if not records:
        return None
    return sum(1 for record in records if record["match"]) / len(records)


def _scenario_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["scenario"]].append(record)

    return {
        scenario: {
            "accuracy": round(_slice_accuracy(group_records) or 0.0, 4),
            "support": len(group_records),
        }
        for scenario, group_records in sorted(grouped.items())
    }


def build_rebaseline_artifact(
    comparison: EvaluationComparison,
    *,
    benchmark_provenance: Dict[str, Any],
    llm_records: Sequence[Dict[str, Any]],
    previous_summary: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the benchmark re-baseline artifact for threshold review."""
    llm_result = comparison.llm_result
    ambiguous_scenarios = set(benchmark_provenance.get("ambiguous_scenarios", []))
    ambiguous_records = [
        record for record in llm_records
        if record.get("scenario") in ambiguous_scenarios
    ]
    clear_control_records = [
        record for record in llm_records
        if record.get("scenario") in {"clear_consumer", "clear_b2b"}
    ]
    scenario_metrics = _scenario_metrics(llm_records)
    ambiguous_accuracy = _slice_accuracy(ambiguous_records)
    clear_control_miss_count = sum(1 for record in clear_control_records if not record["match"])
    per_class_metrics = (
        {label: metrics.to_dict() for label, metrics in llm_result.per_class_metrics.items()}
        if llm_result is not None
        else {}
    )

    recommendation: str | None = None
    justification: list[str] = []
    if llm_result is None:
        justification.append("LLM evaluation did not run; threshold recommendation is unavailable.")
    else:
        scenario_floor_scores = [
            metrics["accuracy"]
            for scenario, metrics in scenario_metrics.items()
            if scenario in ambiguous_scenarios and metrics["support"] >= 6
        ]
        overall_accuracy = llm_result.accuracy
        ambiguous_score = ambiguous_accuracy or 0.0

        if (
            overall_accuracy >= 0.97
            and ambiguous_score >= 0.95
            and all(score >= 0.90 for score in scenario_floor_scores)
        ):
            recommendation = "raise_threshold"
            justification.append(
                "Expanded benchmark still scores near-perfectly across the ambiguous slice."
            )
        elif (
            0.85 <= overall_accuracy <= 0.89
            and ambiguous_score >= 0.80
            and all(score >= 0.67 for score in scenario_floor_scores)
            and clear_control_miss_count == 0
        ):
            recommendation = "lower_threshold"
            justification.append(
                "Misses are concentrated outside the clear-control slice while ambiguous support remains acceptable."
            )
        else:
            recommendation = "keep_0_90"
            justification.append(
                "Default bias is to keep 0.90 unless the expanded benchmark clearly justifies a change."
            )

    return {
        "benchmark_id": benchmark_provenance["benchmark_id"],
        "benchmark_version": benchmark_provenance["benchmark_version"],
        "benchmark_fingerprint": benchmark_provenance["benchmark_fingerprint"],
        "benchmark_manifest_path": benchmark_provenance["benchmark_manifest_path"],
        "pre_expansion_sample_count": (
            previous_summary.get("total_samples") if previous_summary else None
        ),
        "post_expansion_sample_count": benchmark_provenance["benchmark_sample_count"],
        "scenario_counts": benchmark_provenance["scenario_counts"],
        "keyword_accuracy": comparison.keyword_result.accuracy,
        "overall_llm_accuracy": llm_result.accuracy if llm_result else None,
        "ambiguous_slice_accuracy": round(ambiguous_accuracy, 4) if ambiguous_accuracy is not None else None,
        "clear_control_miss_count": clear_control_miss_count,
        "per_class_metrics": per_class_metrics,
        "per_scenario_metrics": scenario_metrics,
        "recommendation": recommendation,
        "justification": justification,
    }


def write_eval_gate_artifact(path: str | Path, artifact: Dict[str, Any]) -> Path:
    """Write an eval-gate artifact as formatted JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def write_rebaseline_artifact(path: str | Path, artifact: Dict[str, Any]) -> Path:
    """Write a re-baseline artifact as formatted JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def default_proposed_changes() -> Iterable[str]:
    """Default prompt/schema changes gated by the LLM-focused evaluation pass."""
    return (
        "Add B2B-in-disguise prompt guidance for sells-tools-to-industry vs operates-in-industry.",
        "Add only the minimum structured decomposition fields proven useful by the eval gate.",
        "Persist new prompt/schema fields only when they improve routing/reporting evidence.",
    )
