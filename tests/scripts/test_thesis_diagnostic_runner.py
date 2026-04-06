"""Tests for scripts/thesis_diagnostic_runner.py."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from scripts import thesis_diagnostic_runner as runner
from utils.thesis_benchmark import (
    compute_dataset_fingerprint,
    load_evaluation_dataset,
    manifest_path_for_dataset,
    scenario_counts_for_samples,
)
from utils.thesis_evaluator import LLMSampleEvaluation


def _write_dataset(path: Path) -> Path:
    samples = [
        {
            "id": "sample_1",
            "input": "Company: TestCo\nDescription: D2C meal kit\nWebsite: https://example.com\nSector: consumer_cpg",
            "target": "QUALIFIED",
            "metadata": {"scenario": "clear_consumer"},
        },
        {
            "id": "sample_2",
            "input": "Company: OpsCo\nDescription: Software for hotels\nSector: b2b_saas",
            "target": "REJECTED",
            "metadata": {"scenario": "b2b_in_disguise"},
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample) + "\n")
    return path


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


class _StubEvaluator:
    init_kwargs = None

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs

    async def evaluate_sample(self, sample):
        classification = runner.ThesisClassification(
            thesis_match=True,
            thesis_fit_score=0.9,
            category="consumer_cpg",
            stage_estimate="seed",
            confidence="high",
            company_name="StubCo",
            rationale="Stub rationale",
            key_signals=["stub"],
            prompt_version="diag-v1",
            model="stub-model",
            classification_status=runner.ClassificationStatus.SUCCESS.value,
            primary_end_user="individual_consumer",
            paying_customer="individual_consumer",
            sells_to_or_operates_in="operates_in_industry_for_consumers",
        )
        return LLMSampleEvaluation(
            sample_id=str(sample["id"]),
            target=sample["target"],
            prediction=sample["target"],
            match=True,
            signal_data={"title": "StubCo"},
            classification=classification,
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_run_diagnostic_dry_run_writes_artifacts(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    output_dir = tmp_path / "artifacts"

    artifact_path, summary_path = await runner.run_diagnostic(
        dataset,
        output_dir,
        run_id="smoke",
        dry_run=True,
    )

    assert artifact_path.exists()
    assert summary_path.exists()

    records = [json.loads(line) for line in artifact_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert all(record["match"] is True for record in records)
    assert records[0]["prompt_version"] == runner.CLASSIFIER_PROMPT_VERSION
    assert records[0]["benchmark_id"] == "thesis_llm_golden_set"
    assert records[0]["benchmark_version"] == "test.v1"
    assert records[0]["benchmark_manifest_path"].endswith("dataset.manifest.json")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["accuracy"] == 1.0
    assert summary["error_count"] == 0
    assert summary["benchmark_id"] == "thesis_llm_golden_set"
    assert summary["benchmark_sample_count"] == 2


@pytest.mark.asyncio
async def test_run_diagnostic_compare_against_reports_improvements(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    output_dir = tmp_path / "artifacts"
    baseline_path = tmp_path / "baseline.jsonl"
    baseline_records = [
        {
            "sample_id": "sample_1",
            "target": "QUALIFIED",
            "prediction": "REJECTED",
            "match": False,
            "benchmark_id": "thesis_llm_golden_set",
            "benchmark_version": "test.v1",
            "benchmark_fingerprint": compute_dataset_fingerprint(load_evaluation_dataset(dataset)),
            "benchmark_manifest_path": str(manifest_path_for_dataset(dataset)),
        },
        {
            "sample_id": "sample_2",
            "target": "REJECTED",
            "prediction": "REJECTED",
            "match": True,
            "benchmark_id": "thesis_llm_golden_set",
            "benchmark_version": "test.v1",
            "benchmark_fingerprint": compute_dataset_fingerprint(load_evaluation_dataset(dataset)),
            "benchmark_manifest_path": str(manifest_path_for_dataset(dataset)),
        },
    ]
    with baseline_path.open("w", encoding="utf-8") as handle:
        for record in baseline_records:
            handle.write(json.dumps(record) + "\n")

    _, summary_path = await runner.run_diagnostic(
        dataset,
        output_dir,
        run_id="candidate",
        dry_run=True,
        compare_against=baseline_path,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    comparison = summary["comparison"]
    assert comparison["status"] == "comparable"
    assert comparison["improved_sample_ids"] == ["sample_1"]
    assert comparison["regressed_sample_ids"] == []
    assert comparison["candidate_accuracy"] == 1.0


@pytest.mark.asyncio
async def test_run_diagnostic_requires_prompt_version_for_prompt_file(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("prompt override", encoding="utf-8")

    with pytest.raises(ValueError, match="prompt_version is required"):
        await runner.run_diagnostic(
            dataset,
            tmp_path / "artifacts",
            run_id="bad",
            dry_run=True,
            prompt_file=prompt_path,
        )


@pytest.mark.asyncio
async def test_run_diagnostic_passes_prompt_overrides_to_evaluator(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("prompt override", encoding="utf-8")

    monkeypatch.setattr(runner, "LLMEvaluator", _StubEvaluator)
    monkeypatch.setattr(runner, "_resolve_llm_api_key", lambda: "key")

    await runner.run_diagnostic(
        dataset,
        tmp_path / "artifacts",
        run_id="live",
        prompt_file=prompt_path,
        prompt_version="diag-v1",
        temperature=0.0,
    )

    assert _StubEvaluator.init_kwargs["system_prompt"] == "prompt override"
    assert _StubEvaluator.init_kwargs["prompt_version"] == "diag-v1"
    assert _StubEvaluator.init_kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_run_diagnostic_blocks_on_benchmark_mismatch(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    output_dir = tmp_path / "artifacts"
    baseline_path = tmp_path / "baseline.jsonl"
    baseline_path.write_text(
        json.dumps(
            {
                "sample_id": "sample_1",
                "target": "QUALIFIED",
                "prediction": "QUALIFIED",
                "match": True,
                "benchmark_id": "thesis_llm_golden_set",
                "benchmark_version": "older.v0",
                "benchmark_fingerprint": "different",
                "benchmark_manifest_path": "tests/fixtures/old.manifest.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, summary_path = await runner.run_diagnostic(
        dataset,
        output_dir,
        run_id="candidate",
        dry_run=True,
        compare_against=baseline_path,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    comparison = summary["comparison"]
    assert comparison["status"] == "blocked_benchmark_mismatch"
    assert "improved_sample_ids" not in comparison
    assert comparison["reasons"]


@pytest.mark.asyncio
async def test_run_diagnostic_blocks_when_baseline_missing_provenance(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    output_dir = tmp_path / "artifacts"
    baseline_path = tmp_path / "baseline.jsonl"
    baseline_path.write_text(
        json.dumps(
            {
                "sample_id": "sample_1",
                "target": "QUALIFIED",
                "prediction": "REJECTED",
                "match": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, summary_path = await runner.run_diagnostic(
        dataset,
        output_dir,
        run_id="candidate",
        dry_run=True,
        compare_against=baseline_path,
    )

    comparison = json.loads(summary_path.read_text(encoding="utf-8"))["comparison"]
    assert comparison["status"] == "blocked_benchmark_mismatch"
    assert any("missing benchmark provenance" in reason for reason in comparison["reasons"])


def test_main_returns_nonzero_on_blocked_benchmark_mismatch(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)
    output_dir = tmp_path / "artifacts"
    baseline_path = tmp_path / "baseline.jsonl"
    baseline_path.write_text(
        json.dumps(
            {
                "sample_id": "sample_1",
                "target": "QUALIFIED",
                "prediction": "QUALIFIED",
                "match": True,
                "benchmark_id": "thesis_llm_golden_set",
                "benchmark_version": "older.v0",
                "benchmark_fingerprint": "different",
                "benchmark_manifest_path": "tests/fixtures/old.manifest.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "thesis_diagnostic_runner.py",
            "--dataset",
            str(dataset),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "candidate",
            "--dry-run",
            "--compare-against",
            str(baseline_path),
        ],
    )

    assert runner.main() == 1
