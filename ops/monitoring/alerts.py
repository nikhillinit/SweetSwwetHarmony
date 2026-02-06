"""Alert rules engine — stateless evaluation of metrics snapshots.

Each AlertRule is a predicate on OpsMetricsSnapshot. The AlertEngine runs
all rules and returns fired Alert objects sorted by severity.
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

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
