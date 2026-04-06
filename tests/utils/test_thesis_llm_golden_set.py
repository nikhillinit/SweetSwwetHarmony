"""Structural tests for the LLM-focused thesis golden set."""

from __future__ import annotations

import json
from pathlib import Path

from utils.thesis_benchmark import compute_dataset_fingerprint, scenario_counts_for_samples
from utils.thesis_evaluator import VALID_LABELS


GOLDEN_SET_PATH = Path(__file__).parent.parent / "fixtures" / "thesis_llm_golden_set.jsonl"
MANIFEST_PATH = Path(__file__).parent.parent / "fixtures" / "thesis_llm_golden_set.manifest.json"
EXPECTED_SCENARIO_COUNTS = {
    "clear_consumer": 10,
    "clear_b2b": 10,
    "b2b_in_disguise": 11,
    "ad_supported": 7,
    "employer_sponsored": 7,
    "two_sided_marketplace": 7,
    "gig_economy": 6,
    "creator_tools": 6,
}
EXPECTED_AMBIGUOUS_SCENARIOS = {
    "b2b_in_disguise",
    "ad_supported",
    "employer_sponsored",
    "two_sided_marketplace",
    "gig_economy",
    "creator_tools",
}
FORBIDDEN_BENCHMARK_FIELDS = {
    "benchmark_id",
    "benchmark_version",
    "benchmark_fingerprint",
    "dataset_fingerprint",
}


def _load_cases():
    with open(GOLDEN_SET_PATH, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_manifest():
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        return json.load(handle)
CASES = _load_cases()
MANIFEST = _load_manifest()


def test_llm_golden_set_exact_sample_count():
    assert len(CASES) == 64


def test_llm_golden_set_unique_ids():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))


def test_llm_golden_set_targets_are_valid():
    assert {case["target"] for case in CASES}.issubset(VALID_LABELS)


def test_llm_golden_set_exact_scenario_counts():
    assert scenario_counts_for_samples(CASES) == EXPECTED_SCENARIO_COUNTS


def test_manifest_parity_with_expanded_fixture():
    assert MANIFEST["benchmark_id"] == "thesis_llm_golden_set"
    assert MANIFEST["dataset_path"] == "tests/fixtures/thesis_llm_golden_set.jsonl"
    assert MANIFEST["sample_count"] == len(CASES)
    assert MANIFEST["scenario_counts"] == scenario_counts_for_samples(CASES)
    assert set(MANIFEST["ambiguous_scenarios"]) == EXPECTED_AMBIGUOUS_SCENARIOS


def test_ambiguous_rows_require_label_rationale():
    for case in CASES:
        metadata = case["metadata"]
        if metadata["scenario"] in EXPECTED_AMBIGUOUS_SCENARIOS:
            assert metadata.get("label_rationale")


def test_clear_control_rows_do_not_need_label_rationale():
    for case in CASES:
        metadata = case["metadata"]
        if metadata["scenario"] not in EXPECTED_AMBIGUOUS_SCENARIOS:
            assert "label_rationale" not in metadata


def test_fixture_rows_do_not_duplicate_benchmark_identity_fields():
    for case in CASES:
        assert not (FORBIDDEN_BENCHMARK_FIELDS & set(case))
        metadata = case.get("metadata", {})
        assert not (FORBIDDEN_BENCHMARK_FIELDS & set(metadata))


def test_manifest_dataset_fingerprint_matches_fixture():
    assert MANIFEST["dataset_fingerprint"] == compute_dataset_fingerprint(CASES)
