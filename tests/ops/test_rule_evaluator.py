"""Phase 5.3 tests — JSON DSL rule evaluator + AlertEngine integration.

TDD RED tests: These should all FAIL until the rule evaluator is implemented.
"""

import json
import sqlite3
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from ops.storage import OpsStorage


# ── Helper: sample snapshot dict ────────────────────────────────────────

def _sample_snapshot_dict(**overrides):
    """Return a snapshot dict matching OpsMetricsSnapshot.to_dict() shape."""
    base = {
        "timestamp": "2026-02-06T12:00:00+00:00",
        "health_summary": {
            "db": {"health_percent": 95.0, "total_checks": 10, "avg_latency_ms": 5.2},
            "api": {"health_percent": 80.0, "total_checks": 8, "avg_latency_ms": 12.1},
        },
        "overall_health_pct": 87.5,
        "last_extraction": {
            "id": 1, "run_at": "2026-02-06T11:00:00", "decisions_processed": 5,
            "facts_created": 3, "llm_failures": 1, "duration_seconds": 45.2,
            "estimated_cost": "0.05",
        },
        "extractions_24h": 3,
        "total_cost_24h": "1.25",
        "avg_extraction_duration": 42.5,
        "total_extractions_all_time": 50,
        "facts_by_status": {"active": 30, "pending": 5, "retired": 10},
        "total_facts": 45,
        "avg_fact_confidence": 0.82,
        "unused_high_confidence_facts": 4,
        "open_incidents": 1,
        "recent_incidents_24h": 2,
        "audit_entries_24h": 15,
    }
    base.update(overrides)
    return base


# ── Simple Conditions ───────────────────────────────────────────────────

