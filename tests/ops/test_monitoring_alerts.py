"""Phase 2.2 — Alert rules engine tests."""

import pytest
from decimal import Decimal

from ops.monitoring.metrics import OpsMetricsSnapshot
from ops.monitoring.alerts import AlertEngine, AlertRule, Alert


def _make_snapshot(**overrides) -> OpsMetricsSnapshot:
    """Helper to create a snapshot with defaults (all healthy)."""
    defaults = dict(
        timestamp="2026-02-05T00:00:00+00:00",
        health_summary={},
        overall_health_pct=100.0,
        last_extraction=None,
        extractions_24h=1,
        total_cost_24h=Decimal("0.10"),
        avg_extraction_duration=3.0,
        total_extractions_all_time=5,
        facts_by_status={"active": 10, "pending": 2},
        total_facts=12,
        avg_fact_confidence=0.85,
        unused_high_confidence_facts=2,
        open_incidents=0,
        recent_incidents_24h=0,
        audit_entries_24h=3,
    )
    defaults.update(overrides)
    return OpsMetricsSnapshot(**defaults)


class TestNoAlerts:
    def test_no_alerts_healthy_snapshot(self):
        """No alerts should fire on a healthy snapshot."""
        engine = AlertEngine()
        snap = _make_snapshot()
        alerts = engine.evaluate(snap)
        assert alerts == []


class TestHealthAlerts:
    def test_health_degraded_fires(self):
        """health_degraded fires when component <70%."""
        engine = AlertEngine()
        snap = _make_snapshot(
            health_summary={"db": {"health_percent": 65.0, "total_checks": 10, "avg_latency_ms": 5.0}}
        )
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "health_degraded" in names
        # 65% > 50%, so health_unhealthy should NOT fire
        assert "health_unhealthy" not in names


    def test_health_unhealthy_fires(self):
        """health_unhealthy fires when component <50%."""
        engine = AlertEngine()
        snap = _make_snapshot(
            health_summary={"db": {"health_percent": 40.0, "total_checks": 10, "avg_latency_ms": 5.0}}
        )
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "health_unhealthy" in names
        assert "health_degraded" in names  # 40% is also <70%


class TestExtractionAlerts:
    def test_extraction_stale_fires(self):
        """extraction_stale fires when no extraction in 24h (and there have been past ones)."""
        engine = AlertEngine()
        snap = _make_snapshot(
            extractions_24h=0,
            total_extractions_all_time=10,
        )
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "extraction_stale" in names

    def test_extraction_stale_suppressed_on_fresh_install(self):
        """extraction_stale suppressed when total_extractions_all_time=0."""
        engine = AlertEngine()
        snap = _make_snapshot(
            extractions_24h=0,
            total_extractions_all_time=0,
        )
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "extraction_stale" not in names


class TestCostAlerts:
    def test_cost_spike_fires_with_decimal(self):
        """cost_spike fires when total_cost_24h > threshold."""
        engine = AlertEngine()
        snap = _make_snapshot(total_cost_24h=Decimal("10.50"))
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "cost_spike" in names

    def test_custom_threshold_via_env(self, monkeypatch):
        """OPS_ALERT_COST_THRESHOLD env var changes threshold."""
        monkeypatch.setenv("OPS_ALERT_COST_THRESHOLD", "1.00")
        engine = AlertEngine()  # Re-creates default rules with new threshold
        snap = _make_snapshot(total_cost_24h=Decimal("2.00"))
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "cost_spike" in names


class TestFactAlerts:
    def test_no_active_facts_suppressed_on_fresh_install(self):
        """no_active_facts suppressed when total_extractions_all_time=0."""
        engine = AlertEngine()
        snap = _make_snapshot(
            total_extractions_all_time=0,
            facts_by_status={},
            total_facts=0,
        )
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "no_active_facts" not in names

    def test_no_active_facts_fires_after_extractions(self):
        """no_active_facts fires when there have been extractions but 0 active."""
        engine = AlertEngine()
        snap = _make_snapshot(
            total_extractions_all_time=5,
            facts_by_status={"pending": 3},
        )
        alerts = engine.evaluate(snap)
        names = [a.rule_name for a in alerts]
        assert "no_active_facts" in names


class TestMultipleAlerts:
    def test_multiple_alerts_fire(self):
        """Multiple rules can fire simultaneously."""
        engine = AlertEngine()
        snap = _make_snapshot(
            health_summary={"db": {"health_percent": 30.0, "total_checks": 10, "avg_latency_ms": 5.0}},
            total_cost_24h=Decimal("20.00"),
            open_incidents=5,
        )
        alerts = engine.evaluate(snap)
        assert len(alerts) >= 3
        names = [a.rule_name for a in alerts]
        assert "health_degraded" in names
        assert "cost_spike" in names
        assert "open_incidents" in names

    def test_severity_ordering(self):
        """Alerts sorted: critical > warning > info."""
        engine = AlertEngine()
        snap = _make_snapshot(
            health_summary={"db": {"health_percent": 30.0, "total_checks": 10, "avg_latency_ms": 5.0}},
            total_cost_24h=Decimal("20.00"),
            unused_high_confidence_facts=15,
        )
        alerts = engine.evaluate(snap)
        severities = [a.severity for a in alerts]
        # All critical should come before warning, warning before info
        for i in range(len(severities) - 1):
            assert _severity_rank(severities[i]) <= _severity_rank(severities[i + 1])


class TestFingerprint:
    def test_alert_fingerprint_stable(self):
        """Fingerprint should be deterministic and stable."""
        rule = AlertRule(
            name="test_rule",
            severity="warning",
            check=lambda s: True,
            message_template="test",
            component="db",
        )
        assert rule.fingerprint == "test_rule:warning:db"

    def test_alert_fingerprint_global(self):
        """Fingerprint without component uses 'global'."""
        rule = AlertRule(
            name="test_rule",
            severity="critical",
            check=lambda s: True,
            message_template="test",
        )
        assert rule.fingerprint == "test_rule:critical:global"


def _severity_rank(s: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(s, 99)
