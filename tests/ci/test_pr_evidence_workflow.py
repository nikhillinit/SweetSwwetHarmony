from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pr-evidence.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pr_evidence_workflow_exists() -> None:
    assert WORKFLOW.exists()


def test_pr_evidence_reruns_on_body_edit_and_new_commits() -> None:
    wf = _workflow()
    assert "pull_request:" in wf
    # 'edited' so adding a run URL to the body re-checks; 'synchronize' so a new
    # head SHA re-checks.
    assert "edited" in wf
    assert "synchronize" in wf


def test_pr_evidence_scoped_to_documented_evidence_paths() -> None:
    wf = _workflow()
    assert "paths:" in wf
    assert "storage/**" in wf
    assert "workflows/pipeline.py" in wf
    assert "workflows/run_manager.py" in wf


def test_pr_evidence_runs_live_check_tied_to_head_sha() -> None:
    wf = _workflow()
    assert "scripts/check_pr_evidence.py" in wf
    assert "--live" in wf
    assert "--head-sha" in wf


def test_pr_evidence_passes_body_via_env_not_interpolation() -> None:
    wf = _workflow()
    # GitHub-recommended: never interpolate untrusted ${{ }} into the run shell.
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in wf
    assert "PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in wf
    assert '--body "$PR_BODY"' in wf
    assert '--head-sha "$PR_HEAD_SHA"' in wf
    assert '--body "${{ github.event.pull_request.body }}"' not in wf


def test_pr_evidence_grants_actions_read_for_gh_api() -> None:
    wf = _workflow()
    assert "actions: read" in wf
    assert "GH_TOKEN: ${{ github.token }}" in wf
