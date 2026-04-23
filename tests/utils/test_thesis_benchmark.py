"""Tests for thesis benchmark manifest and provenance helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.thesis_benchmark import (
    build_benchmark_provenance,
    compare_provenance,
    compute_dataset_fingerprint,
    load_benchmark_manifest,
    load_evaluation_dataset,
    manifest_path_for_dataset,
    missing_provenance_fields,
    scenario_counts_for_samples,
)


def _write_dataset(path: Path) -> Path:
    rows = [
        {
            "id": "sample_1",
            "input": "Company: TestCo\nDescription: D2C meal kit\nSector: consumer_cpg",
            "target": "QUALIFIED",
            "metadata": {"scenario": "clear_consumer", "sector": "consumer_cpg"},
        },
        {
            "id": "sample_2",
            "input": "Company: OpsCo\nDescription: Software for hotels\nSector: b2b_saas",
            "target": "REJECTED",
            "metadata": {"scenario": "b2b_in_disguise", "sector": "b2b_saas"},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _write_manifest(dataset_path: Path, *, manifest_dataset_path: str | None = None) -> Path:
    samples = load_evaluation_dataset(dataset_path)
    manifest = {
        "benchmark_id": "thesis_llm_golden_set",
        "benchmark_version": "test.v1",
        "dataset_path": manifest_dataset_path or str(dataset_path),
        "dataset_fingerprint": compute_dataset_fingerprint(samples),
        "sample_count": len(samples),
        "scenario_counts": scenario_counts_for_samples(samples),
        "ambiguous_scenarios": ["b2b_in_disguise"],
        "changelog": [{"version": "test.v1", "summary": "test"}],
    }
    manifest_path = manifest_path_for_dataset(dataset_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def test_compute_dataset_fingerprint_is_deterministic(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    samples = load_evaluation_dataset(dataset)

    assert compute_dataset_fingerprint(samples) == compute_dataset_fingerprint(samples)


def test_load_benchmark_manifest_validates_parity(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    manifest_path = _write_manifest(dataset)

    manifest = load_benchmark_manifest(dataset, manifest_path=manifest_path)

    assert manifest["benchmark_fingerprint"] == manifest["dataset_fingerprint"]
    assert manifest["benchmark_sample_count"] == 2
    assert manifest["scenario_counts"] == {
        "b2b_in_disguise": 1,
        "clear_consumer": 1,
    }


def test_load_benchmark_manifest_resolves_relative_dataset_path_from_manifest_directory(
    tmp_path,
    monkeypatch,
):
    dataset_dir = tmp_path / "fixtures"
    dataset_dir.mkdir()
    dataset = _write_dataset(dataset_dir / "dataset.jsonl")
    manifest_path = _write_manifest(dataset, manifest_dataset_path=dataset.name)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    manifest = load_benchmark_manifest(dataset.resolve(), manifest_path=manifest_path)

    assert manifest["benchmark_fingerprint"] == manifest["dataset_fingerprint"]
    assert manifest["benchmark_manifest_path"].endswith("dataset.manifest.json")


def test_load_benchmark_manifest_resolves_repo_root_relative_dataset_path(
    tmp_path,
    monkeypatch,
):
    dataset_dir = tmp_path / "fixtures"
    dataset_dir.mkdir()
    dataset = _write_dataset(dataset_dir / "dataset.jsonl")
    manifest_path = _write_manifest(
        dataset,
        manifest_dataset_path="fixtures/dataset.jsonl",
    )
    monkeypatch.chdir(tmp_path)

    manifest = load_benchmark_manifest(dataset.resolve(), manifest_path=manifest_path)

    assert manifest["benchmark_fingerprint"] == manifest["dataset_fingerprint"]
    assert manifest["dataset_path"].endswith("fixtures\\dataset.jsonl") or manifest["dataset_path"].endswith("fixtures/dataset.jsonl")


def test_load_benchmark_manifest_fails_on_drift(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    manifest_path = _write_manifest(dataset)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_fingerprint"] = "wrong"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dataset_fingerprint"):
        load_benchmark_manifest(dataset, manifest_path=manifest_path)


def test_build_benchmark_provenance_echoes_manifest_fields(tmp_path):
    dataset = _write_dataset(tmp_path / "dataset.jsonl")
    _write_manifest(dataset)

    provenance = build_benchmark_provenance(dataset)

    assert provenance["benchmark_id"] == "thesis_llm_golden_set"
    assert provenance["benchmark_version"] == "test.v1"
    assert provenance["benchmark_manifest_path"].endswith("dataset.manifest.json")
    assert provenance["benchmark_sample_count"] == 2


def test_compare_provenance_reports_mismatches():
    baseline = {
        "benchmark_id": "bench",
        "benchmark_version": "v1",
        "benchmark_fingerprint": "abc",
        "benchmark_manifest_path": "one",
    }
    candidate = {
        "benchmark_id": "bench",
        "benchmark_version": "v2",
        "benchmark_fingerprint": "xyz",
        "benchmark_manifest_path": "two",
    }

    reasons = compare_provenance(baseline, candidate)

    assert len(reasons) == 2
    assert any("benchmark_version differs" in reason for reason in reasons)
    assert any("benchmark_fingerprint differs" in reason for reason in reasons)


def test_missing_provenance_fields_reports_all_required_fields():
    missing = missing_provenance_fields({})

    assert missing == [
        "benchmark_id",
        "benchmark_version",
        "benchmark_fingerprint",
        "benchmark_manifest_path",
    ]
