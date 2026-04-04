"""Execution-gate tests for the LLM-focused thesis fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.thesis_eval_gate import build_eval_gate_artifact
from utils.thesis_evaluator import KeywordEvaluator, ThesisEvaluator


DATASET_PATH = Path(__file__).parent.parent / "fixtures" / "thesis_llm_golden_set.jsonl"


@pytest.mark.asyncio
async def test_keyword_evaluator_runs_on_llm_fixture():
    evaluator = KeywordEvaluator()
    result = await evaluator.evaluate(DATASET_PATH)

    assert result.total_samples >= 30
    assert 0.0 <= result.accuracy <= 1.0
    assert set(result.per_class_metrics.keys()) == {"QUALIFIED", "HELD", "REJECTED"}


@pytest.mark.asyncio
async def test_eval_gate_artifact_blocks_without_llm_run():
    evaluator = ThesisEvaluator()
    comparison = await evaluator.evaluate_both(DATASET_PATH, skip_llm=True)
    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=["add structured decomposition fields"],
    )

    assert artifact["decision"] == "no_go"
    assert artifact["deferred_changes"] == ["add structured decomposition fields"]
