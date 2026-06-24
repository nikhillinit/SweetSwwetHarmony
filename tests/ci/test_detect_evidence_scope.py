"""Unit tests for the PR-evidence scope helper.

The helper decides, inside the PR Evidence Gate job, whether a PR's changed
files require a live evidence bundle. Two contracts the workflow depends on are
asserted here:

  1. ``is_evidence_required`` covers the documented evidence paths AND the gate's
     own surface (so a PR weakening the gate is itself gated).
  2. The CLI prints ``true``/``false`` to stdout and ALWAYS exits 0 — the
     workflow parses stdout, it must not branch on exit status.
"""
from __future__ import annotations

from scripts.ci.detect_evidence_scope import (
    EVIDENCE_REQUIRED_PATTERNS,
    is_evidence_required,
    main,
)


# --- documented evidence paths are sensitive ---------------------------------


def test_storage_path_requires_evidence():
    assert is_evidence_required(["storage/signal_store.py"]) is True


def test_nested_storage_path_requires_evidence():
    assert is_evidence_required(["storage/migrations/quality_tables.py"]) is True


def test_pipeline_path_requires_evidence():
    assert is_evidence_required(["workflows/pipeline.py"]) is True


def test_run_manager_path_requires_evidence():
    assert is_evidence_required(["workflows/run_manager.py"]) is True


def test_golden_set_fixture_requires_evidence():
    assert is_evidence_required(["tests/fixtures/thesis_llm_golden_set.jsonl"]) is True


# --- the gate's own surface is sensitive (self-protecting) -------------------


def test_evidence_checker_script_is_self_protected():
    assert is_evidence_required(["scripts/check_pr_evidence.py"]) is True


def test_scope_helper_is_self_protected():
    assert is_evidence_required(["scripts/ci/detect_evidence_scope.py"]) is True


def test_pr_evidence_workflow_is_self_protected():
    assert is_evidence_required([".github/workflows/pr-evidence.yml"]) is True


def test_pr_evidence_workflow_contract_test_is_self_protected():
    assert is_evidence_required(["tests/ci/test_pr_evidence_workflow.py"]) is True


def test_check_pr_evidence_test_is_self_protected():
    assert is_evidence_required(["tests/scripts/test_check_pr_evidence.py"]) is True


# --- non-sensitive paths do not require evidence -----------------------------


def test_docs_only_change_is_not_sensitive():
    assert is_evidence_required(["docs/runbooks/hermes.md"]) is False


def test_unrelated_change_is_not_sensitive():
    assert is_evidence_required(["README.md", "dashboard/app.py"]) is False


def test_empty_changeset_is_not_sensitive():
    assert is_evidence_required([]) is False


def test_patterns_are_nonempty():
    assert len(EVIDENCE_REQUIRED_PATTERNS) >= 5


# --- stdout/exit-code contract the workflow relies on ------------------------


def test_cli_prints_true_and_exits_zero_for_sensitive(capsys):
    rc = main(["--changed-files", "storage/signal_store.py"])
    out = capsys.readouterr().out.strip()
    assert out == "true"
    assert rc == 0


def test_cli_prints_false_and_exits_zero_for_non_sensitive(capsys):
    rc = main(["--changed-files", "README.md"])
    out = capsys.readouterr().out.strip()
    assert out == "false"
    assert rc == 0


def test_cli_accepts_newline_separated_changed_files(capsys):
    rc = main(["--changed-files", "README.md\nstorage/signal_store.py"])
    out = capsys.readouterr().out.strip()
    assert out == "true"
    assert rc == 0
