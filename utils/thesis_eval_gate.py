"""Utilities for producing a prompt/schema go/no-go artifact from thesis evals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from utils.thesis_evaluator import EvaluationComparison

DEFAULT_LLM_ACCURACY_THRESHOLD = 0.90


def build_eval_gate_artifact(
    comparison: EvaluationComparison,
    *,
    threshold: float = DEFAULT_LLM_ACCURACY_THRESHOLD,
    proposed_changes: Sequence[str] | None = None,
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

    return {
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


def write_eval_gate_artifact(path: str | Path, artifact: Dict[str, Any]) -> Path:
    """Write an eval-gate artifact as formatted JSON."""
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
