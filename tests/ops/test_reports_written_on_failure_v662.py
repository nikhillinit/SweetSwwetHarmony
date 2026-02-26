"""Report-on-fail contract tests for all 5 v6.6.2 commands.

For EACH command: trigger a hard-fail, assert:
- exit code != 0
- report file exists at --report path
- report JSON parses
- report["ok"] == false
- len(report["errors"]) > 0
"""

import json
import subprocess
import sys

import pytest

PYTHON = sys.executable
CLI = "run_pipeline.py"


def _run(cmd, args, cwd=None):
    """Run a CLI command via subprocess."""
    return subprocess.run(
        [PYTHON, CLI, cmd] + args,
        capture_output=True, text=True, timeout=60,
        cwd=cwd,
    )


def _assert_fail_report(report_path: str, expected_command: str):
    """Assert the report at path is a valid fail report."""
    with open(report_path) as f:
        report = json.load(f)
    assert report["ok"] is False, f"Expected ok=false, got {report['ok']}"
    assert len(report["errors"]) > 0, "Expected at least one error"
    assert report["command"] == expected_command


class TestCanaryPreflightReportOnFail:
    def test_report_written_on_failure(self, tmp_path):
        report_path = str(tmp_path / "report.json")
        result = _run("canary-preflight", [
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--report", report_path,
        ])
        assert result.returncode != 0
        _assert_fail_report(report_path, "canary-preflight")


class TestBackfillEvidenceFamilyReportOnFail:
    def test_report_written_on_failure(self, tmp_path):
        report_path = str(tmp_path / "report.json")
        result = _run("backfill-evidence-family", [
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--dry-run",
            "--report", report_path,
        ])
        assert result.returncode != 0
        _assert_fail_report(report_path, "backfill-evidence-family")


class TestRehydrateCanonicalKeysV2ReportOnFail:
    def test_report_written_on_failure(self, tmp_path):
        report_path = str(tmp_path / "report.json")
        result = _run("rehydrate-canonical-keys-v2", [
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--dry-run",
            "--report", report_path,
        ])
        assert result.returncode != 0
        _assert_fail_report(report_path, "rehydrate-canonical-keys-v2")


class TestConvergenceKpiReportOnFail:
    def test_report_written_on_failure(self, tmp_path):
        report_path = str(tmp_path / "report.json")
        result = _run("convergence-kpi", [
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--report", report_path,
        ])
        assert result.returncode != 0
        _assert_fail_report(report_path, "convergence-kpi")


class TestHealthJsonPureReportOnFail:
    def test_report_written_on_failure(self, tmp_path):
        report_path = str(tmp_path / "report.json")
        result = _run("health-json-pure", [
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--report", report_path,
        ])
        assert result.returncode != 0
        _assert_fail_report(report_path, "health-json-pure")
