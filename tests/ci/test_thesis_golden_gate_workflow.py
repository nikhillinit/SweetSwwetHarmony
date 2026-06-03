# tests/ci/test_thesis_golden_gate_workflow.py
from pathlib import Path

import yaml

WF = Path(".github/workflows/thesis-golden-gate.yml")


def test_workflow_exists_and_parses():
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert data["name"] == "Thesis Golden Set Gate"


def test_no_top_level_path_filters():
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    # `on` is parsed by PyYAML as boolean True key; handle both.
    triggers = data.get("on") or data.get(True)
    pr = triggers["pull_request"]
    assert pr is None or "paths" not in pr  # must run on every PR
    assert "workflow_dispatch" in triggers


def test_runs_resolver_detector_and_checker():
    text = WF.read_text(encoding="utf-8")
    assert "resolve_thesis_eval_mode.py" in text
    assert "detect_thesis_sensitive_changes.py" in text
    assert "check_thesis_gate_artifact.py" in text
    assert "hermes providers doctor" in text


def test_workflow_uses_resolver_outputs_for_gold_and_hermes_modes():
    text = WF.read_text(encoding="utf-8")

    assert "id: resolve" in text
    assert "steps.resolve.outputs.mode == 'gold'" in text
    assert "steps.resolve.outputs.mode == 'hermes'" in text
    assert "env.GOOGLE_API_KEY != ''" not in text
    assert "python -m ops.cli hermes task thesis-eval --execute --json" in text


def test_workflow_passes_pr_labels_through_environment():
    text = WF.read_text(encoding="utf-8")

    assert "PR_LABELS: ${{ steps.labels.outputs.labels }}" in text
    assert '--labels "$PR_LABELS"' in text
    assert '--labels "${{ steps.labels.outputs.labels }}"' not in text


def test_workflow_exposes_gold_and_gemini_keys_to_resolver():
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    env = data["jobs"]["thesis-gate"]["env"]

    assert "GOOGLE_API_KEY" in env
    assert "GEMINI_API_KEY" in env


def test_workflow_runs_deliberation_as_advisory_artifact():
    text = WF.read_text(encoding="utf-8")

    assert "thesis_deliberation_check.py" in text
    assert "continue-on-error: true" in text
    assert "deliberation-check.json" in text