class TestSimpleConditions:
    """Test field/op/value conditions against snapshot dicts."""

    def test_greater_than_true(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=5)
        cond = {"field": "open_incidents", "op": ">", "value": 3}
        assert evaluate_condition(cond, snap) is True

    def test_greater_than_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=2)
        cond = {"field": "open_incidents", "op": ">", "value": 3}
        assert evaluate_condition(cond, snap) is False

    def test_greater_equal(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=3)
        cond = {"field": "open_incidents", "op": ">=", "value": 3}
        assert evaluate_condition(cond, snap) is True

    def test_less_than(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(overall_health_pct=40.0)
        cond = {"field": "overall_health_pct", "op": "<", "value": 50}
        assert evaluate_condition(cond, snap) is True

    def test_less_equal(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(overall_health_pct=50.0)
        cond = {"field": "overall_health_pct", "op": "<=", "value": 50}
        assert evaluate_condition(cond, snap) is True

    def test_equal(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(extractions_24h=0)
        cond = {"field": "extractions_24h", "op": "==", "value": 0}
        assert evaluate_condition(cond, snap) is True

    def test_not_equal(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(extractions_24h=5)
        cond = {"field": "extractions_24h", "op": "!=", "value": 0}
        assert evaluate_condition(cond, snap) is True

    def test_unknown_operator_raises(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        cond = {"field": "open_incidents", "op": "~", "value": 1}
        with pytest.raises(ValueError, match="Unknown operator"):
            evaluate_condition(cond, snap)

    def test_missing_field_returns_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        cond = {"field": "nonexistent_field", "op": ">", "value": 0}
        assert evaluate_condition(cond, snap) is False

    def test_decimal_string_field_comparison(self):
        """total_cost_24h is serialized as string '1.25' — must compare numerically."""
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(total_cost_24h="6.50")
        cond = {"field": "total_cost_24h", "op": ">", "value": 5.0}
        assert evaluate_condition(cond, snap) is True

    def test_decimal_string_field_less_than(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(total_cost_24h="2.00")
        cond = {"field": "total_cost_24h", "op": "<", "value": 5.0}
        assert evaluate_condition(cond, snap) is True


# ── Dot-Notation Field Access ───────────────────────────────────────────

class TestDotNotation:
    def test_one_level_deep(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        cond = {"field": "health_summary.db.health_percent", "op": ">", "value": 90}
        assert evaluate_condition(cond, snap) is True

    def test_two_levels_deep(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        cond = {"field": "last_extraction.llm_failures", "op": "==", "value": 1}
        assert evaluate_condition(cond, snap) is True

    def test_missing_nested_returns_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        cond = {"field": "health_summary.nonexistent.health_percent", "op": ">", "value": 0}
        assert evaluate_condition(cond, snap) is False

    def test_facts_by_status_access(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        cond = {"field": "facts_by_status.active", "op": ">=", "value": 10}
        assert evaluate_condition(cond, snap) is True


# ── Composite Conditions ────────────────────────────────────────────────

class TestCompositeConditions:
    def test_all_both_true(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=5, total_cost_24h="6.00")
        cond = {"all": [
            {"field": "open_incidents", "op": ">", "value": 3},
            {"field": "total_cost_24h", "op": ">", "value": 5.0},
        ]}
        assert evaluate_condition(cond, snap) is True

    def test_all_one_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=1, total_cost_24h="6.00")
        cond = {"all": [
            {"field": "open_incidents", "op": ">", "value": 3},
            {"field": "total_cost_24h", "op": ">", "value": 5.0},
        ]}
        assert evaluate_condition(cond, snap) is False

    def test_any_one_true(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=1, total_cost_24h="6.00")
        cond = {"any": [
            {"field": "open_incidents", "op": ">", "value": 3},
            {"field": "total_cost_24h", "op": ">", "value": 5.0},
        ]}
        assert evaluate_condition(cond, snap) is True

    def test_any_none_true(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=1, total_cost_24h="2.00")
        cond = {"any": [
            {"field": "open_incidents", "op": ">", "value": 3},
            {"field": "total_cost_24h", "op": ">", "value": 5.0},
        ]}
        assert evaluate_condition(cond, snap) is False

    def test_not_true_becomes_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=5)
        cond = {"not": {"field": "open_incidents", "op": ">", "value": 3}}
        assert evaluate_condition(cond, snap) is False

    def test_not_false_becomes_true(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=1)
        cond = {"not": {"field": "open_incidents", "op": ">", "value": 3}}
        assert evaluate_condition(cond, snap) is True

    def test_nested_composite(self):
        """any( all(A, B), C ) — A+B both false, C true → overall true."""
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict(open_incidents=1, total_cost_24h="2.00", overall_health_pct=30.0)
        cond = {"any": [
            {"all": [
                {"field": "open_incidents", "op": ">", "value": 3},
                {"field": "total_cost_24h", "op": ">", "value": 5.0},
            ]},
            {"field": "overall_health_pct", "op": "<", "value": 50},
        ]}
        assert evaluate_condition(cond, snap) is True

    def test_unknown_condition_type_raises(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        snap = _sample_snapshot_dict()
        with pytest.raises(ValueError, match="Unknown condition type"):
            evaluate_condition({"bogus": True}, snap)


# ── Trend Conditions ────────────────────────────────────────────────────

class TestTrendConditions:
    def _make_history(self, field, values):
        """Create a list of snapshot dicts with the given field values.

        Values are in chronological order; returned list is newest-first
        (matching get_metric_snapshots ordering).
        """
        snaps = []
        for v in values:
            snap = _sample_snapshot_dict(**{field: v})
            snaps.append(snap)
        snaps.reverse()  # newest first
        return snaps

    def test_increasing_detected(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        history = self._make_history("total_cost_24h", ["1.00", "2.00", "3.00"])
        cond = {"trend": {"field": "total_cost_24h", "direction": "increasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), history) is True

    def test_decreasing_detected(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        history = self._make_history("overall_health_pct", [90.0, 80.0, 70.0])
        cond = {"trend": {"field": "overall_health_pct", "direction": "decreasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), history) is True

    def test_not_enough_data_returns_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        history = self._make_history("total_cost_24h", ["1.00", "2.00"])
        cond = {"trend": {"field": "total_cost_24h", "direction": "increasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), history) is False

    def test_no_history_returns_false(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        cond = {"trend": {"field": "total_cost_24h", "direction": "increasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), None) is False

    def test_flat_not_increasing(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        history = self._make_history("total_cost_24h", ["5.00", "5.00", "5.00"])
        cond = {"trend": {"field": "total_cost_24h", "direction": "increasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), history) is False

    def test_window_uses_most_recent(self):
        """With window=3, only look at last 3 snapshots even if 5 exist."""
        from ops.monitoring.rule_evaluator import evaluate_condition
        # Chronological: 1, 2, 10, 8, 9 → last 3 chronological: 10, 8, 9 (not monotonic)
        # But newest-first in history: [9, 8, 10, 2, 1]
        # Window=3 → takes [9, 8, 10] → reverse to chrono [10, 8, 9] → not increasing
        history = self._make_history("open_incidents", [1, 2, 10, 8, 9])
        cond = {"trend": {"field": "open_incidents", "direction": "increasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), history) is False

    def test_unknown_direction_raises(self):
        from ops.monitoring.rule_evaluator import evaluate_condition
        history = self._make_history("open_incidents", [1, 2, 3])
        cond = {"trend": {"field": "open_incidents", "direction": "sideways", "window": 3}}
        with pytest.raises(ValueError, match="Unknown trend direction"):
            evaluate_condition(cond, _sample_snapshot_dict(), history)

    def test_trend_with_dot_notation_field(self):
        """Trend on a nested field like health_summary.db.health_percent."""
        from ops.monitoring.rule_evaluator import evaluate_condition
        snaps = []
        for pct in [95.0, 85.0, 75.0]:
            snap = _sample_snapshot_dict()
            snap["health_summary"]["db"]["health_percent"] = pct
            snaps.append(snap)
        snaps.reverse()
        cond = {"trend": {"field": "health_summary.db.health_percent", "direction": "decreasing", "window": 3}}
        assert evaluate_condition(cond, _sample_snapshot_dict(), snaps) is True


# ── condition_to_check converter ────────────────────────────────────────

class TestConditionToCheck:
    """Test converting JSON DSL conditions to AlertRule-compatible callables."""

    def test_simple_condition_returns_callable(self):
        from ops.monitoring.rule_evaluator import condition_to_check
        from ops.monitoring.metrics import OpsMetricsSnapshot
        check_fn = condition_to_check({"field": "open_incidents", "op": ">", "value": 3})
        snap = OpsMetricsSnapshot(
            timestamp="2026-02-06T12:00:00+00:00",
            health_summary={}, overall_health_pct=100.0,
            last_extraction=None, extractions_24h=0,
            total_cost_24h=Decimal("0"), avg_extraction_duration=0,
            total_extractions_all_time=0, facts_by_status={},
            total_facts=0, avg_fact_confidence=0, unused_high_confidence_facts=0,
            open_incidents=5, recent_incidents_24h=0, audit_entries_24h=0,
        )
        assert check_fn(snap) is True

    def test_simple_condition_false(self):
        from ops.monitoring.rule_evaluator import condition_to_check
        from ops.monitoring.metrics import OpsMetricsSnapshot
        check_fn = condition_to_check({"field": "open_incidents", "op": ">", "value": 3})
        snap = OpsMetricsSnapshot(
            timestamp="2026-02-06T12:00:00+00:00",
            health_summary={}, overall_health_pct=100.0,
            last_extraction=None, extractions_24h=0,
            total_cost_24h=Decimal("0"), avg_extraction_duration=0,
            total_extractions_all_time=0, facts_by_status={},
            total_facts=0, avg_fact_confidence=0, unused_high_confidence_facts=0,
            open_incidents=1, recent_incidents_24h=0, audit_entries_24h=0,
        )
        assert check_fn(snap) is False

    def test_composite_condition_to_check(self):
        from ops.monitoring.rule_evaluator import condition_to_check
        from ops.monitoring.metrics import OpsMetricsSnapshot
        cond = {"all": [
            {"field": "open_incidents", "op": ">", "value": 0},
            {"field": "overall_health_pct", "op": "<", "value": 90},
        ]}
        check_fn = condition_to_check(cond)
        snap = OpsMetricsSnapshot(
            timestamp="2026-02-06T12:00:00+00:00",
            health_summary={}, overall_health_pct=80.0,
            last_extraction=None, extractions_24h=0,
            total_cost_24h=Decimal("0"), avg_extraction_duration=0,
            total_extractions_all_time=0, facts_by_status={},
            total_facts=0, avg_fact_confidence=0, unused_high_confidence_facts=0,
            open_incidents=2, recent_incidents_24h=0, audit_entries_24h=0,
        )
        assert check_fn(snap) is True


# ── AlertEngine Integration ─────────────────────────────────────────────

@pytest.fixture
def ops_db(tmp_path):
    """Create a standalone OpsStorage for integration tests."""
    db_path = tmp_path / "test_engine.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


class TestAlertEngineLoadCustomRules:
    """Test loading custom rules from DB and converting to AlertRule objects."""

    def test_load_custom_rules_empty(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        rules = AlertEngine.load_custom_rules(ops_db)
        assert rules == []

    def test_load_custom_rules_one_rule(self, ops_db):
        from ops.monitoring.alerts import AlertEngine, AlertRule
        ops_db.create_alert_rule(
            name="cost_high",
            condition={"field": "total_cost_24h", "op": ">", "value": 5.0},
            severity="warning",
            message_template="Cost exceeds $5",
            component="cost",
        )
        rules = AlertEngine.load_custom_rules(ops_db)
        assert len(rules) == 1
        assert isinstance(rules[0], AlertRule)
        assert rules[0].name == "cost_high"
        assert rules[0].severity == "warning"
        assert rules[0].component == "cost"

    def test_load_custom_rules_skips_disabled(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        r1 = ops_db.create_alert_rule(
            name="enabled_rule",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="warning", message_template="E",
        )
        r2 = ops_db.create_alert_rule(
            name="disabled_rule",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info", message_template="D",
        )
        ops_db.update_alert_rule(r2, enabled=False)
        rules = AlertEngine.load_custom_rules(ops_db)
        assert len(rules) == 1
        assert rules[0].name == "enabled_rule"

    def test_loaded_rule_check_is_callable(self, ops_db):
        """The loaded rule's check function must actually evaluate the condition."""
        from ops.monitoring.alerts import AlertEngine
        from ops.monitoring.metrics import OpsMetricsSnapshot
        ops_db.create_alert_rule(
            name="incident_high",
            condition={"field": "open_incidents", "op": ">", "value": 3},
            severity="critical", message_template="Too many incidents",
        )
        rules = AlertEngine.load_custom_rules(ops_db)
        snap = OpsMetricsSnapshot(
            timestamp="2026-02-06T12:00:00+00:00",
            health_summary={}, overall_health_pct=100.0,
            last_extraction=None, extractions_24h=0,
            total_cost_24h=Decimal("0"), avg_extraction_duration=0,
            total_extractions_all_time=0, facts_by_status={},
            total_facts=0, avg_fact_confidence=0, unused_high_confidence_facts=0,
            open_incidents=5, recent_incidents_24h=0, audit_entries_24h=0,
        )
        assert rules[0].check(snap) is True


class TestAlertEngineEvaluateAll:
    """Test the full evaluate_all flow: builtins + custom + audit."""

    def _make_snapshot(self, **overrides):
        from ops.monitoring.metrics import OpsMetricsSnapshot
        defaults = dict(
            timestamp="2026-02-06T12:00:00+00:00",
            health_summary={}, overall_health_pct=100.0,
            last_extraction=None, extractions_24h=0,
            total_cost_24h=Decimal("0"), avg_extraction_duration=0,
            total_extractions_all_time=0, facts_by_status={},
            total_facts=0, avg_fact_confidence=0, unused_high_confidence_facts=0,
            open_incidents=0, recent_incidents_24h=0, audit_entries_24h=0,
        )
        defaults.update(overrides)
        return OpsMetricsSnapshot(**defaults)

    def test_evaluate_all_returns_alerts(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        ops_db.create_alert_rule(
            name="custom_cost",
            condition={"field": "total_cost_24h", "op": ">", "value": 5.0},
            severity="warning",
            message_template="Cost too high",
        )
        engine = AlertEngine()
        snap = self._make_snapshot(total_cost_24h=Decimal("10.00"))
        alerts = engine.evaluate_all(snap, ops_db)
        custom_alerts = [a for a in alerts if a.rule_name == "custom_cost"]
        assert len(custom_alerts) == 1

    def test_evaluate_all_records_audit_trail(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        ops_db.create_alert_rule(
            name="audit_test_rule",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="warning",
            message_template="Incidents detected",
        )
        engine = AlertEngine()
        snap = self._make_snapshot(open_incidents=3)
        alerts = engine.evaluate_all(snap, ops_db)
        # Check that an evaluation was recorded
        evals = ops_db.get_alert_evaluations(rule_name="audit_test_rule")
        assert len(evals) >= 1
        assert evals[0]["severity"] == "warning"

    def test_evaluate_all_includes_builtins(self, ops_db):
        """Builtin rules still fire alongside custom rules."""
        from ops.monitoring.alerts import AlertEngine
        engine = AlertEngine()
        snap = self._make_snapshot(open_incidents=5)
        alerts = engine.evaluate_all(snap, ops_db)
        builtin_names = [a.rule_name for a in alerts]
        assert "open_incidents" in builtin_names

    def test_evaluate_all_persists_snapshot(self, ops_db):
        """evaluate_all should persist the snapshot and link evaluations."""
        from ops.monitoring.alerts import AlertEngine
        ops_db.create_alert_rule(
            name="snap_test",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info",
            message_template="Test",
        )
        engine = AlertEngine()
        snap = self._make_snapshot(open_incidents=1)
        engine.evaluate_all(snap, ops_db)
        # Should have saved a metric snapshot
        snaps = ops_db.get_metric_snapshots(hours=24)
        assert len(snaps) >= 1

    def test_evaluate_all_no_custom_rules_still_works(self, ops_db):
        """With no custom rules in DB, builtins still work fine."""
        from ops.monitoring.alerts import AlertEngine
        engine = AlertEngine()
        snap = self._make_snapshot(open_incidents=5)
        alerts = engine.evaluate_all(snap, ops_db)
        assert any(a.rule_name == "open_incidents" for a in alerts)

    def test_backward_compat_evaluate_without_storage(self):
        """Original evaluate() method still works without storage."""
        from ops.monitoring.alerts import AlertEngine
        engine = AlertEngine()
        snap = self._make_snapshot(open_incidents=5)
        alerts = engine.evaluate(snap)
        assert any(a.rule_name == "open_incidents" for a in alerts)


# ── Scheduler-Aware Metrics Enrichment ──────────────────────────────────

class TestSchedulerMetrics:
    """Test that scheduler metrics are collected and available to rules."""

    def test_collect_scheduler_metrics_no_schedules(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        metrics = AlertEngine.collect_scheduler_metrics(ops_db)
        assert metrics["active_schedules"] == 0
        assert metrics["missed_schedules"] == 0
        assert metrics["failed_runs_24h"] == 0

    def test_collect_scheduler_metrics_with_active_schedules(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        # Create a schedule
        with ops_db.transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_schedules (name, cron_expression, enabled) "
                "VALUES ('daily', '0 8 * * *', 1)"
            )
            conn.execute(
                "INSERT INTO pipeline_schedules (name, cron_expression, enabled) "
                "VALUES ('disabled', '0 8 * * *', 0)"
            )
        metrics = AlertEngine.collect_scheduler_metrics(ops_db)
        assert metrics["active_schedules"] == 1

    def test_collect_scheduler_metrics_with_failed_runs(self, ops_db):
        from ops.monitoring.alerts import AlertEngine
        with ops_db.transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_schedules (name, cron_expression, enabled) "
                "VALUES ('sched', '0 8 * * *', 1)"
            )
            sched_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO pipeline_run_history (schedule_id, status, started_at) "
                "VALUES (?, 'failed', datetime('now', '-1 hour'))",
                (sched_id,),
            )
            conn.execute(
                "INSERT INTO pipeline_run_history (schedule_id, status, started_at) "
                "VALUES (?, 'success', datetime('now', '-2 hours'))",
                (sched_id,),
            )
        metrics = AlertEngine.collect_scheduler_metrics(ops_db)
        assert metrics["failed_runs_24h"] == 1

    def test_evaluate_all_enriches_snapshot_with_scheduler(self, ops_db):
        """Custom rules can reference scheduler fields like failed_runs_24h."""
        from ops.monitoring.alerts import AlertEngine
        # Set up a failed run
        with ops_db.transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_schedules (name, cron_expression, enabled) "
                "VALUES ('sched', '0 8 * * *', 1)"
            )
            sched_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO pipeline_run_history (schedule_id, status, started_at) "
                "VALUES (?, 'failed', datetime('now', '-1 hour'))",
                (sched_id,),
            )
        # Create a rule that references scheduler field
        ops_db.create_alert_rule(
            name="schedule_failed",
            condition={"field": "failed_runs_24h", "op": ">", "value": 0},
            severity="critical",
            message_template="Schedule has failed runs",
            component="scheduler",
        )
        engine = AlertEngine()
        snap = self._make_snapshot()
        alerts = engine.evaluate_all(snap, ops_db)
        assert any(a.rule_name == "schedule_failed" for a in alerts)

    def _make_snapshot(self, **overrides):
        from ops.monitoring.metrics import OpsMetricsSnapshot
        defaults = dict(
            timestamp="2026-02-06T12:00:00+00:00",
            health_summary={}, overall_health_pct=100.0,
            last_extraction=None, extractions_24h=0,
            total_cost_24h=Decimal("0"), avg_extraction_duration=0,
            total_extractions_all_time=0, facts_by_status={},
            total_facts=0, avg_fact_confidence=0, unused_high_confidence_facts=0,
            open_incidents=0, recent_incidents_24h=0, audit_entries_24h=0,
        )
        defaults.update(overrides)
        return OpsMetricsSnapshot(**defaults)
