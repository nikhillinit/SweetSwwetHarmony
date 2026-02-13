"""
Drift Detector — Version-compatible canary drift analysis.

Compares current canary run vs previous baseline with same golden_set_hash
and config_hash. Generates drift alerts for:
- pass_rate_drop (critical if >15%, warning if >5%)
- individual_drift (per-signal regression)
- archetype_regression (per-stratum regression)
- pass_rate_improvement (info)
- archetype_improvement (info)

When no compatible baseline exists: verdict='no_baseline', zero alerts.
Minimum 3 signals per stratum before firing stratified alerts.

Wave 5 additions:
- drift_category classification (D4-style: concept/model/data drift)
- signature_key for alert dedup (D15, D18)
- DB-enforced dedup with IntegrityError fallback (D23)
- Correlation ID cap at 25 entries (D24)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Minimum signals per stratum for stratified alerts
MIN_STRATUM_SIZE = 3

# Maximum correlation IDs to store per alert (D24)
MAX_CORRELATION_IDS = 25

# Drift category mapping
_DRIFT_CATEGORY_MAP = {
    "pass_rate_drop": "concept_drift",
    "pass_rate_improvement": "concept_drift",
    "individual_drift": "model_drift",
    "archetype_regression": "data_drift",
    "archetype_improvement": "data_drift",
    "spc_violation": "concept_drift",
    "trend_alert": "concept_drift",
    "calibration_drift": "model_drift",
}


def compute_signature_key(
    alert_type: str,
    drift_category: Optional[str],
    metric_name: str,
    segment_type: str = "overall",
    segment_key: str = "",
) -> str:
    """Compute deterministic dedup signature (D15).

    Includes segment dimensions to prevent collapsing distinct incidents.
    """
    parts = [
        alert_type,
        drift_category or "unknown",
        metric_name,
        segment_type or "global",
        segment_key or "global",
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cap_correlation_ids(existing_json: Optional[str], new_id: str) -> str:
    """Cap correlation IDs to last MAX_CORRELATION_IDS entries (D24)."""
    try:
        ids = json.loads(existing_json) if existing_json else []
    except (json.JSONDecodeError, TypeError):
        ids = []
    if not isinstance(ids, list):
        ids = []
    ids.append(new_id)
    # Keep only the last N
    ids = ids[-MAX_CORRELATION_IDS:]
    return json.dumps(ids)


@dataclass
class DriftAlert:
    """A drift alert generated from canary comparison."""

    alert_type: str  # pass_rate_drop, individual_drift, archetype_regression, etc.
    severity: str = "warning"  # info, warning, critical
    signal_id: Optional[int] = None
    canonical_key: Optional[str] = None
    metric_name: str = ""
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    delta: Optional[float] = None
    message: str = ""
    drift_category: Optional[str] = None
    signature_key: Optional[str] = None
    segment_type: str = "overall"
    segment_key: str = ""


@dataclass
class DriftResult:
    """Result of drift detection."""

    verdict: str = "pass"  # pass, fail, degraded, no_baseline
    alerts: List[DriftAlert] = field(default_factory=list)
    baseline_run_id: Optional[int] = None
    baseline_message: Optional[str] = None


async def detect_drift(
    store: "SignalStore",
    current_run_id: int,
    golden_set_hash: str,
    config_hash: Optional[str] = None,
    drift_threshold: float = 0.15,
    pass_rate_threshold: float = 0.80,
) -> DriftResult:
    """Compare current canary run vs compatible baseline.

    Only compares runs with same golden_set_hash and config_hash.

    Args:
        store: SignalStore for DB access.
        current_run_id: canary_runs.id of the current run.
        golden_set_hash: Hash to match compatible baselines.
        config_hash: Optional config hash to match.
        drift_threshold: Max acceptable drift (absolute).
        pass_rate_threshold: Min acceptable pass rate.

    Returns:
        DriftResult with verdict and alerts.
    """
    db = store._db

    # Fetch current run
    cursor = await db.execute(
        """
        SELECT id, pass_rate, results_json, stratification_json
        FROM canary_runs WHERE id = ?
        """,
        (current_run_id,),
    )
    current_row = await cursor.fetchone()
    if not current_row:
        return DriftResult(verdict="no_baseline", baseline_message="Current run not found")

    current_pass_rate = current_row[1]
    current_results = _safe_json(current_row[2])
    current_strat = _safe_json(current_row[3])

    # Find compatible baseline (most recent prior run with same hashes)
    conditions = ["golden_set_hash = ?", "id < ?"]
    params: list[Any] = [golden_set_hash, current_run_id]

    if config_hash:
        conditions.append("config_hash = ?")
        params.append(config_hash)

    where = " AND ".join(conditions)

    cursor = await db.execute(
        f"""
        SELECT id, pass_rate, results_json, stratification_json
        FROM canary_runs
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        params,
    )
    baseline_row = await cursor.fetchone()

    if not baseline_row:
        return DriftResult(
            verdict="no_baseline",
            baseline_message=f"No compatible baseline found for golden_set_hash={golden_set_hash}",
        )

    baseline_id = baseline_row[0]
    baseline_pass_rate = baseline_row[1]
    baseline_results = _safe_json(baseline_row[2])
    baseline_strat = _safe_json(baseline_row[3])

    result = DriftResult(baseline_run_id=baseline_id)
    alerts: List[DriftAlert] = []

    # 1. Overall pass rate comparison
    if current_pass_rate is not None and baseline_pass_rate is not None:
        rate_delta = current_pass_rate - baseline_pass_rate

        if rate_delta < -drift_threshold:
            alerts.append(DriftAlert(
                alert_type="pass_rate_drop",
                severity="critical",
                metric_name="pass_rate",
                expected_value=baseline_pass_rate,
                actual_value=current_pass_rate,
                delta=round(rate_delta, 4),
                message=f"Pass rate dropped from {baseline_pass_rate:.2%} to {current_pass_rate:.2%} "
                        f"(delta: {rate_delta:.2%})",
            ))
        elif rate_delta < -0.05:
            alerts.append(DriftAlert(
                alert_type="pass_rate_drop",
                severity="warning",
                metric_name="pass_rate",
                expected_value=baseline_pass_rate,
                actual_value=current_pass_rate,
                delta=round(rate_delta, 4),
                message=f"Pass rate decreased from {baseline_pass_rate:.2%} to {current_pass_rate:.2%}",
            ))
        elif rate_delta > 0.05:
            alerts.append(DriftAlert(
                alert_type="pass_rate_improvement",
                severity="info",
                metric_name="pass_rate",
                expected_value=baseline_pass_rate,
                actual_value=current_pass_rate,
                delta=round(rate_delta, 4),
                message=f"Pass rate improved from {baseline_pass_rate:.2%} to {current_pass_rate:.2%}",
            ))

    # 2. Individual signal drift
    if current_results and baseline_results:
        baseline_map = {
            r.get("signal_id"): r for r in baseline_results
            if isinstance(r, dict) and "signal_id" in r
        }
        for cr in current_results:
            if not isinstance(cr, dict) or "signal_id" not in cr:
                continue
            br = baseline_map.get(cr["signal_id"])
            if not br:
                continue

            current_conf = cr.get("actual_confidence")
            baseline_conf = br.get("actual_confidence")

            if current_conf is not None and baseline_conf is not None:
                delta = current_conf - baseline_conf
                if abs(delta) > drift_threshold:
                    alerts.append(DriftAlert(
                        alert_type="individual_drift",
                        severity="warning",
                        signal_id=cr["signal_id"],
                        canonical_key=cr.get("canonical_key"),
                        metric_name="confidence",
                        expected_value=baseline_conf,
                        actual_value=current_conf,
                        delta=round(delta, 4),
                        message=f"Signal {cr['signal_id']} confidence drifted by {delta:+.4f}",
                    ))

    # 3. Archetype/stratum comparison
    if current_strat and baseline_strat:
        for stratum_key, current_data in current_strat.items():
            if not isinstance(current_data, dict):
                continue

            current_count = current_data.get("count", 0)
            if current_count < MIN_STRATUM_SIZE:
                continue  # Skip small strata

            baseline_data = baseline_strat.get(stratum_key)
            if not baseline_data or not isinstance(baseline_data, dict):
                continue

            baseline_rate = baseline_data.get("pass_rate")
            current_rate = current_data.get("pass_rate")

            if baseline_rate is not None and current_rate is not None:
                delta = current_rate - baseline_rate
                if delta < -0.10:
                    alerts.append(DriftAlert(
                        alert_type="archetype_regression",
                        severity="warning",
                        metric_name=f"pass_rate:{stratum_key}",
                        expected_value=baseline_rate,
                        actual_value=current_rate,
                        delta=round(delta, 4),
                        message=f"Stratum '{stratum_key}' regressed: "
                                f"{baseline_rate:.2%} → {current_rate:.2%}",
                    ))
                elif delta > 0.10:
                    alerts.append(DriftAlert(
                        alert_type="archetype_improvement",
                        severity="info",
                        metric_name=f"pass_rate:{stratum_key}",
                        expected_value=baseline_rate,
                        actual_value=current_rate,
                        delta=round(delta, 4),
                        message=f"Stratum '{stratum_key}' improved: "
                                f"{baseline_rate:.2%} → {current_rate:.2%}",
                    ))

    result.alerts = alerts

    # Determine verdict
    critical_count = sum(1 for a in alerts if a.severity == "critical")
    warning_count = sum(1 for a in alerts if a.severity == "warning")

    if critical_count > 0:
        result.verdict = "fail"
    elif warning_count > 0:
        result.verdict = "degraded"
    else:
        result.verdict = "pass"

    return result


