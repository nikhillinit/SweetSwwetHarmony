"""Helpers for thesis benchmark manifests, provenance, and fingerprinting."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


REQUIRED_MANIFEST_FIELDS = {
    "benchmark_id",
    "benchmark_version",
    "dataset_path",
    "dataset_fingerprint",
    "sample_count",
    "scenario_counts",
    "ambiguous_scenarios",
    "changelog",
}

PROVENANCE_FIELDS = (
    "benchmark_id",
    "benchmark_version",
    "benchmark_fingerprint",
    "benchmark_manifest_path",
)


def load_evaluation_dataset(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL evaluation dataset."""
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    samples: list[dict[str, Any]] = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                samples.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_num}: {exc}") from exc
    return samples


def manifest_path_for_dataset(dataset_path: str | Path) -> Path:
    """Return the manifest path for a thesis benchmark dataset."""
    path = Path(dataset_path)
    base = path.with_suffix("") if path.suffix else path
    return base.with_suffix(".manifest.json")


def canonicalize_dataset_samples(samples: Sequence[dict[str, Any]]) -> str:
    """Canonical JSONL serialization used for dataset fingerprinting."""
    normalized_lines = [
        json.dumps(sample, sort_keys=True, separators=(",", ":"))
        for sample in samples
    ]
    return "\n".join(normalized_lines) + "\n"


def compute_dataset_fingerprint(samples: Sequence[dict[str, Any]]) -> str:
    """Compute the canonical lowercase SHA-256 dataset fingerprint."""
    payload = canonicalize_dataset_samples(samples).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scenario_counts_for_samples(samples: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count samples by metadata.scenario."""
    counts = Counter(sample.get("metadata", {}).get("scenario", "") for sample in samples)
    return {
        scenario: count
        for scenario, count in sorted(counts.items())
        if scenario
    }


def _display_path(path: Path) -> str:
    try:
        return os.path.relpath(path, Path.cwd())
    except ValueError:
        return str(path)


def _resolve_manifest_dataset_path(raw_path: str, manifest_path: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.parent / candidate).resolve()


def load_benchmark_manifest(
    dataset_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Load and optionally validate the benchmark manifest for a dataset."""
    dataset = Path(dataset_path).resolve()
    manifest = Path(manifest_path) if manifest_path is not None else manifest_path_for_dataset(dataset)
    manifest = manifest.resolve()

    if not manifest.exists():
        raise FileNotFoundError(f"Benchmark manifest not found: {manifest}")

    with manifest.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    missing = sorted(REQUIRED_MANIFEST_FIELDS - set(data))
    if missing:
        raise ValueError(f"Benchmark manifest missing required fields: {', '.join(missing)}")

    samples = load_evaluation_dataset(dataset)
    computed_counts = scenario_counts_for_samples(samples)
    computed_fingerprint = compute_dataset_fingerprint(samples)

    manifest_dataset_path = _resolve_manifest_dataset_path(data["dataset_path"], manifest)
    errors: list[str] = []
    if manifest_dataset_path != dataset:
        errors.append(
            f"Manifest dataset_path resolves to {manifest_dataset_path} but dataset is {dataset}"
        )
    if int(data["sample_count"]) != len(samples):
        errors.append(
            f"Manifest sample_count {data['sample_count']} does not match dataset row count {len(samples)}"
        )
    if data["scenario_counts"] != computed_counts:
        errors.append(
            "Manifest scenario_counts do not match dataset contents"
        )
    if data["dataset_fingerprint"] != computed_fingerprint:
        errors.append("Manifest dataset_fingerprint does not match canonical dataset fingerprint")

    ambiguous = data.get("ambiguous_scenarios", [])
    if len(ambiguous) != len(set(ambiguous)):
        errors.append("Manifest ambiguous_scenarios contains duplicates")

    if validate and errors:
        raise ValueError("; ".join(errors))

    return {
        **data,
        "dataset_path": _display_path(dataset),
        "dataset_fingerprint": computed_fingerprint,
        "benchmark_fingerprint": computed_fingerprint,
        "benchmark_manifest_path": _display_path(manifest),
        "benchmark_sample_count": len(samples),
        "scenario_counts": computed_counts,
    }


def build_benchmark_provenance(
    dataset_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return the benchmark provenance fields echoed by downstream artifacts."""
    manifest = load_benchmark_manifest(dataset_path, manifest_path=manifest_path, validate=True)
    return {
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_version": manifest["benchmark_version"],
        "benchmark_fingerprint": manifest["benchmark_fingerprint"],
        "benchmark_manifest_path": manifest["benchmark_manifest_path"],
        "benchmark_sample_count": manifest["benchmark_sample_count"],
        "ambiguous_scenarios": list(manifest["ambiguous_scenarios"]),
        "scenario_counts": manifest["scenario_counts"],
    }


def extract_artifact_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract benchmark provenance fields from an artifact record or summary."""
    return {field: payload.get(field) for field in PROVENANCE_FIELDS}


def missing_provenance_fields(payload: dict[str, Any]) -> list[str]:
    """Return missing benchmark provenance fields for a record or summary."""
    return [field for field in PROVENANCE_FIELDS if not payload.get(field)]


def compare_provenance(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[str]:
    """Return mismatch reasons for artifact provenance."""
    reasons: list[str] = []
    for field in ("benchmark_id", "benchmark_version", "benchmark_fingerprint"):
        if baseline.get(field) != candidate.get(field):
            reasons.append(
                f"{field} differs: baseline={baseline.get(field)!r}, candidate={candidate.get(field)!r}"
            )
    return reasons
