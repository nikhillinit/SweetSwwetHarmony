"""Hermetic discovery + run test for the ``thesis_sample_data`` Inspect AI task.

Reproduces the exact loading semantics the thesis-eval workflow relies on:
a fresh Python process (``-I`` isolated mode, so the project root is NOT on
``sys.path``) invoking the Inspect AI CLI with a ``file.py@task`` selector.
Inspect AI loads task files via ``chdir_python(file.parent)``, which puts only
``tests/evaluation`` on ``sys.path`` -- so the task module must bootstrap its
own imports to load hermetically.

Runs with the built-in ``mockllm/model`` provider: no provider keys, no
network access.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("inspect_ai")

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tests" / "evaluation" / "thesis_eval.py"
TASK_SPEC = f"{TASK_FILE.as_posix()}@thesis_sample_data"

# Strip every provider credential so the run is provably key-free.
PROVIDER_KEY_VARS = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
)


def _hermetic_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in PROVIDER_KEY_VARS and key != "PYTHONPATH"
    }
    return env


def _run_inspect_eval(log_dir: Path, task_spec: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "inspect_ai",
            "eval",
            task_spec,
            "--model",
            "mockllm/model",
            "--log-dir",
            str(log_dir),
            "--log-format",
            "json",
            "--display",
            "plain",
        ],
        cwd=ROOT,
        env=_hermetic_env(),
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_thesis_sample_data_discovers_and_runs_with_mock_model(tmp_path: Path) -> None:
    """The workflow's file.py@task selector must discover AND run the task."""
    log_dir = tmp_path / "logs"
    proc = _run_inspect_eval(log_dir, TASK_SPEC)

    combined = f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert proc.returncode == 0, combined
    assert "ModuleNotFoundError" not in combined
    assert "No inspect tasks were found" not in combined

    logs = sorted(log_dir.glob("*.json"))
    assert logs, f"no json eval log produced. {combined}"

    data = json.loads(logs[-1].read_text(encoding="utf-8"))
    assert data["status"] == "success", data.get("error")
    assert data["eval"]["task"].endswith("thesis_sample_data")

    # All 5 in-memory samples ran and an accuracy metric was produced.
    results = data["results"]
    assert results["total_samples"] == 5
    accuracy = _extract_accuracy(results)
    assert isinstance(accuracy, (int, float))
    assert 0.0 <= float(accuracy) <= 1.0


def _extract_accuracy(results: dict) -> float | None:
    """Mirror of the workflow's accuracy extraction from an eval json log."""
    for score in results.get("scores", []):
        metric = score.get("metrics", {}).get("accuracy")
        if metric is not None:
            return metric.get("value")
    return None
