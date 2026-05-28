from __future__ import annotations

import argparse
import sys
from typing import Any

from integrations.hermes.gate_runners._common import (
    emit,
    load_json,
    resolve_project_path,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed ACH/tribunal gate")
    parser.add_argument("--signal-id", required=False)
    parser.add_argument("--matrix", default=None, help="ACH matrix JSON file")
    parser.add_argument("--min-score", type=float, default=0.6)
    parser.add_argument("--max-differentiators", type=int, default=3)
    args = parser.parse_args(argv)

    matrix = _load_matrix(args.matrix)
    if matrix is None:
        return emit(False, "ACH matrix unavailable; tribunal gate fails closed")

    try:
        top_score, differentiator_count, summary = _summarize(matrix)
    except Exception as exc:
        return emit(False, f"tribunal summary failed: {exc}")

    ok = (
        top_score >= args.min_score and differentiator_count <= args.max_differentiators
    )
    return emit(
        ok,
        "tribunal clean" if ok else "tribunal review required",
        {
            "signalId": args.signal_id,
            "topScore": top_score,
            "differentiatorCount": differentiator_count,
            "minScore": args.min_score,
            "maxDifferentiators": args.max_differentiators,
            "summary": summary,
        },
    )


def _load_matrix(path_text: str | None) -> Any | None:
    if not path_text:
        return None
    path = resolve_project_path(path_text)
    if not path.exists():
        return None
    return load_json(path)


def _summarize(matrix: Any) -> tuple[float, int, dict[str, Any]]:
    if not isinstance(matrix, dict):
        return 0.0, 999, {"matrixType": type(matrix).__name__}

    top_score = _top_score(matrix)
    differentiator_count = _differentiator_count(matrix)
    return top_score, differentiator_count, matrix


def _top_score(matrix: dict[str, Any]) -> float:
    explicit = matrix.get("top_score", matrix.get("topScore", matrix.get("score")))
    if explicit is not None:
        return _float_value(explicit)

    scores = matrix.get("hypothesis_scores") or matrix.get("hypothesisScores")
    if isinstance(scores, dict) and scores:
        return max(_float_value(value) for value in scores.values())
    return 0.0


def _differentiator_count(matrix: dict[str, Any]) -> int:
    explicit = matrix.get("differentiator_count", matrix.get("differentiatorCount"))
    if explicit is not None:
        return _int_value(explicit)

    differentiators = matrix.get("differentiators")
    if isinstance(differentiators, list):
        return len(differentiators)
    return 999


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 999


if __name__ == "__main__":
    sys.exit(main())
