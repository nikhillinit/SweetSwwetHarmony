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
