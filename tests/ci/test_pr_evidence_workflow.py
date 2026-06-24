"""Contract tests for the PR Evidence Gate workflow (post-Phase-3 rewrite).

The gate is now ALWAYS-ON for PRs to ``main`` and decides *inside the job*
whether evidence is required, so it can be made a required check without ever
parking a PR on a ``Pending`` status (a path-filtered required check never
reports). It also protects its own source files.
"""
from __future__ import annotations

from pathlib import Path

from scripts.ci.detect_evidence_scope import is_evidence_required

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pr-evidence.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pr_evidence_workflow_exists() -> None:
    assert WORKFLOW.exists()


def test_pr_evidence_has_no_trigger_level_paths_filter() -> None:
    # A required check skipped by a trigger-level paths: filter stays Pending and
    # deadlocks merge. The gate must always run and decide scope in-job.
    assert "paths:" not in _workflow()


def test_pr_evidence_triggers_on_pull_request_to_main() -> None:
    wf = _workflow()
    assert "pull_request:" in wf
    # pull_request (read-only token), NOT pull_request_target (runs with write
    # token against untrusted PR head).
    assert "pull_request_target" not in wf
    assert "branches: [main]" in wf


def test_pr_evidence_reruns_on_body_edit_and_new_commits() -> None:
    wf = _workflow()
    # 'edited' so adding a run URL to the body re-checks; 'synchronize' so a new
    # head SHA re-checks.
    assert "edited" in wf
    assert "synchronize" in wf


def test_pr_evidence_has_read_only_permissions() -> None:
    wf = _workflow()
    assert "contents: read" in wf
    assert "actions: read" in wf
    assert "pull-requests: read" in wf
    # No write scopes.
    assert "contents: write" not in wf
    assert "pull-requests: write" not in wf


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


def test_pr_evidence_uses_paginated_files_api_not_truncating_pr_view() -> None:
    wf = _workflow()
    # gh pr view --json files truncates large PRs and could let a sensitive file
    # slip past the self-protecting gate. Use the paginated files API instead.
    assert "--paginate" in wf
    assert "/files" in wf
    assert "pr view" not in wf


def test_pr_evidence_parses_scope_helper_stdout_not_exit_status() -> None:
    wf = _workflow()
    # The helper prints true/false and exits 0; the workflow must capture stdout
    # (command substitution) and compare to "true", not branch on exit status.
    assert "detect_evidence_scope" in wf
    assert "--changed-files" in wf
    assert '= "true"' in wf


def test_gate_own_surface_is_in_sensitive_set() -> None:
    # A PR that weakens the gate is itself gated.
    for path in (
        "scripts/check_pr_evidence.py",
        "scripts/ci/detect_evidence_scope.py",
        ".github/workflows/pr-evidence.yml",
        "tests/ci/test_pr_evidence_workflow.py",
        "tests/scripts/test_check_pr_evidence.py",
    ):
        assert is_evidence_required([path]) is True, path


def test_docs_only_path_is_not_sensitive() -> None:
    assert is_evidence_required(["docs/runbooks/cloud-backup-setup.md"]) is False
