"""Tests for scripts/run_thesis_llm_eval_gate.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_thesis_llm_eval_gate as gate_script
from utils.thesis_evaluator import EvaluationComparison


class _StubResult:
    def __init__(self, accuracy: float, *, errors: list[str] | None = None):
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


class _StubEvaluator:
    def __init__(self, llm_api_key=None):
        self.llm_api_key = llm_api_key
        self.calls = []

    async def evaluate_both(self, dataset, skip_llm=False):
        self.calls.append((Path(dataset), skip_llm))
        return EvaluationComparison(
            keyword_result=_StubResult(accuracy=0.4),
            llm_result=None if skip_llm else _StubResult(accuracy=0.95),
            accuracy_delta=None if skip_llm else 0.55,
            per_class_deltas={},
        )


@pytest.mark.asyncio
async def test_run_eval_gate_auto_skips_llm_without_key(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("", encoding="utf-8")
    output = tmp_path / "artifact.json"

    stub = _StubEvaluator()
    monkeypatch.setattr(gate_script, "ThesisEvaluator", lambda llm_api_key=None: stub)
    monkeypatch.setattr(gate_script, "_resolve_llm_api_key", lambda: None)

    output_path = await gate_script.run_eval_gate(dataset, output, skip_llm=False)

    assert output_path == output
    artifact = output.read_text(encoding="utf-8")
    assert "GOOGLE_API_KEY/GEMINI_API_KEY not available" in artifact
    assert stub.calls == [(dataset, True)]


@pytest.mark.asyncio
async def test_run_eval_gate_runs_llm_when_key_present(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("", encoding="utf-8")
    output = tmp_path / "artifact.json"

    stub = _StubEvaluator()
    monkeypatch.setattr(gate_script, "ThesisEvaluator", lambda llm_api_key=None: stub)
    monkeypatch.setattr(gate_script, "_resolve_llm_api_key", lambda: "key")

    await gate_script.run_eval_gate(dataset, output, skip_llm=False)

    assert stub.calls == [(dataset, False)]
