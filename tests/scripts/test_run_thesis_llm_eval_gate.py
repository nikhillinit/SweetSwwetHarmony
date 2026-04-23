"""Tests for scripts/run_thesis_llm_eval_gate.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_thesis_llm_eval_gate as gate_script
from utils.thesis_benchmark import (
    compute_dataset_fingerprint,
    load_evaluation_dataset,
    manifest_path_for_dataset,
    scenario_counts_for_samples,
)


class _StubResult:
    def __init__(
        self,
        accuracy: float | None,
        *,
        errors: list[str] | None = None,
        run_state: str = "completed",
        llm_execution_error_count: int = 0,
        attempted_sample_count: int | None = None,
        blocked_reason: str | None = None,
    ):
        self.run_id = "run"
        self.evaluator_type = "llm"
        self.dataset_path = "dataset"
        self.total_samples = 30
        self.accuracy = accuracy
        self.per_class_metrics = {}
        self.confusion_matrix = {}
        self.timestamp = "2026-04-04T00:00:00Z"
        self.latency_ms = None
        self.avg_latency_ms = None
        self.token_usage = None
        self.errors = errors or []
        self.run_state = run_state
        self.llm_execution_error_count = llm_execution_error_count
        self.attempted_sample_count = attempted_sample_count
        self.blocked_reason = blocked_reason


class _StubEvaluator:
    def __init__(self, llm_api_key=None):
        self.llm_api_key = llm_api_key
        self.keyword_calls = []
        self.llm_calls = []
        self.llm_fail_fast_flags = []
        self.keyword_evaluator = self
        self.llm_evaluator = self

    async def evaluate(self, dataset):
        self.keyword_calls.append(Path(dataset))
        return _StubResult(accuracy=0.4)

    async def evaluate_keyword(self, dataset):
        return await self.evaluate(dataset)

    async def evaluate_samples(self, dataset, *, fail_fast_on_operational_failure=False):
        self.llm_calls.append(Path(dataset))
        self.llm_fail_fast_flags.append(fail_fast_on_operational_failure)
        samples = load_evaluation_dataset(dataset)
        sample_evaluations = [
            type(
                "SampleEval",
                (),
                {
                    "sample_id": sample["id"],
                    "target": sample["target"],
                    "prediction": sample["target"],
                    "match": True,
                },
            )()
            for sample in samples
        ]
        return samples, sample_evaluations

    def build_result_from_samples(self, dataset, samples, sample_evaluations):
        return _StubResult(accuracy=0.95)


def _write_manifest(dataset_path: Path) -> Path:
    samples = load_evaluation_dataset(dataset_path)
    manifest = {
        "benchmark_id": "thesis_llm_golden_set",
        "benchmark_version": "test.v1",
        "dataset_path": str(dataset_path),
        "dataset_fingerprint": compute_dataset_fingerprint(samples),
        "sample_count": len(samples),
        "scenario_counts": scenario_counts_for_samples(samples),
        "ambiguous_scenarios": ["b2b_in_disguise"],
        "changelog": [{"version": "test.v1", "summary": "test"}],
    }
    manifest_path = manifest_path_for_dataset(dataset_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


@pytest.mark.asyncio
async def test_run_eval_gate_auto_skips_llm_without_key(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "sample_1",
                "input": "Company: Test\nDescription: D2C\nSector: consumer_cpg",
                "target": "QUALIFIED",
                "metadata": {"scenario": "clear_consumer"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(dataset)
    output = tmp_path / "artifact.json"
    rebaseline = tmp_path / "rebaseline.json"

    stub = _StubEvaluator()
    monkeypatch.setattr(gate_script, "ThesisEvaluator", lambda **kwargs: stub)
    monkeypatch.setattr(gate_script, "_resolve_llm_api_key", lambda: None)

    output_path = await gate_script.run_eval_gate(
        dataset,
        output,
        skip_llm=False,
        rebaseline_output=rebaseline,
    )

    assert output_path == output
    artifact = output.read_text(encoding="utf-8")
    assert "GOOGLE_API_KEY/GEMINI_API_KEY not available" in artifact
    assert "benchmark_id" in artifact
    assert stub.keyword_calls == [dataset]
    assert stub.llm_calls == []
    rebaseline_artifact = json.loads(rebaseline.read_text(encoding="utf-8"))
    assert rebaseline_artifact["benchmark_id"] == "thesis_llm_golden_set"
    assert rebaseline_artifact["recommendation"] is None


@pytest.mark.asyncio
async def test_run_eval_gate_runs_llm_when_key_present(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "sample_1",
                "input": "Company: Test\nDescription: D2C\nSector: consumer_cpg",
                "target": "QUALIFIED",
                "metadata": {"scenario": "clear_consumer"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(dataset)
    output = tmp_path / "artifact.json"
    rebaseline = tmp_path / "rebaseline.json"
    baseline_summary = tmp_path / "baseline.summary.json"
    baseline_summary.write_text(json.dumps({"total_samples": 40}) + "\n", encoding="utf-8")

    stub = _StubEvaluator()
    monkeypatch.setattr(gate_script, "ThesisEvaluator", lambda **kwargs: stub)
    monkeypatch.setattr(gate_script, "_resolve_llm_api_key", lambda: "key")

    await gate_script.run_eval_gate(
        dataset,
        output,
        skip_llm=False,
        rebaseline_output=rebaseline,
        baseline_summary=baseline_summary,
    )

    assert stub.keyword_calls == [dataset]
    assert stub.llm_calls == [dataset]
    assert stub.llm_fail_fast_flags == [True]
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["benchmark_id"] == "thesis_llm_golden_set"
    assert artifact["benchmark_version"] == "test.v1"
    rebaseline_artifact = json.loads(rebaseline.read_text(encoding="utf-8"))
    assert rebaseline_artifact["pre_expansion_sample_count"] == 40
    assert rebaseline_artifact["post_expansion_sample_count"] == 1
    assert rebaseline_artifact["recommendation"] == "keep_0_90"


@pytest.mark.asyncio
async def test_run_eval_gate_emits_blocked_execution_artifacts(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "sample_1",
                "input": "Company: Test\nDescription: D2C\nSector: consumer_cpg",
                "target": "QUALIFIED",
                "metadata": {"scenario": "clear_consumer"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(dataset)
    output = tmp_path / "artifact.json"
    rebaseline = tmp_path / "rebaseline.json"

    stub = _StubEvaluator()
    stub.build_result_from_samples = lambda dataset, samples, sample_evaluations: _StubResult(
        accuracy=None,
        errors=["Sample sample_1: Classification failed: ConnectError"],
        run_state="blocked_execution",
        llm_execution_error_count=1,
        attempted_sample_count=1,
        blocked_reason=(
            "LLM preflight hit a Gemini API/transport failure before the full benchmark "
            "evaluation; keep the gate blocked until the evaluation environment is healthy."
        ),
    )
    monkeypatch.setattr(gate_script, "ThesisEvaluator", lambda **kwargs: stub)
    monkeypatch.setattr(gate_script, "_resolve_llm_api_key", lambda: "key")

    await gate_script.run_eval_gate(
        dataset,
        output,
        skip_llm=False,
        rebaseline_output=rebaseline,
    )

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["run_state"] == "blocked_execution"
    assert artifact["llm_execution_error_count"] == 1
    assert artifact["llm_attempted_sample_count"] == 1
    assert artifact["llm_accuracy"] is None
    assert artifact["accuracy_delta"] is None
    assert "preflight hit a gemini api/transport failure" in artifact["blocked_reasons"][0].lower()

    rebaseline_artifact = json.loads(rebaseline.read_text(encoding="utf-8"))
    assert rebaseline_artifact["run_state"] == "blocked_execution"
    assert rebaseline_artifact["llm_execution_error_count"] == 1
    assert rebaseline_artifact["llm_attempted_sample_count"] == 1
    assert rebaseline_artifact["overall_llm_accuracy"] is None
    assert rebaseline_artifact["recommendation"] == "blocked_execution"
    assert "preflight hit a gemini api/transport failure" in rebaseline_artifact["justification"][0].lower()
