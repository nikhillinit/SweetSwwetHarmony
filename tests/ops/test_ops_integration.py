"""Phase 6 — Cross-module integration tests for the ops layer.

Tests data flows between Scheduler, Metrics, AlertEngine, RuleEvaluator,
Notifier, and Storage using real SQLite databases (no OpsStorage mocking).
"""

import json
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from ops.storage import OpsStorage
from ops.monitoring.metrics import OpsMetricsCollector, OpsMetricsSnapshot
from ops.monitoring.alerts import AlertEngine, Alert
from ops.monitoring.notifier import OpsAlertNotifier
from ops.scheduler import PipelineScheduler, ScheduleConfig, RunStatus


@pytest.fixture
def ops_db(tmp_path):
    """Real SQLite ops database with FK constraints disabled."""
    db_path = tmp_path / "integration_test.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


def _seed_health_data(storage, component="api", healthy=3, unhealthy=2):
    """Seed system_health to produce a known health_percent."""
    for _ in range(healthy):
        storage.log_health(component, "healthy", latency_ms=100.0)
    for _ in range(unhealthy):
        storage.log_health(component, "unhealthy", latency_ms=5000.0)


# =============================================================================
# evaluate_all() Full Pipeline
# =============================================================================


class TestEvaluateAllPipeline:
    """Test evaluate_all() end-to-end with real DB: persist snapshot, enrich
    with scheduler metrics, evaluate builtins + custom rules, record audit."""

    def test_evaluate_all_persists_snapshot(self, ops_db):
        """evaluate_all() should save the snapshot to metric_snapshots."""
        _seed_health_data(ops_db)
        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        engine.evaluate_all(snap, ops_db)

        snapshots = ops_db.get_metric_snapshots(hours=1)
        assert len(snapshots) >= 1
        assert "overall_health_pct" in snapshots[0]["snapshot"]

    def test_evaluate_all_fires_custom_rule(self, ops_db):
        """Custom rule stored in DB should fire when condition is met."""
        # Seed unhealthy data so overall_health_pct < 70
        _seed_health_data(ops_db, healthy=1, unhealthy=4)

        # Create custom rule: fires when health < 70%
        ops_db.create_alert_rule(
            name="integration_health_low",
            condition={"field": "overall_health_pct", "op": "<", "value": 70},
            severity="warning",
            message_template="Integration test: health below 70%",
        )

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        custom_alerts = [a for a in alerts if a.rule_name == "integration_health_low"]
        assert len(custom_alerts) == 1
        assert custom_alerts[0].severity == "warning"

    def test_evaluate_all_records_evaluation_trail(self, ops_db):
        """Fired alerts should be recorded in alert_evaluations table."""
        _seed_health_data(ops_db, healthy=1, unhealthy=4)

        ops_db.create_alert_rule(
            name="eval_trail_test",
            condition={"field": "overall_health_pct", "op": "<", "value": 70},
            severity="critical",
            message_template="Evaluation trail test",
        )

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        evals = ops_db.get_alert_evaluations(rule_name="eval_trail_test")
        assert len(evals) >= 1
        assert evals[0]["severity"] == "critical"
        assert evals[0]["snapshot_id"] is not None

    def test_evaluate_all_builtin_and_custom_coexist(self, ops_db):
        """Both builtin and custom rules should fire in the same evaluation."""
        # Seed unhealthy data to trigger builtin health_degraded (< 70%)
        _seed_health_data(ops_db, healthy=1, unhealthy=4)

        # Custom rule that also fires on same condition
        ops_db.create_alert_rule(
            name="coexistence_test",
            condition={"field": "overall_health_pct", "op": "<", "value": 70},
            severity="info",
            message_template="Custom coexistence test",
        )

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        rule_names = {a.rule_name for a in alerts}
        # Builtin health_degraded should fire (component < 70%)
        assert "health_degraded" in rule_names or "health_unhealthy" in rule_names
        # Custom rule should also fire
        assert "coexistence_test" in rule_names


# =============================================================================
# Scheduler → Metrics → Alerts Chain
# =============================================================================


class TestSchedulerMetricsAlerts:
    """Test that scheduler run data flows into metrics and alerts."""

    def test_collect_scheduler_metrics_after_run(self, ops_db):
        """collect_scheduler_metrics() should reflect recorded run data."""
        scheduler = PipelineScheduler(ops_db)
        config = ScheduleConfig(name="integration_sched", cron_expression="0 * * * *")
        sched_id = scheduler.create_schedule(config)

        # Record a failed run (datetime objects, not strings)
        now = datetime.now(timezone.utc)
        scheduler.record_run(
            schedule_id=sched_id,
            status=RunStatus.FAILED,
            started_at=now,
            error_message="Test failure",
        )

        metrics = AlertEngine.collect_scheduler_metrics(ops_db)
        assert metrics["active_schedules"] >= 1
        assert metrics["failed_runs_24h"] >= 1

    def test_scheduler_aware_custom_rule_fires(self, ops_db):
        """Custom rule referencing failed_runs_24h fires after failed run."""
        scheduler = PipelineScheduler(ops_db)
        config = ScheduleConfig(name="sched_alert_test", cron_expression="0 2 * * *")
        sched_id = scheduler.create_schedule(config)

        now = datetime.now(timezone.utc)
        scheduler.record_run(
            schedule_id=sched_id,
            status=RunStatus.FAILED,
            started_at=now,
            error_message="Pipeline crash",
        )

        # Custom rule: fire if any failed runs in 24h
        ops_db.create_alert_rule(
            name="failed_run_alert",
            condition={"field": "failed_runs_24h", "op": ">", "value": 0},
            severity="critical",
            message_template="Pipeline has failed runs in last 24h",
        )

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        custom_alerts = [a for a in alerts if a.rule_name == "failed_run_alert"]
        assert len(custom_alerts) == 1
        assert custom_alerts[0].severity == "critical"


