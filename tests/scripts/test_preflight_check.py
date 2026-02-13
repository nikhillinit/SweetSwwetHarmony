"""Tests for scripts/preflight_check.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from scripts.preflight_check import (
    run_preflight,
    _check_db_integrity,
    _check_schema_version,
    _check_config_validation,
    _check_backup_freshness,
    _check_canary_items,
    _check_regression_freshness,
    _check_api_health,
    PASS, WARN, FAIL, SKIP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_full_db(path: Path) -> Path:
    """Create a DB with schema_migrations and canary_runs tables."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
    conn.execute("INSERT INTO schema_migrations VALUES (41)")
    conn.execute(
        "CREATE TABLE canary_runs (id INTEGER PRIMARY KEY, verdict TEXT, pass_rate REAL, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE canary_drift_alerts (id INTEGER PRIMARY KEY, severity TEXT, status TEXT)"
    )
    conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, canonical_key TEXT)")
    conn.commit()
    conn.close()
    return path


def _create_backup(backup_dir: Path, age_hours: float = 0) -> Path:
    """Create a fake backup file with controlled mtime."""
    from datetime import datetime, timezone, timedelta
    from scripts.backup_db import BACKUP_PREFIX, BACKUP_SUFFIX
    import os
    import time

    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    name = f"{BACKUP_PREFIX}{ts.strftime('%Y%m%d-%H%M%S')}{BACKUP_SUFFIX}"
    backup_file = backup_dir / name
    backup_file.write_bytes(b"backup-data")
    # Set mtime to match the intended age
    mtime = ts.timestamp()
    os.utime(str(backup_file), (mtime, mtime))
    return backup_file


# ---------------------------------------------------------------------------
# Check 1: DB integrity
# ---------------------------------------------------------------------------

class TestCheckDbIntegrity:
    def test_pass_on_valid_db(self, tmp_path):
        db = _create_full_db(tmp_path / "signals.db")
        result = _check_db_integrity(db)
        assert result["status"] == PASS

    def test_fail_on_missing_db(self, tmp_path):
        result = _check_db_integrity(tmp_path / "no-such.db")
        assert result["status"] == FAIL

    def test_fail_on_corrupt_db(self, tmp_path):
        corrupt = tmp_path / "corrupt.db"
        corrupt.write_bytes(b"NOT-A-DB" * 100)
        result = _check_db_integrity(corrupt)
        assert result["status"] == FAIL


# ---------------------------------------------------------------------------
# Check 2: Schema version
# ---------------------------------------------------------------------------

class TestCheckSchemaVersion:
    def test_pass_on_matching_version(self, tmp_path):
        db = _create_full_db(tmp_path / "signals.db")
        result = _check_schema_version(db)
        assert result["status"] == PASS

    def test_fail_on_old_version(self, tmp_path):
        db = tmp_path / "signals.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations VALUES (30)")
        conn.commit()
        conn.close()
        result = _check_schema_version(db)
        assert result["status"] == FAIL
        assert "missing migrations" in result["message"]

    def test_warn_on_future_version(self, tmp_path):
        db = tmp_path / "signals.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations VALUES (999)")
        conn.commit()
        conn.close()
        result = _check_schema_version(db)
        assert result["status"] == WARN
        assert "mismatch" in result["message"]


# ---------------------------------------------------------------------------
# Check 3: Config validation
# ---------------------------------------------------------------------------

