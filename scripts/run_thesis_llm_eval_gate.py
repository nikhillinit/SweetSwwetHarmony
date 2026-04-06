"""Run the thesis LLM evaluation gate and write a go/no-go artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from utils.thesis_benchmark import build_benchmark_provenance
from utils.thesis_eval_gate import (
    build_eval_gate_artifact,
    build_rebaseline_artifact,
    default_proposed_changes,
    write_eval_gate_artifact,
    write_rebaseline_artifact,
)
from utils.thesis_evaluator import EvaluationComparison, ThesisEvaluator, VALID_LABELS


DEFAULT_DATASET = Path("tests/fixtures/thesis_llm_golden_set.jsonl")
DEFAULT_OUTPUT = Path(".omx/specs/thesis-llm-eval-gate.json")
DEFAULT_REBASELINE_OUTPUT = Path(".omx/specs/thesis-llm-benchmark-rebaseline.json")


def _load_project_env() -> None:
    """Best-effort load of the repo's .env file, matching project entrypoints."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv()


def _resolve_llm_api_key() -> str | None:
    _load_project_env()
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _load_previous_summary(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _build_comparison(
    keyword_result,
    llm_result,
) -> EvaluationComparison:
    accuracy_delta = llm_result.accuracy - keyword_result.accuracy
    per_class_deltas: dict[str, dict[str, float]] = {}
    for label in VALID_LABELS:
        kw_metrics = keyword_result.per_class_metrics.get(label)
        llm_metrics = llm_result.per_class_metrics.get(label)
        if kw_metrics and llm_metrics:
            per_class_deltas[label] = {
                "precision": llm_metrics.precision - kw_metrics.precision,
                "recall": llm_metrics.recall - kw_metrics.recall,
                "f1": llm_metrics.f1 - kw_metrics.f1,
            }
    return EvaluationComparison(
        keyword_result=keyword_result,
        llm_result=llm_result,
        accuracy_delta=accuracy_delta,
        per_class_deltas=per_class_deltas,
    )


async def run_eval_gate(
    dataset: Path,
    output: Path,
    *,
    skip_llm: bool,
    rebaseline_output: Path = DEFAULT_REBASELINE_OUTPUT,
    baseline_summary: Path | None = None,
) -> Path:
    api_key = _resolve_llm_api_key()
    effective_skip_llm = skip_llm or not api_key
    benchmark_provenance = build_benchmark_provenance(dataset)
    evaluator = ThesisEvaluator(
        llm_api_key=api_key,
        llm_temperature=0.0,
    )
    keyword_result = await evaluator.evaluate_keyword(dataset)
    llm_records: list[dict] = []
    if effective_skip_llm:
        comparison = EvaluationComparison(
            keyword_result=keyword_result,
            llm_result=None,
            accuracy_delta=None,
            per_class_deltas={},
        )
    else:
        samples, sample_evaluations = await evaluator.llm_evaluator.evaluate_samples(dataset)
        llm_result = evaluator.llm_evaluator.build_result_from_samples(
            dataset,
            samples,
            sample_evaluations,
        )
        comparison = _build_comparison(keyword_result, llm_result)
        llm_records = [
            {
                "sample_id": sample_eval.sample_id,
                "scenario": sample.get("metadata", {}).get("scenario"),
                "target": sample_eval.target,
                "prediction": sample_eval.prediction,
                "match": sample_eval.match,
            }
            for sample, sample_eval in zip(samples, sample_evaluations)
        ]
    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=list(default_proposed_changes()),
        benchmark_provenance=benchmark_provenance,
    )

    rebaseline_artifact = build_rebaseline_artifact(
        comparison,
        benchmark_provenance=benchmark_provenance,
        llm_records=llm_records,
        previous_summary=_load_previous_summary(baseline_summary),
    )
    if not api_key:
        artifact.setdefault("blocked_reasons", []).insert(
            0,
            "GOOGLE_API_KEY/GEMINI_API_KEY not available after loading the project environment.",
        )
        artifact["decision"] = "no_go"
        rebaseline_artifact.setdefault("justification", []).insert(
            0,
            "GOOGLE_API_KEY/GEMINI_API_KEY not available after loading the project environment.",
        )
    write_rebaseline_artifact(rebaseline_output, rebaseline_artifact)
    return write_eval_gate_artifact(output, artifact)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rebaseline-output", type=Path, default=DEFAULT_REBASELINE_OUTPUT)
    parser.add_argument(
        "--baseline-summary",
        type=Path,
        help="Optional prior benchmark summary JSON used to record pre-expansion sample count.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Only run the keyword baseline and emit a blocked eval-gate artifact.",
    )
    args = parser.parse_args()

    output_path = asyncio.run(
        run_eval_gate(
            args.dataset,
            args.output,
            skip_llm=args.skip_llm,
            rebaseline_output=args.rebaseline_output,
            baseline_summary=args.baseline_summary,
        )
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
