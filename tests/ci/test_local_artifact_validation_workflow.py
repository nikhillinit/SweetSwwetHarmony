"""Contract test for the Local Artifact Validation workflow.

Locks the shape that validates documentation artifacts on every PR (and on
demand) and runs the local-artifact test lane.

Raw-text assertions (YAML-1.1 loaders mis-parse the ``on`` key).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "local-artifact-validation.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists() -> None:
    assert WORKFLOW.exists()


def test_triggers_on_pull_request_and_dispatch() -> None:
    wf = _workflow()
    assert "name: Local Artifact Validation" in wf
    assert "pull_request:" in wf
    assert "workflow_dispatch:" in wf


def test_validates_local_docs_artifacts() -> None:
    wf = _workflow()
    assert "python scripts/ci/check_doc_artifacts.py docs" in wf


def test_runs_local_artifact_tests() -> None:
    wf = _workflow()
    assert "python -m pytest" in wf
    assert "tests/ci/test_check_doc_artifacts.py" in wf
    assert "tests/scripts/test_create_doc_artifact.py" in wf


def test_permissions_are_read_only() -> None:
    wf = _workflow()
    assert "contents: read" in wf
    assert "contents: write" not in wf
