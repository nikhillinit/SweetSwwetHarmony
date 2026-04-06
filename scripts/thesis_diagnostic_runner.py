"""Run sample-level thesis classifier diagnostics and optional baseline comparison."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from consumer.thesis_filter.llm_classifier import (
    CLASSIFIER_PROMPT_VERSION,
    ClassificationStatus,
    ThesisClassification,
)
from utils.thesis_benchmark import (
    build_benchmark_provenance,
    compare_provenance,
    extract_artifact_provenance,
    missing_provenance_fields,
)
from utils.thesis_evaluator import (
    LLMEvaluator,
    LLMSampleEvaluation,
    calculate_metrics,
    load_evaluation_dataset,
)


DEFAULT_DATASET = Path("tests/fixtures/thesis_llm_golden_set.jsonl")
DEFAULT_OUTPUT_DIR = Path("artifacts/thesis_diagnostics")


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


def _read_prompt_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _build_dry_run_classification(target: str, prompt_version: str) -> ThesisClassification:
    """Return a deterministic stub result for plumbing verification."""
    if target == "QUALIFIED":
        thesis_match = True
        score = 0.9
        category = "consumer_cpg"
    elif target == "HELD":
        thesis_match = True
        score = 0.2
        category = "other"
    else:
        thesis_match = False
        score = 0.0
        category = "excluded"

    return ThesisClassification(
        thesis_match=thesis_match,
        thesis_fit_score=score,
        category=category,
        stage_estimate="seed" if thesis_match else "unknown",
        confidence="high",
        company_name="dry-run",
        rationale="Dry run stub classification for sandbox validation.",
        key_signals=["dry-run"],
        prompt_version=prompt_version,
        model="dry-run-model",
        classification_status=ClassificationStatus.SUCCESS.value,
        primary_end_user="individual_consumer" if thesis_match else "unclear",
        paying_customer="individual_consumer" if thesis_match else "unclear",
        sells_to_or_operates_in=(
            "operates_in_industry_for_consumers" if thesis_match else "unclear"
        ),
    )


def _sample_record(
    sample: dict[str, Any],
    sample_eval: Any,
    benchmark_provenance: dict[str, Any],
) -> dict[str, Any]:
    classification = sample_eval.classification
    scenario = sample.get("metadata", {}).get("scenario")

    classification_status = (
        classification.classification_status
        if classification is not None
        else ClassificationStatus.ERROR_API.value
    )
    error = sample_eval.error
    if (
        error is None
        and classification is not None
        and classification.classification_status != ClassificationStatus.SUCCESS.value
    ):
        error = classification.rationale

    return {
        "sample_id": sample_eval.sample_id,
        "scenario": scenario,
        "target": sample_eval.target,
        "prediction": sample_eval.prediction,
        "match": sample_eval.match,
        "classification_status": classification_status,
        "thesis_match": classification.thesis_match if classification else None,
        "thesis_fit_score": classification.thesis_fit_score if classification else None,
        "category": classification.category if classification else None,
        "confidence": classification.confidence if classification else None,
        "rationale": classification.rationale if classification else None,
        "primary_end_user": classification.primary_end_user if classification else None,
        "paying_customer": classification.paying_customer if classification else None,
        "sells_to_or_operates_in": (
            classification.sells_to_or_operates_in if classification else None
        ),
        "prompt_version": classification.prompt_version if classification else None,
        "model": classification.model if classification else None,
        "latency_ms": sample_eval.latency_ms,
        "input_tokens": classification.input_tokens if classification else None,
        "output_tokens": classification.output_tokens if classification else None,
        "error": error,
        "benchmark_id": benchmark_provenance["benchmark_id"],
        "benchmark_version": benchmark_provenance["benchmark_version"],
        "benchmark_fingerprint": benchmark_provenance["benchmark_fingerprint"],
        "benchmark_manifest_path": benchmark_provenance["benchmark_manifest_path"],
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _build_comparison_summary(
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    baseline_path: Path,
) -> dict[str, Any]:
    baseline_provenance, baseline_reasons = _resolve_artifact_provenance(
        baseline_records,
        baseline_path,
    )
    candidate_provenance, candidate_reasons = _resolve_artifact_provenance(
        candidate_records,
        None,
    )

    mismatch_reasons = baseline_reasons + candidate_reasons
    mismatch_reasons.extend(compare_provenance(baseline_provenance, candidate_provenance))
    if mismatch_reasons:
        return {
            "status": "blocked_benchmark_mismatch",
            "baseline_path": str(baseline_path),
            "reasons": mismatch_reasons,
            "baseline_provenance": baseline_provenance,
            "candidate_provenance": candidate_provenance,
        }

    baseline_by_id = {record["sample_id"]: record for record in baseline_records}
    candidate_by_id = {record["sample_id"]: record for record in candidate_records}

    baseline_only = sorted(set(baseline_by_id) - set(candidate_by_id))
    candidate_only = sorted(set(candidate_by_id) - set(baseline_by_id))
    shared_ids = sorted(set(baseline_by_id) & set(candidate_by_id))

    improved_sample_ids: list[str] = []
    regressed_sample_ids: list[str] = []
    unchanged_correct_sample_ids: list[str] = []
    unchanged_incorrect_sample_ids: list[str] = []

    for sample_id in shared_ids:
        baseline_match = bool(baseline_by_id[sample_id]["match"])
        candidate_match = bool(candidate_by_id[sample_id]["match"])
        if not baseline_match and candidate_match:
            improved_sample_ids.append(sample_id)
        elif baseline_match and not candidate_match:
            regressed_sample_ids.append(sample_id)
        elif baseline_match and candidate_match:
            unchanged_correct_sample_ids.append(sample_id)
        else:
            unchanged_incorrect_sample_ids.append(sample_id)

    baseline_accuracy = (
        sum(1 for record in baseline_records if record["match"]) / len(baseline_records)
        if baseline_records
        else 0.0
    )
    candidate_accuracy = (
        sum(1 for record in candidate_records if record["match"]) / len(candidate_records)
        if candidate_records
        else 0.0
    )

    return {
        "status": "comparable",
        "baseline_path": str(baseline_path),
        "baseline_accuracy": round(baseline_accuracy, 4),
        "candidate_accuracy": round(candidate_accuracy, 4),
        "shared_sample_count": len(shared_ids),
        "baseline_only_sample_ids": baseline_only,
        "candidate_only_sample_ids": candidate_only,
        "improved_sample_ids": improved_sample_ids,
        "regressed_sample_ids": regressed_sample_ids,
        "unchanged_correct_sample_ids": unchanged_correct_sample_ids,
        "unchanged_incorrect_sample_ids": unchanged_incorrect_sample_ids,
    }


def _resolve_artifact_provenance(
    records: list[dict[str, Any]],
    artifact_path: Path | None,
) -> tuple[dict[str, Any], list[str]]:
    provenance: dict[str, Any] = {}
    reasons: list[str] = []

    if not records:
        label = str(artifact_path) if artifact_path is not None else "candidate artifact"
        return provenance, [f"{label} has no records to inspect for benchmark provenance"]

    first = records[0]
    provenance = extract_artifact_provenance(first)
    missing = missing_provenance_fields(first)
    if missing:
        label = str(artifact_path) if artifact_path is not None else "candidate artifact"
        reasons.append(f"{label} is missing benchmark provenance fields: {', '.join(missing)}")

    for record in records[1:]:
        record_provenance = extract_artifact_provenance(record)
        for field in provenance:
            if record_provenance.get(field) != provenance.get(field):
                label = str(artifact_path) if artifact_path is not None else "candidate artifact"
                reasons.append(f"{label} has inconsistent {field} values across records")
                break

    return provenance, reasons


def _build_summary(
    *,
    run_id: str,
    dataset_path: Path,
    records: list[dict[str, Any]],
    dry_run: bool,
    comparison: dict[str, Any] | None,
    benchmark_provenance: dict[str, Any],
) -> dict[str, Any]:
    predictions = [record["prediction"] for record in records]
    targets = [record["target"] for record in records]
    accuracy, per_class, confusion = calculate_metrics(predictions, targets)
    error_count = sum(1 for record in records if record.get("error"))

    return {
        "run_id": run_id,
        "dataset_path": str(dataset_path),
        "dry_run": dry_run,
        "total_samples": len(records),
        "accuracy": round(accuracy, 4),
        "per_class_metrics": {label: metrics.to_dict() for label, metrics in per_class.items()},
        "confusion_matrix": confusion,
        "error_count": error_count,
        "comparison": comparison,
        "benchmark_id": benchmark_provenance["benchmark_id"],
        "benchmark_version": benchmark_provenance["benchmark_version"],
        "benchmark_fingerprint": benchmark_provenance["benchmark_fingerprint"],
        "benchmark_manifest_path": benchmark_provenance["benchmark_manifest_path"],
        "benchmark_sample_count": benchmark_provenance["benchmark_sample_count"],
    }


def _print_summary(summary: dict[str, Any], artifact_path: Path, summary_path: Path) -> None:
    print(f"artifact: {artifact_path}")
    print(f"summary: {summary_path}")
    print(f"accuracy: {summary['accuracy']:.1%} ({summary['total_samples']} samples)")
    print(f"errors: {summary['error_count']}")

    comparison = summary.get("comparison")
    if comparison:
        print(f"comparison_status: {comparison['status']}")
        if comparison["status"] == "comparable":
            print(f"baseline_accuracy: {comparison['baseline_accuracy']:.1%}")
            print(f"candidate_accuracy: {comparison['candidate_accuracy']:.1%}")
            print(f"improved: {len(comparison['improved_sample_ids'])}")
            print(f"regressed: {len(comparison['regressed_sample_ids'])}")
        else:
            print(f"comparison_blocked: {'; '.join(comparison['reasons'])}")


async def run_diagnostic(
    dataset: Path,
    output_dir: Path,
    *,
    run_id: str,
    dry_run: bool = False,
    prompt_file: Path | None = None,
    prompt_version: str | None = None,
    compare_against: Path | None = None,
    temperature: float | None = None,
) -> tuple[Path, Path]:
    if prompt_file is not None and not prompt_version:
        raise ValueError("prompt_version is required when prompt_file is provided")

    prompt_text = None
    if prompt_file is not None:
        prompt_text = _read_prompt_file(prompt_file)

    if not dry_run:
        api_key = _resolve_llm_api_key()
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY/GEMINI_API_KEY not available after loading the project environment."
            )
    else:
        api_key = None

    samples = load_evaluation_dataset(dataset)
    benchmark_provenance = build_benchmark_provenance(dataset)
    evaluator = LLMEvaluator(
        api_key=api_key,
        system_prompt=prompt_text,
        prompt_version=prompt_version,
        temperature=temperature,
    )
    effective_prompt_version = prompt_version or CLASSIFIER_PROMPT_VERSION

    records: list[dict[str, Any]] = []
    for sample in samples:
        if dry_run:
            signal_data = evaluator._parse_input_to_signal(sample["input"])
            target = sample.get("target", "HELD")
            classification = _build_dry_run_classification(
                target,
                effective_prompt_version,
            )
            sample_eval = LLMSampleEvaluation(
                sample_id=str(sample.get("id", "")),
                target=target,
                prediction=target,
                match=True,
                signal_data=signal_data,
                classification=classification,
                latency_ms=0,
            )
        else:
            sample_eval = await evaluator.evaluate_sample(sample)

        records.append(_sample_record(sample, sample_eval, benchmark_provenance))

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{run_id}.jsonl"
    _write_jsonl(artifact_path, records)

    comparison = None
    if compare_against is not None:
        comparison = _build_comparison_summary(
            _load_jsonl_records(compare_against),
            records,
            compare_against,
        )

    summary = _build_summary(
        run_id=run_id,
        dataset_path=dataset,
        records=records,
        dry_run=dry_run,
        comparison=comparison,
        benchmark_provenance=benchmark_provenance,
    )
    summary_path = output_dir / f"{run_id}.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _print_summary(summary, artifact_path, summary_path)
    return artifact_path, summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--prompt-version")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compare-against", type=Path)
    parser.add_argument("--temperature", type=float)
    args = parser.parse_args()

    if args.prompt_file and not args.prompt_version:
        parser.error("--prompt-version is required when --prompt-file is provided")

    asyncio.run(
        run_diagnostic(
            args.dataset,
            args.output_dir,
            run_id=args.run_id,
            dry_run=args.dry_run,
            prompt_file=args.prompt_file,
            prompt_version=args.prompt_version,
            compare_against=args.compare_against,
            temperature=args.temperature,
        )
    )
    summary_path = args.output_dir / f"{args.run_id}.summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    comparison = summary.get("comparison")
    if comparison and comparison.get("status") == "blocked_benchmark_mismatch":
        print("benchmark comparison blocked", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
