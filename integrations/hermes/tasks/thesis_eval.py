from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.adapters import ExecutorResult, build_executor
from integrations.hermes.router import RoutingPlan, score_task_for_lane
from utils.thesis_benchmark import (
    build_benchmark_provenance,
    load_evaluation_dataset,
)
from utils.thesis_eval_gate import (
    build_eval_gate_artifact,
    build_rebaseline_artifact,
    default_proposed_changes,
    write_eval_gate_artifact,
    write_rebaseline_artifact,
)
from utils.thesis_evaluator import (
    VALID_LABELS,
    EvaluationComparison,
    KeywordEvaluator,
    ThesisEvaluationResult,
    calculate_metrics,
)

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskFailure,
    run_async_blocking,
)

DEFAULT_DATASET = "tests/fixtures/thesis_llm_golden_set.jsonl"
DEFAULT_MANIFEST = "tests/fixtures/thesis_llm_golden_set.manifest.json"
DEFAULT_OUTPUT = "artifacts/thesis_diagnostics/pr-gate.json"
DEFAULT_REBASELINE_OUTPUT = "artifacts/thesis_diagnostics/pr-rebaseline.json"
ROUTE_TASK = "thesis golden-set eval"
THESIS_EVAL_GATE_ARTIFACT = "thesis_eval_gate.json"
THESIS_EVAL_PREDICTIONS_ARTIFACT = "thesis_eval_predictions.json"
THESIS_EVAL_EXECUTOR_ARTIFACT = "thesis_eval_executor.json"
THESIS_EVAL_DRY_RUN_ARTIFACT = "thesis_eval_dry_run.json"


