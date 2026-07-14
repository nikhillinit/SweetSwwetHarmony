"""Tests for the trust command group (trust-release milestone status generator).

Covers:
- argparse registration of the trust group and status subcommand
- markdown table emission with all milestone rows
- provenance markers (manual vs derived) including failed derived checks
- --out file writing
- ledger-audit evidence appendix (scan latest / --evidence override / absent)
- no-network default (no subprocess use unless --live-gh)
- --live-gh run-conclusion annotation (subprocess mocked)
- end-to-end registration through python -m ops.cli
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ops.trust_cli import register_trust_commands, _cmd_status
import ops.trust_release_status as trs
from ops.trust_release_status import (
    MILESTONES,
    build_status_report,
    load_ledger_audit_summary,
    render_status_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

TABLE_HEADER = "| Milestone | Status | Evidence / caveat | Provenance |"


def _parse(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    subparsers = parser.add_subparsers(dest="command")
    register_trust_commands(subparsers)
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_trust_status_registers_with_func(self):
        args = _parse(["trust", "status"])
        assert args.command == "trust"
        assert callable(args.func)

    def test_trust_status_flags(self):
        args = _parse([
            "trust", "status",
            "--out", "table.md",
            "--evidence", "report.json",
            "--live-gh",
        ])
        assert args.out == "table.md"
        assert args.evidence == "report.json"
        assert args.live_gh is True

    def test_defaults_are_offline_and_stdout_only(self):
        args = _parse(["trust", "status"])
        assert args.out is None
        assert args.evidence is None
        assert args.live_gh is False

    def test_registered_through_ops_cli_module(self):
        result = subprocess.run(
            [sys.executable, "-m", "ops.cli", "trust", "status", "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "--out" in result.stdout
        assert "--live-gh" in result.stdout


# ---------------------------------------------------------------------------
# Table emission
# ---------------------------------------------------------------------------

class TestTableEmission:
    def test_table_header_and_all_milestone_rows_present(self):
        report = build_status_report(repo_root=REPO_ROOT)
        md = render_status_markdown(report)
        assert TABLE_HEADER in md
        for m in MILESTONES:
            assert m.milestone in md, f"missing row for {m.key}"

    def test_every_data_row_has_provenance_marker(self):
        report = build_status_report(repo_root=REPO_ROOT)
        md = render_status_markdown(report)
        lines = [l for l in md.splitlines() if l.startswith("| ") and l != TABLE_HEADER]
        assert len(lines) == len(MILESTONES)
        for line in lines:
            assert ("manual" in line) or ("derived" in line), line

    def test_generated_by_banner_names_the_command(self):
        report = build_status_report(repo_root=REPO_ROOT)
        md = render_status_markdown(report)
        assert "python -m ops.cli trust status" in md
        assert "ops/trust_release_status.py" in md


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_manual_row_marked_manual(self):
        report = build_status_report(repo_root=REPO_ROOT)
        row = next(r for r in report.rows if r.milestone.key == "p0-3-dry-run")
        assert row.provenance_label.startswith("manual")

    def test_derived_row_passes_repo_checks_in_real_repo(self):
        report = build_status_report(repo_root=REPO_ROOT)
        row = next(r for r in report.rows if r.milestone.key == "m1a-db-anomaly")
        assert row.checks_ok is True
        assert row.provenance_label.startswith("derived")
        assert "FAILED" not in row.provenance_label

    def test_derived_row_flags_failed_checks_on_empty_root(self, tmp_path):
        report = build_status_report(repo_root=tmp_path)
        row = next(r for r in report.rows if r.milestone.key == "m1a-db-anomaly")
        assert row.checks_ok is False
        assert "FAILED" in row.provenance_label

    def test_m7_import_check_validates_schema_gate(self):
        report = build_status_report(repo_root=REPO_ROOT)
        row = next(r for r in report.rows if r.milestone.key == "m7-trust-status-cli")
        assert row.checks_ok is True


# ---------------------------------------------------------------------------
# Ledger-audit evidence appendix (READ-ONLY inputs)
# ---------------------------------------------------------------------------

def _write_fake_audit(runs_dir: Path, run_id: str, audit_id: str) -> Path:
    d = runs_dir / run_id
    d.mkdir(parents=True)
    path = d / "ledger_audit_report.json"
    path.write_text(json.dumps({
        "auditId": audit_id,
        "generatedAt": "2026-07-14T18:35:05+00:00",
        "operatorSummary": {
            "status": "action_required",
            "totalFindings": 11,
            "blockingFindings": 11,
            "severityCounts": {"low": 0, "medium": 0, "high": 0, "critical": 11},
            "subsystemsWithFindings": ["restore_sqlite"],
            "nextAction": "review_blocking_findings",
        },
    }), encoding="utf-8")
    return path


class TestLedgerAppendix:
    def test_latest_report_is_selected_by_run_dir_name(self, tmp_path):
        runs = tmp_path / "ai-logs" / "hermes" / "runs"
        _write_fake_audit(runs, "hermes_20260601_000000_aaaaaaaa", "audit-old")
        _write_fake_audit(runs, "hermes_20260714_183501_081efde1", "audit-new")
        summary = load_ledger_audit_summary(repo_root=tmp_path)
        assert summary is not None
        assert summary["auditId"] == "audit-new"

    def test_evidence_flag_overrides_scan(self, tmp_path):
        runs = tmp_path / "ai-logs" / "hermes" / "runs"
        _write_fake_audit(runs, "hermes_20260714_183501_081efde1", "audit-scanned")
        override = _write_fake_audit(
            tmp_path / "elsewhere", "hermes_20260601_000000_bbbbbbbb", "audit-override"
        )
        summary = load_ledger_audit_summary(repo_root=tmp_path, evidence_path=override)
        assert summary["auditId"] == "audit-override"

    def test_appendix_rendered_into_markdown(self, tmp_path):
        runs = tmp_path / "ai-logs" / "hermes" / "runs"
        _write_fake_audit(runs, "hermes_20260714_183501_081efde1", "ledger-audit-20260714T183505Z")
        report = build_status_report(repo_root=tmp_path)
        md = render_status_markdown(report)
        assert "ledger-audit-20260714T183505Z" in md
        assert "action_required" in md
        assert "restore_sqlite" in md

    def test_absent_artifacts_noted_not_fatal(self, tmp_path):
        report = build_status_report(repo_root=tmp_path)
        md = render_status_markdown(report)
        assert "No hermes ledger-audit artifacts found" in md


# ---------------------------------------------------------------------------
# Network discipline
# ---------------------------------------------------------------------------

class TestNetworkDiscipline:
    def test_no_subprocess_use_without_live_gh(self, monkeypatch):
        def _boom(*args, **kwargs):  # pragma: no cover - failure path
            raise AssertionError("subprocess must not be used without --live-gh")
        monkeypatch.setattr(trs.subprocess, "run", _boom)
        report = build_status_report(repo_root=REPO_ROOT)
        md = render_status_markdown(report)
        assert "--live-gh" in md  # skipped-verification note names the flag

    def test_live_gh_annotates_run_conclusions(self, monkeypatch):
        calls = []

        def _fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            class R:
                returncode = 0
                stdout = '{"conclusion": "success"}'
                stderr = ""
            return R()

        monkeypatch.setattr(trs.subprocess, "run", _fake_run)
        report = build_status_report(repo_root=REPO_ROOT, live_gh=True)
        assert calls, "gh must be invoked under --live-gh"
        assert all(c[0] == "gh" for c in calls)
        md = render_status_markdown(report)
        assert "gh-verified" in md


# ---------------------------------------------------------------------------
# --out
# ---------------------------------------------------------------------------

class TestOutFlag:
    def test_out_writes_utf8_file_and_prints_table(self, tmp_path, capsys):
        out_file = tmp_path / "trust-status.md"
        args = argparse.Namespace(
            out=str(out_file), evidence=None, live_gh=False, repo_root=str(REPO_ROOT)
        )
        _cmd_status(args)
        captured = capsys.readouterr()
        assert TABLE_HEADER in captured.out
        content = out_file.read_text(encoding="utf-8")
        assert TABLE_HEADER in content
        for m in MILESTONES:
            assert m.milestone in content
