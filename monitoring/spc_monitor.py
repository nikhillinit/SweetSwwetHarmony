"""SPC-Lite Statistical Process Control Monitor (Wave 5).

Detects out-of-control conditions in quality metrics using control charts
with safety constraints for ratio metrics.

Design decisions:
- D5:  Clamp UCL/LCL to [0,1] for ratio metrics; one-sided for FP rate
- D19: Dual min-N gating: MIN_BASELINE_DAYS + MIN_TOTAL_SAMPLES_FOR_SPC
- D22: Validate metric names against VALID_SPC_METRICS
- D26: Wilson interval for n<30 per day, 3σ for n>=30
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_MIN_BASELINE_DAYS = 14
DEFAULT_MIN_TOTAL_SAMPLES_FOR_SPC = 100
SIGMA_ZERO_FALLBACK = 0.05  # ±5% absolute when sigma=0

VALID_SPC_METRICS = frozenset({
    "overall_fp_rate",
    "publish_fp_rate",
    "collector_volume",
    "quarantine_regret",
    "confidence_calibration_ece",
})

# Ratio metrics get clamped to [0,1]
RATIO_METRICS = frozenset({
    "overall_fp_rate",
    "publish_fp_rate",
    "quarantine_regret",
    "confidence_calibration_ece",
})

# Metrics that only alert on increase (decrease is good)
ONE_SIDED_INCREASE_METRICS = frozenset({"overall_fp_rate", "publish_fp_rate"})

# Zero-volume alerting defaults
ZERO_VOLUME_MIN_BASELINE_MEAN = 1.0


def _get_min_baseline_days() -> int:
    return int(os.environ.get("SPC_MIN_BASELINE_DAYS", str(DEFAULT_MIN_BASELINE_DAYS)))


def _get_zero_volume_alerting_enabled() -> bool:
    return os.environ.get("SPC_ZERO_VOLUME_ALERTING", "true").lower() not in (
        "false", "0", "no", "off",
    )


def _get_min_total_samples_for_spc() -> int:
    return int(os.environ.get("SPC_MIN_TOTAL_SAMPLES", str(DEFAULT_MIN_TOTAL_SAMPLES_FOR_SPC)))


@dataclass
class ControlLimits:
    """SPC control limits for a metric."""
    mean: float
    ucl: float
    lcl: float
    n_valid_days: int
    total_samples: int
    method: str  # 'wilson', '3sigma', or 'fallback'


@dataclass
class SPCAlert:
    """Alert from SPC violation."""
    metric_name: str
    segment_type: str
    segment_key: str
    alert_type: str  # 'spc_violation', 'trend_alert'
    severity: str  # 'info', 'warning', 'critical'
    current_value: float
    ucl: float
    lcl: float
    mean: float
    message: str


@dataclass
class SPCResult:
    """Result of an SPC check for one metric."""
    metric_name: str
    segment_type: str
    segment_key: str
    verdict: str  # 'in_control', 'out_of_control', 'insufficient_data'
    alerts: list[SPCAlert] = field(default_factory=list)
    limits: Optional[ControlLimits] = None
    current_value: Optional[float] = None


def _wilson_bounds(p: float, n: int, z: float = 3.0) -> tuple[float, float]:
    """Compute Wilson interval bounds for proportion p with sample size n.

    Wilson interval is valid for small binomial samples (n < 30).
    Uses z=3.0 for consistency with 3σ SPC convention.
    """
    if n == 0:
        return (0.0, 1.0)

    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator

    lcl = max(center - spread, 0.0)
    ucl = min(center + spread, 1.0)
    return (lcl, ucl)


class SPCMonitor:
    """SPC-lite monitor for quality metrics."""

    def compute_control_limits(
        self,
        conn,
        metric: str,
        segment_type: str = "overall",
        segment_key: str = "",
        lookback_days: int = 30,
    ) -> Optional[ControlLimits]:
        """Compute control limits from baseline data.

        Returns None if insufficient data (D19).
        Uses Wilson interval when per-day n < 30, 3σ when n >= 30 (D26).
        """
        if metric not in VALID_SPC_METRICS:
            raise ValueError(
                f"Invalid SPC metric: {metric!r}. "
                f"Valid metrics: {sorted(VALID_SPC_METRICS)}"
            )

        rows = conn.execute(
            """SELECT value, n FROM quality_metrics_daily
               WHERE metric_name = ? AND segment_type = ? AND segment_key = ?
                 AND value IS NOT NULL
               ORDER BY metric_date DESC
               LIMIT ?""",
            (metric, segment_type, segment_key, lookback_days),
        ).fetchall()

        n_valid_days = len(rows)
        total_samples = sum(r[1] for r in rows)

        # Dual min-N gating (D19)
        min_baseline_days = _get_min_baseline_days()
        min_total_samples = _get_min_total_samples_for_spc()
        if n_valid_days < min_baseline_days or total_samples < min_total_samples:
            return None

        values = [r[0] for r in rows]
        ns = [r[1] for r in rows]
        mean = sum(values) / len(values)

        # Determine method based on typical day sample sizes (D26)
        median_n = sorted(ns)[len(ns) // 2]

        if metric in RATIO_METRICS and median_n < 30:
            # Wilson interval for small samples
            avg_n = total_samples // n_valid_days if n_valid_days > 0 else 0
            lcl, ucl = _wilson_bounds(mean, avg_n, z=3.0)
            method = "wilson"
        else:
            # Standard 3σ
            if len(values) < 2:
                sigma = 0.0
            else:
                variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
                sigma = math.sqrt(variance)

            if sigma == 0.0:
                # Fallback for identical values (D5 constraint 3)
                logger.warning(
                    "Sigma=0 for %s/%s/%s: using ±%s fallback",
                    metric, segment_type, segment_key, SIGMA_ZERO_FALLBACK,
                )
                ucl = mean + SIGMA_ZERO_FALLBACK
                lcl = mean - SIGMA_ZERO_FALLBACK
                method = "fallback"
            else:
                ucl = mean + 3 * sigma
                lcl = mean - 3 * sigma
                method = "3sigma"

            # Clamp to [0,1] for ratio metrics (D5)
            if metric in RATIO_METRICS:
                ucl = min(ucl, 1.0)
                lcl = max(lcl, 0.0)

        return ControlLimits(
            mean=mean,
            ucl=ucl,
            lcl=lcl,
            n_valid_days=n_valid_days,
            total_samples=total_samples,
            method=method,
        )

    def check_metric(
        self,
        conn,
        metric: str,
        current_value: float,
        segment_type: str = "overall",
        segment_key: str = "",
    ) -> SPCResult:
        """Check a metric value against SPC control limits.

        Returns SPCResult with verdict and any alerts.
        One-sided for FP rate (only alert on increase, D5).
        """
        if metric not in VALID_SPC_METRICS:
            raise ValueError(
                f"Invalid SPC metric: {metric!r}. "
                f"Valid metrics: {sorted(VALID_SPC_METRICS)}"
            )

        limits = self.compute_control_limits(conn, metric, segment_type, segment_key)

        if limits is None:
            return SPCResult(
                metric_name=metric,
                segment_type=segment_type,
                segment_key=segment_key,
                verdict="insufficient_data",
                current_value=current_value,
            )

        alerts = []
        out_of_control = False

        # Check UCL
        if current_value > limits.ucl:
            severity = "critical" if current_value > limits.ucl * 1.5 else "warning"
            alerts.append(SPCAlert(
                metric_name=metric,
                segment_type=segment_type,
                segment_key=segment_key,
                alert_type="spc_violation",
                severity=severity,
                current_value=current_value,
                ucl=limits.ucl,
                lcl=limits.lcl,
                mean=limits.mean,
                message=(
                    f"{metric} = {current_value:.4f} exceeds UCL {limits.ucl:.4f} "
                    f"(mean={limits.mean:.4f}, method={limits.method})"
                ),
            ))
            out_of_control = True

        # Check LCL (skip for one-sided increase metrics, D5)
        if metric not in ONE_SIDED_INCREASE_METRICS and current_value < limits.lcl:
            severity = "critical" if current_value < limits.lcl * 0.5 else "warning"
            alerts.append(SPCAlert(
                metric_name=metric,
                segment_type=segment_type,
                segment_key=segment_key,
                alert_type="spc_violation",
                severity=severity,
                current_value=current_value,
                ucl=limits.ucl,
                lcl=limits.lcl,
                mean=limits.mean,
                message=(
                    f"{metric} = {current_value:.4f} below LCL {limits.lcl:.4f} "
                    f"(mean={limits.mean:.4f}, method={limits.method})"
                ),
            ))
            out_of_control = True

        return SPCResult(
            metric_name=metric,
            segment_type=segment_type,
            segment_key=segment_key,
            verdict="out_of_control" if out_of_control else "in_control",
            alerts=alerts,
            limits=limits,
            current_value=current_value,
        )

    def check_zero_volume(
        self,
        conn,
        segment_key: str,
        current_value: float,
        lookback_days: int = 30,
        min_baseline_mean: float = ZERO_VOLUME_MIN_BASELINE_MEAN,
    ) -> Optional[SPCAlert]:
        """Check if a collector that historically produces signals has dropped to zero.

        Returns SPCAlert (warning severity) if:
        1. Zero-volume alerting is enabled (SPC_ZERO_VOLUME_ALERTING env var)
        2. current_value == 0
        3. Baseline has >= SPC_MIN_BASELINE_DAYS days of data
        4. Baseline mean >= min_baseline_mean (collector historically active)

        Returns None otherwise.
        """
        if not _get_zero_volume_alerting_enabled():
            return None

        if current_value > 0:
            return None

        rows = conn.execute(
            """SELECT value FROM quality_metrics_daily
               WHERE metric_name = 'collector_volume'
                 AND segment_type = 'collector' AND segment_key = ?
                 AND value IS NOT NULL
               ORDER BY metric_date DESC
               LIMIT ?""",
            (segment_key, lookback_days),
        ).fetchall()

        min_baseline_days = _get_min_baseline_days()
        if len(rows) < min_baseline_days:
            return None

        values = [r[0] for r in rows]
        mean = sum(values) / len(values)

        if mean < min_baseline_mean:
            return None

        logger.warning(
            "Zero-volume detected for collector '%s' (baseline mean=%.1f over %d days)",
            segment_key, mean, len(rows),
        )

        return SPCAlert(
            metric_name="collector_volume",
            segment_type="collector",
            segment_key=segment_key,
            alert_type="spc_violation",
            severity="warning",
            current_value=0.0,
            ucl=0.0,
            lcl=0.0,
            mean=mean,
            message=(
                f"collector_volume for '{segment_key}' is 0 "
                f"(baseline mean={mean:.1f} over {len(rows)} days). "
                f"Collector may have stopped producing signals."
            ),
        )

    def detect_trends(
        self,
        conn,
        metric: str,
        segment_type: str = "overall",
        segment_key: str = "",
        window: int = 7,
    ) -> Optional[SPCAlert]:
        """Detect monotonic trends over a window.

        Returns SPCAlert if all values in window are monotonically increasing
        or decreasing, else None.
        """
        if metric not in VALID_SPC_METRICS:
            raise ValueError(f"Invalid SPC metric: {metric!r}")

        rows = conn.execute(
            """SELECT value FROM quality_metrics_daily
               WHERE metric_name = ? AND segment_type = ? AND segment_key = ?
                 AND value IS NOT NULL
               ORDER BY metric_date DESC
               LIMIT ?""",
            (metric, segment_type, segment_key, window),
        ).fetchall()

        if len(rows) < window:
            return None

        values = [r[0] for r in rows]
        # Reverse to chronological order (oldest first)
        values = list(reversed(values))

        is_increasing = all(values[i] < values[i + 1] for i in range(len(values) - 1))
        is_decreasing = all(values[i] > values[i + 1] for i in range(len(values) - 1))

        if not is_increasing and not is_decreasing:
            return None

        direction = "increasing" if is_increasing else "decreasing"

        # For one-sided metrics, only alert on increase
        if metric in ONE_SIDED_INCREASE_METRICS and is_decreasing:
            return None

        severity = "warning"
        return SPCAlert(
            metric_name=metric,
            segment_type=segment_type,
            segment_key=segment_key,
            alert_type="trend_alert",
            severity=severity,
            current_value=values[-1],
            ucl=0.0,
            lcl=0.0,
            mean=sum(values) / len(values),
            message=(
                f"{metric} has been monotonically {direction} "
                f"for {window} consecutive days ({values[0]:.4f} → {values[-1]:.4f})"
            ),
        )

    def compute_calibration_curve(
        self,
        conn,
        bins: int = 10,
    ) -> dict:
        """Compute observed vs expected calibration curve with ECE.

        Returns dict with bins, ece, and total_n.
        """
        rows = conn.execute(
            """SELECT
                CAST(ROUND(s.confidence * ?) AS INTEGER) AS bin_idx,
                COUNT(*) AS n_b,
                SUM(CASE WHEN sqm.human_label = 'TP' THEN 1 ELSE 0 END) AS tp_b,
                AVG(s.confidence) AS avg_conf_b
            FROM signals s
            JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
            WHERE sqm.human_label IN ('TP', 'FP')
            GROUP BY bin_idx
            ORDER BY bin_idx""",
            (bins,),
        ).fetchall()

        curve_bins = []
        total_n = 0
        ece = 0.0

        for bin_idx, n_b, tp_b, avg_conf in rows:
            if n_b < 3:
                continue
            acc = tp_b / n_b
            curve_bins.append({
                "bin": bin_idx / bins,
                "n": n_b,
                "accuracy": acc,
                "avg_confidence": avg_conf,
                "abs_error": abs(acc - avg_conf),
            })
            total_n += n_b

        if total_n > 0:
            ece = sum(b["n"] / total_n * b["abs_error"] for b in curve_bins)

        return {"bins": curve_bins, "ece": ece, "total_n": total_n}