class ThesisEvalTask(HermesTask):
    name = "thesis-eval"
    description = "CLI-backed Hermes thesis golden-set evaluation."
    risk_level = "medium"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ()
    mutates_external_systems = False
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--dataset", default=DEFAULT_DATASET)
        parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
        parser.add_argument("--output", default=DEFAULT_OUTPUT)
        parser.add_argument("--rebaseline-output", default=DEFAULT_REBASELINE_OUTPUT)
        parser.add_argument("--baseline-summary")
        parser.add_argument("--min-accuracy", type=float, default=0.9)
        parser.add_argument("--executor")

    def plan(self, context: TaskContext) -> dict[str, Any]:
        dataset = _dataset_path(context)
        manifest = _manifest_path(context)
        output = _output_path(context)
        rebaseline_output = _rebaseline_output_path(context)
        benchmark, benchmark_error = _benchmark_evidence(dataset, manifest)
        routing, routing_error = _routing_evidence(context)

        plan = self._base_plan(context)
        plan.update(
            {
                "dataset": {
                    "path": str(dataset),
                    "exists": dataset.exists(),
                },
                "manifest": {
                    "path": str(manifest),
                    "exists": manifest.exists(),
                },
                "benchmark": benchmark,
                "routing": routing,
                "artifacts": {
                    "gate": str(output),
                    "rebaseline": str(rebaseline_output),
                    "ledger": [
                        THESIS_EVAL_GATE_ARTIFACT,
                        THESIS_EVAL_PREDICTIONS_ARTIFACT,
                        THESIS_EVAL_EXECUTOR_ARTIFACT,
                        "run_record.json",
                    ],
                },
                "preflight_gates": [
                    "dataset_exists",
                    "manifest_exists",
                    "benchmark_manifest_valid",
                    "executor_supports_execute",
                ],
                "postflight_gates": [
                    "thesis_eval_gate_artifact_written",
                    "thesis_eval_predictions_artifact_written",
                    "thesis_eval_executor_artifact_written",
                    "ledger_written",
                ],
                "prompt_contract": {
                    "allowed_labels": sorted(VALID_LABELS),
                    "sample_fields": ["sample_id", "input", "metadata"],
                    "forbidden_fields": ["target"],
                    "response_shape": {
                        "predictions": [
                            {
                                "sample_id": "...",
                                "prediction": "QUALIFIED|HELD|REJECTED",
                                "rationale": "...",
                            }
                        ]
                    },
                },
                "mutation": {
                    "allowed": False,
                    "affected_files": [],
                    "affected_tables": [],
                    "external_systems": [],
                    "ledger_artifacts": [
                        THESIS_EVAL_GATE_ARTIFACT,
                        THESIS_EVAL_PREDICTIONS_ARTIFACT,
                        THESIS_EVAL_EXECUTOR_ARTIFACT,
                        "run_record.json",
                    ],
                },
            }
        )
        if benchmark_error:
            plan["benchmark"]["error"] = benchmark_error
        if routing_error:
            plan["routing"]["error"] = routing_error
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        dataset = _dataset_path(context)
        manifest = _manifest_path(context)
        benchmark_error = str(plan.get("benchmark", {}).get("error") or "")
        routing_error = str(plan.get("routing", {}).get("error") or "")
        executor = plan.get("routing", {}).get("executor")
        return [
            CheckResult(
                "dataset_exists",
                dataset.exists(),
                str(dataset),
                {"path": str(dataset)},
            ),
            CheckResult(
                "manifest_exists",
                manifest.exists(),
                str(manifest),
                {"path": str(manifest)},
            ),
            CheckResult(
                "benchmark_manifest_valid",
                not benchmark_error,
                benchmark_error or "benchmark manifest valid",
                dict(plan.get("benchmark") or {}),
            ),
            CheckResult(
                "executor_supports_execute",
                bool(executor) and not routing_error,
                routing_error or str(executor),
                dict(plan.get("routing") or {}),
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        samples = _load_samples(_dataset_path(context))
        prompt = _build_prompt(samples)
        payload = {
            "task": self.name,
            "mode": context.mode,
            "dryRun": True,
            "mutationCommitted": False,
            "sampleCount": len(samples),
            "promptChars": len(prompt),
            "promptPreview": prompt[:1000],
            "executor": plan.get("routing", {}).get("executor"),
            "benchmarkFingerprint": plan.get("benchmark", {}).get(
                "benchmark_fingerprint"
            ),
        }
        context.write_json(THESIS_EVAL_DRY_RUN_ARTIFACT, payload)
        return payload

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        return _run_thesis_eval(context, plan)

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        if outputs.get("dryRun"):
            dry_run_path = context.run_dir / THESIS_EVAL_DRY_RUN_ARTIFACT
            return [
                CheckResult(
                    "thesis_eval_dry_run_artifact_written",
                    dry_run_path.exists(),
                    dry_run_path.name if dry_run_path.exists() else "missing",
                    {"path": str(dry_run_path)},
                ),
                CheckResult(
                    "ledger_written",
                    (context.run_dir / "run_record.json").exists(),
                    "run_record.json",
                ),
            ]

        gate_path = Path(str(outputs.get("gateArtifact") or ""))
        predictions_path = context.run_dir / THESIS_EVAL_PREDICTIONS_ARTIFACT
        executor_path = context.run_dir / THESIS_EVAL_EXECUTOR_ARTIFACT
        return [
            CheckResult(
                "thesis_eval_gate_artifact_written",
                gate_path.exists() and (context.run_dir / THESIS_EVAL_GATE_ARTIFACT).exists(),
                str(gate_path) if gate_path.exists() else "missing",
                {
                    "outputPath": str(gate_path),
                    "ledgerPath": str(context.run_dir / THESIS_EVAL_GATE_ARTIFACT),
                },
            ),
            CheckResult(
                "thesis_eval_predictions_artifact_written",
                predictions_path.exists(),
                predictions_path.name if predictions_path.exists() else "missing",
                {"path": str(predictions_path)},
            ),
            CheckResult(
                "thesis_eval_executor_artifact_written",
                executor_path.exists(),
                executor_path.name if executor_path.exists() else "missing",
                {"path": str(executor_path)},
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


def _dataset_path(context: TaskContext) -> Path:
    return context.resolve(getattr(context.args, "dataset", DEFAULT_DATASET)) or (
        context.root / DEFAULT_DATASET
    )


def _manifest_path(context: TaskContext) -> Path:
    return context.resolve(getattr(context.args, "manifest", DEFAULT_MANIFEST)) or (
        context.root / DEFAULT_MANIFEST
    )


def _output_path(context: TaskContext) -> Path:
    return context.resolve(getattr(context.args, "output", DEFAULT_OUTPUT)) or (
        context.root / DEFAULT_OUTPUT
    )


def _rebaseline_output_path(context: TaskContext) -> Path:
    return context.resolve(
        getattr(context.args, "rebaseline_output", DEFAULT_REBASELINE_OUTPUT)
    ) or (context.root / DEFAULT_REBASELINE_OUTPUT)


def _baseline_summary_path(context: TaskContext) -> Path | None:
    return context.resolve(getattr(context.args, "baseline_summary", None))


def _benchmark_evidence(dataset: Path, manifest: Path) -> tuple[dict[str, Any], str | None]:
    try:
        return build_benchmark_provenance(dataset, manifest_path=manifest), None
    except Exception as exc:
        return {
            "benchmark_fingerprint": None,
            "benchmark_sample_count": None,
        }, str(exc)


def _routing_evidence(context: TaskContext) -> tuple[dict[str, Any], str | None]:
    try:
        routing = _routing_plan(context)
    except Exception as exc:
        return {
            "task": ROUTE_TASK,
            "manual_executor": getattr(context.args, "executor", None),
            "executor": None,
        }, str(exc)
    return {
        "task": ROUTE_TASK,
        "phase": routing.phase,
        "risk": routing.risk,
        "manual_executor": routing.manual_model,
        "executor": routing.recommended_executor,
        "alternatives": list(routing.alternatives),
        "executorMetadata": {
            name: metadata.to_dict()
            for name, metadata in routing.executor_metadata
        },
    }, None


def _routing_plan(context: TaskContext) -> RoutingPlan:
    if context.config is None:
        raise ValueError("routing config missing")
    manual_executor = getattr(context.args, "executor", None) or None
    return score_task_for_lane(
        ROUTE_TASK,
        phase="production",
        config=context.config,
        manual_model=manual_executor,
        require_execute=True,
    )


def _load_samples(dataset_path: Path) -> list[dict[str, Any]]:
    samples = load_evaluation_dataset(dataset_path)
    missing_ids = [index for index, sample in enumerate(samples, 1) if not sample.get("id")]
    if missing_ids:
        raise TaskFailure(
            "thesis-eval dataset rows must include stable id values",
            evidence={"missingIdRows": missing_ids},
        )
    return samples


def _run_thesis_eval(context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
    if context.config is None:
        raise TaskFailure("routing config missing")
    dataset = _dataset_path(context)
    manifest = _manifest_path(context)
    output = _output_path(context)
    rebaseline_output = _rebaseline_output_path(context)
    samples = _load_samples(dataset)
    benchmark = build_benchmark_provenance(dataset, manifest_path=manifest)
    routing = _routing_plan(context)
    executor_name = routing.recommended_executor
    prompt = _build_prompt(samples)
    context.write_text("thesis_eval_prompt.txt", prompt)

    try:
        executor = build_executor(executor_name, context.config)
        started = time.monotonic()
        executor_result = run_async_blocking(executor.execute(prompt, context_files=None))
        if executor_result.duration_ms <= 0:
            duration_ms = int((time.monotonic() - started) * 1000)
            executor_result = ExecutorResult(
                executor=executor_result.executor,
                success=executor_result.success,
                exit_code=executor_result.exit_code,
                content=executor_result.content,
                duration_ms=duration_ms,
                error=executor_result.error,
                token_usage=executor_result.token_usage,
                provenance=executor_result.provenance,
            )
    except TaskFailure:
        raise
    except Exception as exc:
        raise TaskFailure(
            "thesis-eval executor failed",
            evidence={"executor": executor_name, "error": str(exc)},
        ) from exc

    context.write_json(THESIS_EVAL_EXECUTOR_ARTIFACT, executor_result.to_dict())
    if not executor_result.success:
        raise TaskFailure(
            "thesis-eval executor failed",
            evidence=executor_result.to_dict(),
        )

    predictions = _parse_prediction_payload(executor_result.content, samples)
    comparison, records = _build_comparison(
        dataset=dataset,
        samples=samples,
        predictions=predictions,
        token_usage=executor_result.token_usage,
        latency_ms=executor_result.duration_ms,
    )
    gate = build_eval_gate_artifact(
        comparison,
        threshold=float(getattr(context.args, "min_accuracy", 0.9) or 0.9),
        proposed_changes=list(default_proposed_changes()),
        benchmark_provenance=benchmark,
    )
    rebaseline = build_rebaseline_artifact(
        comparison,
        benchmark_provenance=benchmark,
        llm_records=records,
        previous_summary=_load_previous_summary(_baseline_summary_path(context)),
    )

    written_gate = write_eval_gate_artifact(output, gate)
    written_rebaseline = write_rebaseline_artifact(rebaseline_output, rebaseline)
    context.write_json(THESIS_EVAL_GATE_ARTIFACT, gate)
    context.write_json(
        THESIS_EVAL_PREDICTIONS_ARTIFACT,
        {
            "executor": executor_name,
            "predictions": records,
            "sampleCount": len(records),
            "benchmarkFingerprint": benchmark["benchmark_fingerprint"],
        },
    )
    return {
        "executor": executor_name,
        "sampleCount": len(samples),
        "accuracy": comparison.llm_result.accuracy if comparison.llm_result else None,
        "decision": gate["decision"],
        "benchmarkFingerprint": benchmark["benchmark_fingerprint"],
        "gateArtifact": str(written_gate),
        "rebaselineArtifact": str(written_rebaseline),
        "ledgerArtifacts": {
            "gate": THESIS_EVAL_GATE_ARTIFACT,
            "predictions": THESIS_EVAL_PREDICTIONS_ARTIFACT,
            "executor": THESIS_EVAL_EXECUTOR_ARTIFACT,
        },
        "mutationCommitted": False,
    }


def _build_prompt(samples: list[dict[str, Any]]) -> str:
    safe_rows = [
        {
            "sample_id": str(sample["id"]),
            "input": str(sample.get("input", "")),
            "metadata": _safe_metadata(sample.get("metadata")),
        }
        for sample in samples
    ]
    schema = {
        "predictions": [
            {
                "sample_id": "sample id from input",
                "prediction": "QUALIFIED | HELD | REJECTED",
                "rationale": "brief reason",
            }
        ]
    }
    return "\n".join(
        [
            "# Thesis Golden-Set Evaluation",
            "",
            "Classify each row for the Press On Ventures consumer thesis.",
            "Allowed labels: QUALIFIED, HELD, REJECTED.",
            "",
            "QUALIFIED: consumer CPG, consumer health tech, travel/hospitality, or consumer marketplace.",
            "HELD: plausible consumer-facing or ambiguous cases needing human review.",
            "REJECTED: B2B, enterprise SaaS, developer tools, crypto/Web3, cleantech, agencies, Series B+, or hardware-only.",
            "",
            "Return strict JSON only with exactly one prediction for every sample_id.",
            "Required JSON shape:",
            json.dumps(schema, indent=2, sort_keys=True),
            "",
            "Rows:",
            json.dumps(safe_rows, indent=2, sort_keys=True),
            "",
        ]
    )


def _safe_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    stripped = _strip_forbidden_label_keys(value)
    return stripped if isinstance(stripped, dict) else {}


def _strip_forbidden_label_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_forbidden_label_keys(item)
            for key, item in value.items()
            if str(key) not in {"target", "label", "ground_truth", "expected"}
        }
    if isinstance(value, list):
        return [_strip_forbidden_label_keys(item) for item in value]
    return value


def _parse_prediction_payload(
    content: str,
    samples: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    parsed = _parse_json_object(content)
    raw_predictions = parsed.get("predictions")
    errors: list[str] = []
    if not isinstance(raw_predictions, list):
        raise TaskFailure(
            "invalid thesis-eval predictions",
            evidence={"errors": ["predictions must be a list"]},
        )

    expected_ids = {str(sample["id"]) for sample in samples}
    seen: dict[str, dict[str, str]] = {}
    for index, item in enumerate(raw_predictions):
        if not isinstance(item, dict):
            errors.append(f"prediction at index {index} must be an object")
            continue
        sample_id = str(item.get("sample_id") or "").strip()
        prediction = str(item.get("prediction") or "").strip().upper()
        rationale = str(item.get("rationale") or "").strip()
        if not sample_id:
            errors.append(f"prediction at index {index} is missing sample_id")
            continue
        if sample_id not in expected_ids:
            errors.append(f"unknown sample_id {sample_id!r}")
            continue
        if sample_id in seen:
            errors.append(f"duplicate sample_id {sample_id!r}")
            continue
        if prediction not in VALID_LABELS:
            errors.append(f"invalid prediction {prediction!r} for sample_id {sample_id!r}")
            continue
        seen[sample_id] = {
            "sample_id": sample_id,
            "prediction": prediction,
            "rationale": rationale,
        }

    missing = sorted(expected_ids - set(seen))
    if missing:
        errors.append(f"missing predictions for sample_ids: {', '.join(missing)}")
    if errors:
        raise TaskFailure(
            "invalid thesis-eval predictions",
            evidence={"errors": errors, "contentExcerpt": content[:1000]},
        )
    return seen


def _parse_json_object(content: str) -> dict[str, Any]:
    if not content.strip():
        raise TaskFailure(
            "invalid thesis-eval predictions",
            evidence={"errors": ["executor output was empty"]},
        )
    for candidate in _json_candidates(content):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise TaskFailure(
        "invalid thesis-eval predictions",
        evidence={
            "errors": ["executor output did not contain a JSON object"],
            "contentExcerpt": content[:1000],
        },
    )


def _json_candidates(content: str) -> list[str]:
    candidates = [content.strip()]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*([\s\S]*?)\s*```",
            content,
            flags=re.IGNORECASE,
        )
    )
    return candidates


def _build_comparison(
    *,
    dataset: Path,
    samples: list[dict[str, Any]],
    predictions: dict[str, dict[str, str]],
    token_usage: dict[str, int],
    latency_ms: int,
) -> tuple[EvaluationComparison, list[dict[str, Any]]]:
    keyword_result = KeywordEvaluator().evaluate_sync(dataset)
    ordered_predictions = [
        predictions[str(sample["id"])]["prediction"]
        for sample in samples
    ]
    targets = [str(sample["target"]) for sample in samples]
    accuracy, per_class, confusion = calculate_metrics(ordered_predictions, targets)
    llm_result = ThesisEvaluationResult(
        run_id=f"hermes_{uuid.uuid4().hex[:8]}",
        evaluator_type="llm",
        dataset_path=str(dataset),
        total_samples=len(samples),
        accuracy=accuracy,
        per_class_metrics=per_class,
        confusion_matrix=confusion,
        timestamp=datetime.now(timezone.utc).isoformat(),
        latency_ms=latency_ms,
        avg_latency_ms=latency_ms / len(samples) if samples else None,
        token_usage=dict(token_usage),
        attempted_sample_count=len(samples),
    )
    comparison = EvaluationComparison(
        keyword_result=keyword_result,
        llm_result=llm_result,
        accuracy_delta=(
            llm_result.accuracy - keyword_result.accuracy
            if keyword_result.accuracy is not None and llm_result.accuracy is not None
            else None
        ),
        per_class_deltas=_per_class_deltas(keyword_result, llm_result),
    )
    records = [
        {
            "sample_id": str(sample["id"]),
            "scenario": str(sample.get("metadata", {}).get("scenario") or ""),
            "prediction": predictions[str(sample["id"])]["prediction"],
            "target": str(sample["target"]),
            "match": predictions[str(sample["id"])]["prediction"] == str(sample["target"]),
            "rationale": predictions[str(sample["id"])].get("rationale", ""),
        }
        for sample in samples
    ]
    return comparison, records


def _per_class_deltas(
    keyword_result: ThesisEvaluationResult,
    llm_result: ThesisEvaluationResult,
) -> dict[str, dict[str, float]]:
    deltas: dict[str, dict[str, float]] = {}
    for label in VALID_LABELS:
        keyword_metrics = keyword_result.per_class_metrics.get(label)
        llm_metrics = llm_result.per_class_metrics.get(label)
        if keyword_metrics is None or llm_metrics is None:
            continue
        deltas[label] = {
            "precision": llm_metrics.precision - keyword_metrics.precision,
            "recall": llm_metrics.recall - keyword_metrics.recall,
            "f1": llm_metrics.f1 - keyword_metrics.f1,
        }
    return deltas


def _load_previous_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
