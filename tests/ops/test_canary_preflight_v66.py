"""Tests for canary-preflight CLI command.

Verifies:
- Works from repo root
- Missing DB → report with ok=false
- Env mismatch → report with ok=false
"""

import json
import subprocess
import sys

import pytest

PYTHON = sys.executable
CLI = "run_pipeline.py"


def _run_preflight(args, env_override=None, cwd=None):
    """Run canary-preflight via subprocess."""
    import os
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [PYTHON, CLI, "canary-preflight"] + args,
        capture_output=True, text=True, timeout=60,
        env=env, cwd=cwd,
    )


class TestPreflightFromRepoRoot:
    """Preflight works when run from repo root."""

    def test_help_exits_zero(self):
        result = _run_preflight(["--help"])
        assert result.returncode == 0


class TestMissingDb:
    """Missing DB produces report with ok=false."""

    def test_missing_db_report(self, tmp_path):
        report_path = str(tmp_path / "preflight_report.json")
        result = _run_preflight([
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--report", report_path,
        ])

        assert result.returncode != 0

        with open(report_path) as f:
            report = json.load(f)
        assert report["ok"] is False
        assert len(report["errors"]) > 0
        assert report["command"] == "canary-preflight"


class TestEnvMismatch:
    """Required env var mismatch produces report with ok=false."""

    def test_env_mismatch_report(self, tmp_path):
        report_path = str(tmp_path / "env_report.json")
        result = _run_preflight([
            "--db-path", str(tmp_path / "nonexistent.db"),
            "--report", report_path,
            "--require-env", "NONEXISTENT_VAR_XYZ=required_value",
        ])

        assert result.returncode != 0

        with open(report_path) as f:
            report = json.load(f)
        assert report["ok"] is False
        assert any("NONEXISTENT_VAR_XYZ" in e for e in report["errors"])