class TestCheckConfigValidation:
    def test_pass_with_valid_config(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        result = _check_config_validation()
        # staging_only doesn't require Notion keys, so should be clean
        assert result["status"] == PASS

    def test_fail_with_invalid_delivery_mode(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "bogus")
        result = _check_config_validation()
        assert result["status"] == FAIL


# ---------------------------------------------------------------------------
# Check 6: Backup freshness
# ---------------------------------------------------------------------------

class TestCheckBackupFreshness:
    def test_pass_with_recent_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        _create_backup(backup_dir, age_hours=1)
        result = _check_backup_freshness(backup_dir)
        assert result["status"] == PASS

    def test_warn_with_no_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        result = _check_backup_freshness(backup_dir)
        assert result["status"] == WARN

    def test_warn_with_old_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        _create_backup(backup_dir, age_hours=48)
        result = _check_backup_freshness(backup_dir)
        assert result["status"] == WARN

    def test_warn_on_missing_dir(self, tmp_path):
        result = _check_backup_freshness(tmp_path / "no-backups")
        assert result["status"] == WARN


# ---------------------------------------------------------------------------
# Check 7: Canary items
# ---------------------------------------------------------------------------

class TestCheckCanaryItems:
    def test_pass_with_canary_runs(self, tmp_path):
        db = _create_full_db(tmp_path / "signals.db")
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO canary_runs (verdict, pass_rate, created_at) VALUES ('pass', 1.0, '2026-01-15T12:00:00Z')")
        conn.commit()
        conn.close()
        result = _check_canary_items(db)
        assert result["status"] == PASS

    def test_warn_with_no_canary_runs(self, tmp_path):
        db = _create_full_db(tmp_path / "signals.db")
        result = _check_canary_items(db)
        assert result["status"] == WARN


# ---------------------------------------------------------------------------
# Check 8: Regression freshness
# ---------------------------------------------------------------------------

class TestCheckRegressionFreshness:
    def test_warn_without_github_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = _check_regression_freshness()
        assert result["status"] == WARN

    def test_pass_with_success_check_run(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "check_runs": [
                {"name": "Core Regression Suite", "conclusion": "success"}
            ]
        }

        def _mock_subprocess_run(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            if "rev-parse" in cmd:
                proc.stdout = "abc123def456\n"
            elif "get-url" in cmd:
                proc.stdout = "https://github.com/owner/repo.git\n"
            return proc

        with patch("subprocess.run", side_effect=_mock_subprocess_run), \
             patch("scripts.preflight_check._httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_resp
            mock_httpx.Timeout = lambda *a, **kw: 10.0

            result = _check_regression_freshness()
            assert result["status"] == PASS

    def test_fail_with_failed_check_run(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "check_runs": [
                {"name": "Core Regression Suite", "conclusion": "failure"}
            ]
        }

        def _mock_subprocess_run(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            if "rev-parse" in cmd:
                proc.stdout = "abc123def456\n"
            elif "get-url" in cmd:
                proc.stdout = "https://github.com/owner/repo.git\n"
            return proc

        with patch("subprocess.run", side_effect=_mock_subprocess_run), \
             patch("scripts.preflight_check._httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_resp
            mock_httpx.Timeout = lambda *a, **kw: 10.0

            result = _check_regression_freshness()
            assert result["status"] == FAIL


# ---------------------------------------------------------------------------
# Check 9: API health
# ---------------------------------------------------------------------------

class TestCheckApiHealth:
    def test_skip_when_api_not_running(self):
        # Default: no API running -> should return skip
        result = _check_api_health()
        assert result["status"] in (SKIP, WARN)


# ---------------------------------------------------------------------------
# run_preflight integration
# ---------------------------------------------------------------------------

class TestRunPreflight:
    def test_all_pass_returns_pass(self, tmp_path, monkeypatch):
        """All checks pass -> overall pass."""
        db = _create_full_db(tmp_path / "signals.db")
        backup_dir = tmp_path / "backups"
        _create_backup(backup_dir, age_hours=1)

        # Add canary data
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO canary_runs (verdict, pass_rate, created_at) VALUES ('pass', 1.0, '2026-01-15T12:00:00Z')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        report = run_preflight(db, mode="quick", backup_dir=backup_dir)

        # With no GITHUB_TOKEN, regression check warns -> overall at least WARN
        non_skip = [c for c in report["checks"] if c["status"] not in (SKIP, WARN)]
        assert all(c["status"] == PASS for c in non_skip)
        assert report["overall"] in (PASS, WARN)

    def test_missing_db_returns_fail(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        report = run_preflight(tmp_path / "no-such.db", mode="quick", backup_dir=tmp_path / "backups")
        assert report["overall"] == FAIL
        assert report["checks"][0]["status"] == FAIL

    def test_old_schema_returns_fail(self, tmp_path, monkeypatch):
        db = tmp_path / "signals.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations VALUES (10)")
        conn.commit()
        conn.close()

        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        report = run_preflight(db, mode="quick", backup_dir=tmp_path / "backups")
        assert report["overall"] == FAIL

    def test_config_error_returns_fail(self, tmp_path, monkeypatch):
        db = _create_full_db(tmp_path / "signals.db")
        monkeypatch.setenv("DELIVERY_MODE", "invalid_value")
        report = run_preflight(db, mode="quick", backup_dir=tmp_path / "backups")
        assert report["overall"] == FAIL

    def test_json_output(self, tmp_path, monkeypatch):
        db = _create_full_db(tmp_path / "signals.db")
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        report = run_preflight(db, mode="quick", backup_dir=tmp_path / "backups")
        # Verify JSON-serializable
        serialized = json.dumps(report)
        parsed = json.loads(serialized)
        assert "overall" in parsed
        assert "checks" in parsed

    def test_quick_mode_skips_smoke(self, tmp_path, monkeypatch):
        db = _create_full_db(tmp_path / "signals.db")
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        report = run_preflight(db, mode="quick", backup_dir=tmp_path / "backups")
        smoke = next(c for c in report["checks"] if c["check"] == "smoke_suite")
        assert smoke["status"] == SKIP