# =============================================================================
# Trend Rules with Real History
# =============================================================================


class TestTrendRulesRealHistory:
    """Test trend conditions using real metric_snapshots in the DB."""

    def test_trend_increasing_fires_with_enough_history(self, ops_db):
        """Trend rule fires when field is increasing across 3+ snapshots."""
        # Insert 3 prior snapshots with increasing total_cost_24h
        for cost in [1.0, 2.0, 3.0]:
            ops_db.save_metric_snapshot({
                "total_cost_24h": str(cost),
                "overall_health_pct": 90.0,
            })

        # Insert extraction_runs so the real snapshot has cost=4.0
        # (continues the increasing trend)
        with ops_db.transaction() as conn:
            conn.execute(
                """INSERT INTO extraction_runs
                   (decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (5, 3, 0, 10.0, 4.0)"""
            )

        ops_db.create_alert_rule(
            name="cost_trend_up",
            condition={
                "trend": {
                    "field": "total_cost_24h",
                    "direction": "increasing",
                    "window": 3,
                }
            },
            severity="warning",
            message_template="Cost trending upward",
        )

        _seed_health_data(ops_db, healthy=5, unhealthy=0)
        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        trend_alerts = [a for a in alerts if a.rule_name == "cost_trend_up"]
        assert len(trend_alerts) == 1

    def test_trend_does_not_fire_with_insufficient_history(self, ops_db):
        """Trend rule should NOT fire with fewer than window snapshots."""
        # Only 1 snapshot — not enough for window=3
        ops_db.save_metric_snapshot({"total_cost_24h": "5.0"})

        ops_db.create_alert_rule(
            name="cost_trend_insufficient",
            condition={
                "trend": {
                    "field": "total_cost_24h",
                    "direction": "increasing",
                    "window": 3,
                }
            },
            severity="info",
            message_template="Should not fire",
        )

        _seed_health_data(ops_db, healthy=5, unhealthy=0)
        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        trend_alerts = [a for a in alerts if a.rule_name == "cost_trend_insufficient"]
        assert len(trend_alerts) == 0


# =============================================================================
# Custom Rule Lifecycle
# =============================================================================


class TestCustomRuleLifecycle:
    """Test full lifecycle: create → evaluate → record → resolve."""

    def test_full_lifecycle(self, ops_db):
        """Create rule, evaluate, check evaluation, resolve, verify."""
        # Step 1: Create rule
        rule_id = ops_db.create_alert_rule(
            name="lifecycle_test",
            condition={"field": "open_incidents", "op": ">", "value": -1},
            severity="info",
            message_template="Lifecycle test rule",
        )
        assert rule_id > 0

        # Step 2: Evaluate — rule should fire (open_incidents >= 0 > -1)
        _seed_health_data(ops_db, healthy=5, unhealthy=0)
        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()
        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        lifecycle_alerts = [a for a in alerts if a.rule_name == "lifecycle_test"]
        assert len(lifecycle_alerts) == 1

        # Step 3: Check evaluation recorded
        evals = ops_db.get_alert_evaluations(rule_name="lifecycle_test")
        assert len(evals) >= 1
        eval_id = evals[0]["id"]
        assert evals[0]["resolved_at"] is None  # Still open

        # Step 4: Resolve
        resolved = ops_db.resolve_alert_evaluation(eval_id)
        assert resolved is True

        # Step 5: Verify resolved
        evals_after = ops_db.get_alert_evaluations(rule_name="lifecycle_test")
        assert evals_after[0]["resolved_at"] is not None

    def test_disabled_rule_skipped(self, ops_db):
        """Disabled custom rule should not fire in evaluate_all()."""
        rule_id = ops_db.create_alert_rule(
            name="disabled_test",
            condition={"field": "open_incidents", "op": ">=", "value": 0},
            severity="info",
            message_template="Should not fire when disabled",
        )

        # Disable it
        ops_db.update_alert_rule(rule_id, enabled=False)

        _seed_health_data(ops_db, healthy=5, unhealthy=0)
        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()
        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        disabled_alerts = [a for a in alerts if a.rule_name == "disabled_test"]
        assert len(disabled_alerts) == 0


# =============================================================================
# Notifier Integration
# =============================================================================


class TestNotifierIntegration:
    """Test evaluate_all() → send_alerts() → audit_log."""

    def test_evaluate_all_then_notify(self, ops_db):
        """Alerts from evaluate_all() should flow through notifier to audit_log."""
        _seed_health_data(ops_db, healthy=1, unhealthy=4)

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)
        assert len(alerts) > 0, "Should have at least one builtin alert"

        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=0)
        result = notifier.send_alerts(alerts)

        assert result["sent"] == len(alerts)
        assert result["suppressed"] == 0

        # Verify audit_log entries
        with ops_db.read_transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action_type = 'ALERT_SENT'"
            ).fetchone()
            assert row[0] == len(alerts)

    def test_notifier_dedup_after_evaluate_all(self, ops_db):
        """Second call to send_alerts within cooldown should suppress."""
        _seed_health_data(ops_db, healthy=1, unhealthy=4)

        collector = OpsMetricsCollector(ops_db)
        snap = collector.collect()

        engine = AlertEngine()
        alerts = engine.evaluate_all(snap, ops_db)

        notifier = OpsAlertNotifier(ops_db, cooldown_minutes=60)

        # First send
        result1 = notifier.send_alerts(alerts)
        assert result1["sent"] > 0

        # Second send (same alerts, within cooldown)
        result2 = notifier.send_alerts(alerts)
        assert result2["suppressed"] == len(alerts)
        assert result2["sent"] == 0
