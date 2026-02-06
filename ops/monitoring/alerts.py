"""Alert rules engine — stateless evaluation of metrics snapshots.

Each AlertRule is a predicate on OpsMetricsSnapshot. The AlertEngine runs
all rules and returns fired Alert objects sorted by severity.

Phase 5 additions:
- load_custom_rules(storage) — loads JSON DSL rules from DB as AlertRules
- collect_scheduler_metrics(storage) — gathers scheduler-aware metrics
- evaluate_all(snapshot, storage) — builtins + custom, audit trail, snapshot persist
"""

import json
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, List, Optional

from ops.monitoring.metrics import OpsMetricsSnapshot

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class AlertRule:
    """A single alert rule that checks a condition on a metrics snapshot."""
    name: str
    severity: str  # "critical" | "warning" | "info"
    check: Callable[[OpsMetricsSnapshot], bool]
    message_template: str
    component: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        """Stable key for dedup: {name}:{severity}:{component_or_global}."""
        comp = self.component or "global"
        return f"{self.name}:{self.severity}:{comp}"


@dataclass
class Alert:
    """A fired alert."""
    rule_name: str
    severity: str
    message: str
    fired_at: str
    snapshot_value: Any
    fingerprint: str


class AlertEngine:
    """Evaluates alert rules against a metrics snapshot."""

    def __init__(self, rules: Optional[list] = None):
        self.rules = rules if rules is not None else self.default_rules()

    def evaluate(self, snapshot: OpsMetricsSnapshot) -> list:
        """Run all rules, return fired alerts sorted by severity."""
        alerts = []
        for rule in self.rules:
            try:
                if rule.check(snapshot):
                    alerts.append(Alert(
                        rule_name=rule.name,
                        severity=rule.severity,
                        message=rule.message_template,
                        fired_at=datetime.now(timezone.utc).isoformat(),
                        snapshot_value=None,
                        fingerprint=rule.fingerprint,
                    ))
            except Exception as e:
                logger.warning("Alert rule %s failed: %s", rule.name, e)

        alerts.sort(key=lambda a: SEVERITY_ORDER.get(a.severity, 99))
        return alerts

    @staticmethod
    def default_rules() -> list:
        """Built-in alert rules with false-positive gating for fresh installs."""
        cost_threshold = Decimal(
            os.environ.get("OPS_ALERT_COST_THRESHOLD", "5.00")
        )

        return [
            # 1. Extraction failures (only if there have been extractions)
            AlertRule(
                name="extraction_failures",
                severity="warning",
                check=lambda s: (
                    s.total_extractions_all_time > 0
                    and s.last_extraction is not None
                    and s.last_extraction.get("llm_failures", 0) > 3
                ),
                message_template="Last extraction had >3 LLM failures",
                component="extraction",
            ),
            # 2. Extraction stale (only if there have been extractions)
            AlertRule(
                name="extraction_stale",
                severity="warning",
                check=lambda s: (
                    s.total_extractions_all_time > 0
                    and s.extractions_24h == 0
                ),
                message_template="No extraction runs in the last 24 hours",
                component="extraction",
            ),
            # 3. Health degraded
            AlertRule(
                name="health_degraded",
                severity="critical",
                check=lambda s: any(
                    v["health_percent"] < 70
                    for v in s.health_summary.values()
                ),
                message_template="Component health below 70%",
                component="health",
            ),
            # 4. Health unhealthy
            AlertRule(
                name="health_unhealthy",
                severity="critical",
                check=lambda s: any(
                    v["health_percent"] < 50
                    for v in s.health_summary.values()
                ),
                message_template="Component health below 50%",
                component="health",
            ),
            # 5. Cost spike
            AlertRule(
                name="cost_spike",
                severity="warning",
                check=lambda s: s.total_cost_24h > cost_threshold,
                message_template=f"Daily cost exceeds ${cost_threshold}",
                component="cost",
            ),
            # 6. Open incidents
            AlertRule(
                name="open_incidents",
                severity="warning",
                check=lambda s: s.open_incidents > 2,
                message_template="More than 2 open incidents",
                component="incidents",
            ),
            # 7. Unused facts (only if there have been extractions)
            AlertRule(
                name="unused_facts",
                severity="info",
                check=lambda s: (
                    s.total_extractions_all_time > 0
                    and s.unused_high_confidence_facts > 10
                ),
                message_template="More than 10 unused high-confidence facts",
                component="facts",
            ),
            # 8. No active facts (only if there have been extractions)
            AlertRule(
                name="no_active_facts",
                severity="warning",
                check=lambda s: (
                    s.total_extractions_all_time > 0
                    and s.facts_by_status.get("active", 0) == 0
                ),
                message_template="No active facts in the system",
                component="facts",
            ),
        ]

    # ── Phase 5: Custom rules from DB ───────────────────────────────

    @staticmethod
    def load_custom_rules(storage) -> List[AlertRule]:
        """Load enabled custom rules from DB and convert to AlertRule objects."""
        from ops.monitoring.rule_evaluator import condition_to_check

        db_rules = storage.list_alert_rules(enabled_only=True)
        result = []
        for row in db_rules:
            try:
                condition = json.loads(row["condition_json"])
                check_fn = condition_to_check(condition, storage=storage)
                result.append(AlertRule(
                    name=row["name"],
                    severity=row["severity"],
                    check=check_fn,
                    message_template=row["message_template"],
                    component=row.get("component"),
                ))
            except Exception as e:
                logger.warning("Failed to load custom rule %s: %s", row["name"], e)
        return result

    @staticmethod
    def collect_scheduler_metrics(storage) -> dict:
        """Collect scheduler-related metrics for rule evaluation."""
        metrics = {
            "active_schedules": 0,
            "missed_schedules": 0,
            "failed_runs_24h": 0,
        }
        try:
            with storage.read_transaction() as conn:
                # Active schedules
                row = conn.execute(
                    "SELECT COUNT(*) FROM pipeline_schedules WHERE enabled = 1"
                ).fetchone()
                metrics["active_schedules"] = row[0] if row else 0

                # Failed runs in last 24h
                row = conn.execute(
                    "SELECT COUNT(*) FROM pipeline_run_history "
                    "WHERE status = 'failed' AND started_at >= datetime('now', '-24 hours')"
                ).fetchone()
                metrics["failed_runs_24h"] = row[0] if row else 0
        except Exception as e:
            logger.warning("Failed to collect scheduler metrics: %s", e)

        return metrics

    def evaluate_all(self, snapshot: OpsMetricsSnapshot, storage) -> List[Alert]:
        """Evaluate builtins + custom rules, persist snapshot, record audit trail.

        This is the primary entry point for Phase 5 rule evaluation.
        The original evaluate() method remains for backward compatibility.
        """
        from ops.monitoring.rule_evaluator import evaluate_condition

        # 1. Persist the snapshot
        snapshot_dict = snapshot.to_dict()
        snapshot_id = storage.save_metric_snapshot(snapshot_dict)

        # 2. Enrich snapshot dict with scheduler metrics
        sched_metrics = self.collect_scheduler_metrics(storage)
        enriched = {**snapshot_dict, **sched_metrics}

        # 3. Load custom rule conditions from DB (single query)
        db_rules = storage.list_alert_rules(enabled_only=True)
        conditions_by_name = {}
        for row in db_rules:
            try:
                conditions_by_name[row["name"]] = json.loads(row["condition_json"])
            except Exception as e:
                logger.warning("Bad condition JSON for rule %s: %s", row["name"], e)

        # 4. Fetch history for trend rules
        history_rows = storage.get_metric_snapshots(hours=24 * 7, limit=20)
        history = [row["snapshot"] for row in history_rows]

        # 5. Evaluate builtin rules (original evaluate)
        alerts = self.evaluate(snapshot)

        # 6. Evaluate custom rules against enriched snapshot
        custom_rules = self.load_custom_rules(storage)
        for rule in custom_rules:
            try:
                condition = conditions_by_name.get(rule.name)
                if condition is not None:
                    fired = evaluate_condition(condition, enriched, history)
                else:
                    fired = rule.check(snapshot)

                if fired:
                    alerts.append(Alert(
                        rule_name=rule.name,
                        severity=rule.severity,
                        message=rule.message_template,
                        fired_at=datetime.now(timezone.utc).isoformat(),
                        snapshot_value=None,
                        fingerprint=rule.fingerprint,
                    ))
            except Exception as e:
                logger.warning("Custom rule %s failed: %s", rule.name, e)

        # 7. Record audit trail for all fired alerts
        for alert in alerts:
            try:
                storage.record_alert_evaluation(
                    rule_name=alert.rule_name,
                    fingerprint=alert.fingerprint,
                    severity=alert.severity,
                    message=alert.message,
                    snapshot_id=snapshot_id,
                )
            except Exception as e:
                logger.warning("Failed to record evaluation for %s: %s", alert.rule_name, e)

        # 8. Sort by severity
        alerts.sort(key=lambda a: SEVERITY_ORDER.get(a.severity, 99))
        return alerts