def _classify_drift_category(alert_type: str) -> Optional[str]:
    """Map alert_type to drift_category."""
    return _DRIFT_CATEGORY_MAP.get(alert_type)


def _enrich_alert(alert: DriftAlert) -> DriftAlert:
    """Add drift_category and signature_key to an alert."""
    if alert.drift_category is None:
        alert.drift_category = _classify_drift_category(alert.alert_type)
    if alert.signature_key is None:
        alert.signature_key = compute_signature_key(
            alert.alert_type,
            alert.drift_category,
            alert.metric_name,
            alert.segment_type,
            alert.segment_key,
        )
    return alert


async def store_drift_alerts(
    store: "SignalStore",
    canary_run_id: int,
    alerts: List[DriftAlert],
    correlation_id: Optional[str] = None,
) -> int:
    """Persist drift alerts with dedup via signature_key (D18, D23).

    Uses DB-enforced partial unique index for dedup. Falls back to
    explicit UPDATE on IntegrityError (D23).

    Returns:
        Number of alerts stored or updated.
    """
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    corr_id = correlation_id or str(canary_run_id)

    for alert in alerts:
        _enrich_alert(alert)
        initial_corr_json = json.dumps([corr_id])

        try:
            # Primary path: UPSERT with partial-index conflict target (D18)
            await db.execute(
                """INSERT INTO canary_drift_alerts (
                    canary_run_id, alert_type, severity,
                    signal_id, canonical_key, metric_name,
                    expected_value, actual_value, delta,
                    message, status, drift_category, signature_key,
                    occurrence_count, last_seen_at, correlation_ids_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, 1, ?, ?, ?)
                ON CONFLICT(signature_key) WHERE status IN ('open','snoozed')
                DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    occurrence_count = occurrence_count + 1,
                    correlation_ids_json = excluded.correlation_ids_json""",
                (
                    canary_run_id,
                    alert.alert_type,
                    alert.severity,
                    alert.signal_id,
                    alert.canonical_key,
                    alert.metric_name,
                    alert.expected_value,
                    alert.actual_value,
                    alert.delta,
                    alert.message,
                    alert.drift_category,
                    alert.signature_key,
                    now,
                    initial_corr_json,
                    now,
                ),
            )
            count += 1
        except (sqlite3.IntegrityError, Exception) as exc:
            # Fallback path (D23): explicit UPDATE for active alerts
            if "UNIQUE constraint" in str(exc) or isinstance(exc, sqlite3.IntegrityError):
                # Read existing correlation_ids, cap and update
                cursor = await db.execute(
                    "SELECT correlation_ids_json FROM canary_drift_alerts "
                    "WHERE signature_key = ? AND status IN ('open','snoozed')",
                    (alert.signature_key,),
                )
                row = await cursor.fetchone()
                capped_json = _cap_correlation_ids(
                    row[0] if row else None, corr_id
                )
                await db.execute(
                    "UPDATE canary_drift_alerts SET last_seen_at=?, "
                    "occurrence_count=occurrence_count+1, "
                    "correlation_ids_json=? "
                    "WHERE signature_key=? AND status IN ('open','snoozed')",
                    (now, capped_json, alert.signature_key),
                )
                count += 1
            else:
                raise

    await db.commit()
    return count


def _safe_json(val: Any) -> Any:
    """Parse JSON string, returning None on failure."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None
