"""
Drift Detector

Detects quality regressions in extraction and matching:
- Threshold-based alerting
- Consecutive run detection (confidence collapse)
- Slack integration for red alerts

Sprint 6: Evaluation & Calibration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from utils.slack_notifier import SlackNotifier

logger = logging.getLogger(__name__)


# =============================================================================
# DRIFT THRESHOLDS
# =============================================================================

@dataclass
class ThresholdConfig:
    """Configuration for a drift threshold."""
    metric: str
    comparison: str  # vs_baseline, absolute, absolute_min
    threshold: float
    severity: str  # red, yellow
    consecutive_runs: int = 1  # Number of consecutive failures needed
    description: str = ""


# Default thresholds from consensus document
DEFAULT_THRESHOLDS: Dict[str, ThresholdConfig] = {
    # Extraction metrics
    "extraction_f1_drop": ThresholdConfig(
        metric="extraction_f1",
        comparison="vs_baseline",
        threshold=5.0,
        severity="red",
        description="F1 dropped more than 5 points vs baseline",
    ),
    "abstention_rate_spike": ThresholdConfig(
        metric="abstention_rate",
        comparison="absolute",
        threshold=25.0,
        severity="red",
        description="Abstention rate exceeds 25%",
    ),
    "abstention_rate_increase": ThresholdConfig(
        metric="abstention_rate",
        comparison="vs_baseline",
        threshold=8.0,
        severity="yellow",
        description="Abstention rate increased more than 8 points",
    ),
    # Similarity metrics
    "top10_recall_drop": ThresholdConfig(
        metric="top10_recall",
        comparison="vs_baseline",
        threshold=7.0,
        severity="red",
        description="Top-10 recall dropped more than 7 points",
    ),
    "top10_recall_absolute": ThresholdConfig(
        metric="top10_recall",
        comparison="absolute_min",
        threshold=60.0,
        severity="red",
        description="Top-10 recall below 60%",
    ),
    # Confidence metrics
    "confidence_collapse": ThresholdConfig(
        metric="median_confidence",
        comparison="absolute_min",
        threshold=55.0,
        severity="red",
        consecutive_runs=3,
        description="Median confidence below 55% for 3 consecutive runs",
    ),
    # Investor match metrics
    "investor_precision_drop": ThresholdConfig(
        metric="mean_precision_at_5",
        comparison="vs_baseline",
        threshold=10.0,
        severity="yellow",
        description="Investor match precision dropped more than 10 points",
    ),
}


# =============================================================================
# DRIFT ALERT
# =============================================================================

@dataclass
class DriftAlert:
    """A drift alert."""
    alert_type: str
    severity: str  # red, yellow
    metric_name: str
    baseline_value: float
    current_value: float
    threshold: float
    message: str
    evaluation_run_id: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "metric_name": self.metric_name,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "message": self.message,
            "evaluation_run_id": self.evaluation_run_id,
            "created_at": self.created_at,
        }


@dataclass
class DriftCheckResult:
    """Result of a drift check."""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    alerts: List[DriftAlert] = field(default_factory=list)
    red_count: int = 0
    yellow_count: int = 0

    @property
    def has_red_alerts(self) -> bool:
        """Check if there are any red alerts."""
        return self.red_count > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "checked_at": self.checked_at,
            "alerts": [a.to_dict() for a in self.alerts],
            "red_count": self.red_count,
            "yellow_count": self.yellow_count,
        }


# =============================================================================
# DRIFT DETECTOR
# =============================================================================

class DriftDetector:
    """
    Detects quality regressions in extraction and matching.

    Threshold types:
    - vs_baseline: Compare current vs baseline (e.g., F1 drop)
    - absolute: Check against absolute threshold (e.g., abstention > 25%)
    - absolute_min: Check minimum threshold (e.g., recall < 60%)

    Consecutive runs:
    - Some thresholds require multiple consecutive failures
    - Used for noisy metrics like confidence
    """

    def __init__(
        self,
        store: "SignalStore",
        thresholds: Optional[Dict[str, ThresholdConfig]] = None,
        slack_notifier: Optional["SlackNotifier"] = None,
    ):
        """
        Initialize drift detector.

        Args:
            store: SignalStore for database access
            thresholds: Custom thresholds (default: DEFAULT_THRESHOLDS)
            slack_notifier: Optional Slack notifier for red alerts
        """
        self._store = store
        self._thresholds = thresholds or DEFAULT_THRESHOLDS
        self._slack = slack_notifier

    async def check_drift(
        self,
        current_metrics: Dict[str, float],
        baseline_metrics: Optional[Dict[str, float]] = None,
        evaluation_run_id: Optional[int] = None,
        save_alerts: bool = True,
        notify_slack: bool = True,
    ) -> DriftCheckResult:
        """
        Check metrics against thresholds and generate alerts.

        Args:
            current_metrics: Current metric values
            baseline_metrics: Baseline metric values (for vs_baseline checks)
            evaluation_run_id: Optional evaluation run to link alerts to
            save_alerts: Whether to save alerts to database
            notify_slack: Whether to send Slack notifications for red alerts

        Returns:
            DriftCheckResult with any triggered alerts
        """
        result = DriftCheckResult()

        for alert_type, config in self._thresholds.items():
            # Get current value
            current = current_metrics.get(config.metric)
            if current is None:
                continue

            # Get baseline value
            baseline = 0.0
            if baseline_metrics and config.comparison == "vs_baseline":
                baseline = baseline_metrics.get(config.metric, 0.0)

            # Check threshold
            is_alert, message = self._check_threshold(
                current=current,
                baseline=baseline,
                config=config,
            )

            if not is_alert:
                continue

            # Check consecutive runs if required
            if config.consecutive_runs > 1:
                consecutive = await self._count_consecutive_failures(
                    alert_type=alert_type,
                    metric_name=config.metric,
                )
                if consecutive + 1 < config.consecutive_runs:
                    logger.debug(
                        f"Threshold {alert_type} triggered ({consecutive + 1}/{config.consecutive_runs})"
                    )
                    continue

            # Create alert
            alert = DriftAlert(
                alert_type=alert_type,
                severity=config.severity,
                metric_name=config.metric,
                baseline_value=baseline,
                current_value=current,
                threshold=config.threshold,
                message=message,
                evaluation_run_id=evaluation_run_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            result.alerts.append(alert)

            if config.severity == "red":
                result.red_count += 1
            else:
                result.yellow_count += 1

            # Save to database
            if save_alerts:
                await self._store.save_drift_alert(
                    alert_type=alert_type,
                    severity=config.severity,
                    metric_name=config.metric,
                    baseline_value=baseline,
                    current_value=current,
                    threshold=config.threshold,
                    evaluation_run_id=evaluation_run_id,
                )

            logger.warning(
                f"Drift alert [{config.severity.upper()}]: {alert_type} - {message}"
            )

        # Send Slack notification for red alerts
        if notify_slack and result.has_red_alerts and self._slack:
            await self._notify_slack(result)

        return result

    def _check_threshold(
        self,
        current: float,
        baseline: float,
        config: ThresholdConfig,
    ) -> tuple[bool, str]:
        """
        Check if metric breaches threshold.

        Returns:
            Tuple of (is_alert, message)
        """
        if config.comparison == "vs_baseline":
            # Check if current dropped vs baseline
            diff = baseline - current
            if diff > config.threshold:
                return True, f"{config.metric} dropped {diff:.1f} points (baseline: {baseline:.1f}, current: {current:.1f})"

        elif config.comparison == "absolute":
            # Check if current exceeds threshold
            if current > config.threshold:
                return True, f"{config.metric} at {current:.1f}% exceeds {config.threshold}% threshold"

        elif config.comparison == "absolute_min":
            # Check if current is below minimum
            if current < config.threshold:
                return True, f"{config.metric} at {current:.1f}% below {config.threshold}% minimum"

        return False, ""

    async def _count_consecutive_failures(
        self,
        alert_type: str,
        metric_name: str,
    ) -> int:
        """
        Count consecutive unacknowledged alerts of the same type.

        Returns:
            Number of consecutive failures
        """
        if not self._store._db:
            return 0

        cursor = await self._store._db.execute(
            """
            SELECT COUNT(*) FROM drift_alerts
            WHERE alert_type = ? AND metric_name = ? AND acknowledged = 0
            ORDER BY created_at DESC
            """,
            (alert_type, metric_name),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def _notify_slack(self, result: DriftCheckResult) -> None:
        """Send Slack notification for red alerts."""
        if not self._slack or not self._slack.is_configured:
            return

        try:
            red_alerts = [a for a in result.alerts if a.severity == "red"]

            message = f"*Drift Detection Alert*\n{result.red_count} RED alert(s) detected:\n"
            for alert in red_alerts[:5]:  # Limit to 5
                message += f"- [{alert.alert_type}] {alert.message}\n"

            await self._slack.send_message(message)
            logger.info(f"Sent Slack notification for {result.red_count} drift alerts")

        except Exception as e:
            logger.warning(f"Failed to send Slack notification: {e}")

    async def get_baseline_metrics(
        self,
        run_type: str,
        lookback_days: int = 30,
    ) -> Optional[Dict[str, float]]:
        """
        Get baseline metrics from recent evaluation runs.

        Args:
            run_type: Type of evaluation (extraction, similarity, investor_match)
            lookback_days: Number of days to look back

        Returns:
            Baseline metrics dict, or None if no history
        """
        if not self._store._db:
            return None

        cursor = await self._store._db.execute(
            """
            SELECT metrics FROM evaluation_runs
            WHERE run_type = ?
              AND created_at > datetime('now', ? || ' days')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (run_type, f"-{lookback_days}"),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        import json
        metrics_data = json.loads(row[0])

        # Flatten nested metrics with category prefix
        flat_metrics = {}
        for category, values in metrics_data.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    # Prefix with category for unique naming (e.g., extraction_f1)
                    flat_metrics[f"{category}_{key}"] = value
            else:
                flat_metrics[category] = values

        return flat_metrics

    async def check_evaluation_drift(
        self,
        evaluation_run_id: int,
        current_metrics: Dict[str, Any],
        run_type: str,
    ) -> DriftCheckResult:
        """
        Check drift for an evaluation run.

        Gets baseline automatically and checks all relevant thresholds.

        Args:
            evaluation_run_id: The evaluation run to check
            current_metrics: Current metrics from the run
            run_type: Type of evaluation

        Returns:
            DriftCheckResult
        """
        # Flatten current metrics if nested (with category prefix)
        flat_current = {}
        for category, values in current_metrics.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    # Prefix with category for unique naming (e.g., extraction_f1)
                    flat_current[f"{category}_{key}"] = value
            else:
                flat_current[category] = values

        # Get baseline
        baseline = await self.get_baseline_metrics(run_type)

        # Check drift
        return await self.check_drift(
            current_metrics=flat_current,
            baseline_metrics=baseline,
            evaluation_run_id=evaluation_run_id,
        )
