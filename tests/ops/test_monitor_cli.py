"""Phase 2.3 — CLI monitor subcommand tests."""

import subprocess
import sys
import pytest

from ops.storage import OpsStorage


@pytest.fixture
def ops_db(tmp_path):
    db_path = tmp_path / "test_cli.db"
    storage = OpsStorage(str(db_path))
    yield str(db_path), storage
    del storage


def _run_cli(db_path: str, *args) -> subprocess.CompletedProcess:
    """Run ops CLI as subprocess."""
    cmd = [sys.executable, "-m", "ops.cli", "--db", db_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class TestMonitorStatus:
    def test_monitor_status_output(self, ops_db):
        """monitor status should produce output without crashing."""
        db_path, storage = ops_db
        result = _run_cli(db_path, "monitor", "status")
        assert result.returncode == 0
        assert "OPS MONITOR STATUS" in result.stdout

    def test_monitor_status_ascii_only(self, ops_db):
        """Output should be ASCII-safe (no emoji, no box-drawing)."""
        db_path, storage = ops_db
        # Insert some data so there's output to check
        storage.log_health("test_comp", "healthy", latency_ms=5.0)

        result = _run_cli(db_path, "monitor", "status")
        assert result.returncode == 0
        # Check that all chars are ASCII-printable (plus newlines/tabs)
        for char in result.stdout:
            assert ord(char) < 128, f"Non-ASCII character found: {repr(char)}"

    def test_monitor_status_with_health_data(self, ops_db):
        """Shows health components when data exists."""
        db_path, storage = ops_db
        storage.log_health("database", "healthy", latency_ms=5.0)
        storage.log_health("database", "degraded", latency_ms=100.0)

        result = _run_cli(db_path, "monitor", "status")
        assert result.returncode == 0
        assert "database" in result.stdout


class TestMonitorAlerts:
    def test_monitor_alerts_no_alerts(self, ops_db):
        """monitor alerts on clean DB shows no alerts."""
        db_path, storage = ops_db
        result = _run_cli(db_path, "monitor", "alerts")
        assert result.returncode == 0
        assert "alert" in result.stdout.lower() or "no alerts" in result.stdout.lower()

    def test_monitor_alerts_with_alerts(self, ops_db):
        """monitor alerts shows fired alerts."""
        db_path, storage = ops_db
        # Create conditions that trigger an alert (health below 70%)
        for _ in range(5):
            storage.log_health("api", "unhealthy", latency_ms=500.0)

        result = _run_cli(db_path, "monitor", "alerts")
        assert result.returncode == 0
        # Should mention the alert
        assert "health" in result.stdout.lower() or "CRIT" in result.stdout

    def test_monitor_alerts_send_flag(self, ops_db):
        """--send flag should be accepted (graceful without webhook)."""
        db_path, storage = ops_db
        result = _run_cli(db_path, "monitor", "alerts", "--send")
        assert result.returncode == 0


class TestMonitorHistory:
    def test_monitor_history_empty(self, ops_db):
        """monitor history on clean DB."""
        db_path, storage = ops_db
        result = _run_cli(db_path, "monitor", "history")
        assert result.returncode == 0
        assert "EXTRACTION HISTORY" in result.stdout or "No extraction" in result.stdout

    def test_monitor_history_with_data(self, ops_db):
        """monitor history shows extraction data."""
        db_path, storage = ops_db
        with storage.transaction() as conn:
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now'), 10, 3, 1, 5.5, 0.25)"""
            )

        result = _run_cli(db_path, "monitor", "history")
        assert result.returncode == 0

    def test_monitor_history_days_flag(self, ops_db):
        """--days flag should be accepted."""
        db_path, storage = ops_db
        result = _run_cli(db_path, "monitor", "history", "--days", "3")
        assert result.returncode == 0
