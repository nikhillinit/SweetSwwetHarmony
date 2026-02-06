"""Phase 2.5 — End-to-end monitoring pipeline + notifier dedup tests."""

import json
import time
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch

from ops.storage import OpsStorage
from ops.monitoring.metrics import OpsMetricsCollector, OpsMetricsSnapshot
from ops.monitoring.alerts import AlertEngine, Alert
from ops.monitoring.notifier import OpsAlertNotifier


@pytest.fixture
def ops_db(tmp_path):
    db_path = tmp_path / "test_e2e.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


def _make_alert(rule_name="test_rule", severity="warning",
                fingerprint=None) -> Alert:
    return Alert(
        rule_name=rule_name,
        severity=severity,
        message=f"Test alert: {rule_name}",
        fired_at="2026-02-05T00:00:00+00:00",
        snapshot_value=None,
        fingerprint=fingerprint or f"{rule_name}:{severity}:global",
    )


class TestFullPipeline:
    def test_metrics_to_alerts_to_notification_flow(self, ops_db):
        """Full pipeline: collect -> evaluate -> notify."""
        # Create conditions for an alert
        for _ in range(5):
            ops_db.log_health("api", "unhealthy", latency_ms=500.0)

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate(snap)

        assert len(alerts) > 0, "Should have at least one alert"

        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=60)
        result = notifier.send_alerts(alerts)

        # Without webhook, sends are recorded as "success"
        assert result["sent"] + result["suppressed"] + result["failed"] == len(alerts)


class TestNotifierDedup:
    def test_notifier_suppresses_duplicate_within_cooldown(self, ops_db):
        """Same alert sent twice within cooldown should be suppressed."""
        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=60)
        alert = _make_alert()

        # First send
        result1 = notifier.send_alerts([alert])
        assert result1["sent"] == 1

        # Second send (within cooldown)
        result2 = notifier.send_alerts([alert])
        assert result2["suppressed"] == 1
        assert result2["sent"] == 0

    def test_notifier_sends_after_cooldown_expires(self, ops_db):
        """After cooldown, the same alert should be sent again."""
        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=0)  # 0 min = no cooldown
        alert = _make_alert()

        result1 = notifier.send_alerts([alert])
        assert result1["sent"] == 1

        # With 0 cooldown, should send again immediately
        result2 = notifier.send_alerts([alert])
        assert result2["sent"] == 1

    def test_notifier_resolved_notification(self, ops_db, monkeypatch):
        """Resolved notification when alert clears."""
        monkeypatch.setenv("OPS_ALERT_NOTIFY_RESOLVED", "true")
        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=60)

        alert = _make_alert()

        # First: fire alert
        result1 = notifier.send_alerts([alert])
        assert result1["sent"] == 1

        # Second: alert clears, pass previous alerts
        result2 = notifier.send_alerts([], previous_alerts=[alert])
        # Should have sent a resolved notification (check audit log)
        with ops_db.pool.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE operation = 'ALERT_RESOLVED'"
            ).fetchone()
            assert row[0] >= 1

    def test_notifier_degrades_without_webhook(self, ops_db):
        """Without Slack webhook, notifier records audit but doesn't crash."""
        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=60)
        alert = _make_alert()

        # Should not raise
        result = notifier.send_alerts([alert])
        assert result["sent"] == 1  # Treated as success

        # Verify audit was recorded
        with ops_db.pool.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE operation = 'ALERT_SENT'"
            ).fetchone()
            assert row[0] == 1

    def test_notifier_retry_on_slack_failure(self, ops_db):
        """Slack failures trigger retry with backoff."""
        mock_slack = MagicMock()
        mock_slack.is_configured = True
        # Always fail
        mock_slack.notify_text = AsyncMock(return_value=False)

        notifier = OpsAlertNotifier(ops_db, slack_notifier=mock_slack, cooldown_minutes=0)
        alert = _make_alert()

        # Patch time.sleep to avoid actual delays
        with patch("ops.monitoring.notifier.time.sleep"):
            result = notifier.send_alerts([alert])

        # After 3 retries, should be marked as failed
        assert result["failed"] == 1

        # Verify ALERT_NOTIFY_FAILED was recorded
        with ops_db.pool.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE operation = 'ALERT_NOTIFY_FAILED'"
            ).fetchone()
            assert row[0] == 1


class TestSanitizeMessage:
    def test_sanitize_message_strips_sensitive_data(self):
        """Emails should be redacted."""
        result = OpsAlertNotifier._sanitize_message(
            "Contact admin@example.com for details"
        )
        assert "@" not in result
        assert "[REDACTED]" in result

    def test_sanitize_message_truncates(self):
        """Long messages should be truncated."""
        long_msg = "x" * 600
        result = OpsAlertNotifier._sanitize_message(long_msg, max_length=100)
        assert len(result) <= 100
        assert result.endswith("...")
