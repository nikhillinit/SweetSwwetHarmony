"""Structural tests for the LLM-focused thesis golden set."""

from __future__ import annotations

import json
from pathlib import Path

from utils.thesis_evaluator import VALID_LABELS


GOLDEN_SET_PATH = Path(__file__).parent.parent / "fixtures" / "thesis_llm_golden_set.jsonl"


def _load_cases():
    with open(GOLDEN_SET_PATH, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


CASES = _load_cases()


def test_llm_golden_set_minimum_count():
    assert len(CASES) >= 30


def test_llm_golden_set_unique_ids():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))


def test_llm_golden_set_targets_are_valid():
    assert {case["target"] for case in CASES}.issubset(VALID_LABELS)


def test_llm_golden_set_scenario_coverage():
    covered = {case["metadata"]["scenario"] for case in CASES}
    required = {
        "clear_consumer",
        "clear_b2b",
        "b2b_in_disguise",
        "ad_supported",
        "employer_sponsored",
        "two_sided_marketplace",
        "gig_economy",
        "creator_tools",
    }
    assert required.issubset(covered)
