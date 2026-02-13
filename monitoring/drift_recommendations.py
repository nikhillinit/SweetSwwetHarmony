"""Drift Recommendation Engine (Wave 5).

Generates advisory recommendations from recent drift alerts:
- Repeated archetype_regression → "Expand golden set for {archetype}"
- pass_rate_drop + collector concentration → "Investigate {collector}"
- SPC trend alert for FP rate → "Adjust MIN_CONFIDENCE threshold"
- High calibration ECE → "Recalibrate scoring model"

Recommendations are advisory only — no auto-mutation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Thresholds
ARCHETYPE_REPEAT_THRESHOLD = 3  # ≥3 archetype regressions in window
ECE_WARNING_THRESHOLD = 0.15  # ECE above this triggers recommendation


@dataclass
class DriftRecommendation:
    """Advisory recommendation from drift analysis."""

    type: str  # archetype_expand, collector_investigate, threshold_adjust, recalibrate
    priority: str  # high, medium, low
    evidence: list[int] = field(default_factory=list)  # alert IDs
    message: str = ""
    action_template: str = ""


async def generate_recommendations(
    store,
    lookback_days: int = 7,
) -> list[DriftRecommendation]:
    """Generate drift recommendations from recent alerts.

    Args:
        store: SignalStore
        lookback_days: How far back to analyze

    Returns:
        Priority-sorted list of DriftRecommendation.
    """
    db = store._db
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    recs: list[DriftRecommendation] = []

    # 1. Repeated archetype regressions
    cursor = await db.execute(
        """SELECT metric_name, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
        FROM canary_drift_alerts
        WHERE alert_type = 'archetype_regression'
          AND created_at >= ?
          AND status IN ('open', 'acknowledged', 'snoozed')
        GROUP BY metric_name
        HAVING cnt >= ?""",
        (since, ARCHETYPE_REPEAT_THRESHOLD),
    )
    for row in await cursor.fetchall():
        metric_name = row[0]
        count = row[1]
        alert_ids = [int(x) for x in row[2].split(",")]
        archetype = metric_name.split(":")[-1] if ":" in metric_name else metric_name
        recs.append(DriftRecommendation(
            type="archetype_expand",
            priority="high",
            evidence=alert_ids,
            message=f"Archetype '{archetype}' has regressed {count} times in the last {lookback_days} days",
            action_template=f"Expand golden set coverage for archetype '{archetype}'",
        ))

    # 2. Pass rate drop with collector concentration
    cursor = await db.execute(
        """SELECT id, message FROM canary_drift_alerts
        WHERE alert_type = 'pass_rate_drop'
          AND created_at >= ?
          AND status IN ('open', 'acknowledged', 'snoozed')
        ORDER BY created_at DESC LIMIT 5""",
        (since,),
    )
    pass_rate_alerts = await cursor.fetchall()
    if pass_rate_alerts:
        alert_ids = [row[0] for row in pass_rate_alerts]
        recs.append(DriftRecommendation(
            type="collector_investigate",
            priority="high",
            evidence=alert_ids,
            message=f"{len(pass_rate_alerts)} pass rate drop(s) detected in the last {lookback_days} days",
            action_template="Review collector quality and recent signal patterns",
        ))

    # 3. SPC trend alerts for FP rate
    cursor = await db.execute(
        """SELECT id FROM canary_drift_alerts
        WHERE alert_type = 'trend_alert'
          AND metric_name = 'overall_fp_rate'
          AND created_at >= ?
          AND status IN ('open', 'acknowledged', 'snoozed')""",
        (since,),
    )
    trend_alerts = await cursor.fetchall()
    if trend_alerts:
        alert_ids = [row[0] for row in trend_alerts]
        recs.append(DriftRecommendation(
            type="threshold_adjust",
            priority="medium",
            evidence=alert_ids,
            message="FP rate shows sustained upward trend",
            action_template="Consider adjusting MIN_CONFIDENCE threshold or reviewing thesis matcher rules",
        ))

    # 4. High calibration ECE
    cursor = await db.execute(
        """SELECT value FROM quality_metrics_daily
        WHERE metric_name = 'confidence_calibration_ece'
          AND segment_type = 'overall' AND segment_key = ''
          AND value IS NOT NULL
        ORDER BY metric_date DESC LIMIT 1""",
    )
    ece_row = await cursor.fetchone()
    if ece_row and ece_row[0] > ECE_WARNING_THRESHOLD:
        recs.append(DriftRecommendation(
            type="recalibrate",
            priority="medium",
            evidence=[],
            message=f"Calibration ECE = {ece_row[0]:.3f} exceeds threshold {ECE_WARNING_THRESHOLD}",
            action_template="Recalibrate confidence scoring model",
        ))

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: priority_order.get(r.priority, 99))

    return recs
