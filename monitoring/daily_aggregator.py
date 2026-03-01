"""Daily Quality Metrics Aggregator (Wave 5).

Computes and stores daily quality metrics into quality_metrics_daily:
- overall_fp_rate: FP count / labeled count per day
- collector_volume: Signal count per collector per day
- quarantine_regret: Deferred-then-approved / total-deferred per day
- confidence_calibration_ece: Population-weighted ECE per day

Design decisions:
- D9:  UTC midnight boundaries, configurable recompute window (default 7 days)
- D11: Idempotent UPSERT (ON CONFLICT DO UPDATE), not INSERT OR REPLACE
- D12: collector_volume (count), not collector_yield
- D13: Population-weighted ECE
- D17: Non-null segment normalization
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from storage.claim_fact_store import count_claim_facts_in_range_sync

logger = logging.getLogger(__name__)

# Configurable constants
SPC_RECOMPUTE_WINDOW_DAYS = int(os.environ.get("SPC_RECOMPUTE_WINDOW_DAYS", "7"))
SPC_MIN_LABELED_PER_DAY = int(os.environ.get("SPC_MIN_LABELED_PER_DAY", "10"))


def _utc_today() -> str:
    """Return today's date as YYYY-MM-DD in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _date_range(end_date: str, days: int) -> list[str]:
    """Generate YYYY-MM-DD strings from (end_date - days + 1) to end_date inclusive."""
    end = datetime.strptime(end_date, "%Y-%m-%d")
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def _upsert_metric(conn, metric_date: str, metric_name: str,
                   value: Optional[float], n: int,
                   segment_type: str = "overall", segment_key: str = "") -> None:
    """Idempotent UPSERT into quality_metrics_daily (D11)."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO quality_metrics_daily
           (metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(metric_date, metric_name, segment_type, segment_key)
           DO UPDATE SET value=excluded.value, n=excluded.n, updated_at=excluded.updated_at""",
        (metric_date, metric_name, segment_type, segment_key, value, n, now, now),
    )


# =============================================================================
# Individual metric computers
# =============================================================================

def _compute_fp_rate(conn, date: str) -> None:
    """Compute overall_fp_rate for a given UTC date.

    FP rate = fp_count / labeled_count.
    If labeled_count < MIN_LABELED_PER_DAY, store NULL value.
    """
    row = conn.execute(
        """SELECT
            COUNT(*) AS labeled,
            SUM(CASE WHEN sqm.human_label = 'FP' THEN 1 ELSE 0 END) AS fp
        FROM signals s
        JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
        WHERE sqm.human_label IN ('TP', 'FP')
          AND s.detected_at >= ? AND s.detected_at < ?""",
        (date + "T00:00:00", _next_day(date) + "T00:00:00"),
    ).fetchone()

    labeled = row[0] or 0
    fp = row[1] or 0

    if labeled < SPC_MIN_LABELED_PER_DAY:
        _upsert_metric(conn, date, "overall_fp_rate", None, labeled)
    else:
        _upsert_metric(conn, date, "overall_fp_rate", fp / labeled, labeled)


def _compute_collector_volume(conn, date: str) -> None:
    """Compute collector_volume per collector for a given UTC date (D12).

    Stores one row per collector + one 'overall' aggregate.
    Zero is valid (collector didn't run).
    """
    rows = conn.execute(
        """SELECT source_api, COUNT(*) AS cnt
        FROM signals
        WHERE detected_at >= ? AND detected_at < ?
        GROUP BY source_api""",
        (date + "T00:00:00", _next_day(date) + "T00:00:00"),
    ).fetchall()

    total = 0
    for source_api, cnt in rows:
        _upsert_metric(conn, date, "collector_volume", float(cnt), cnt,
                        segment_type="collector", segment_key=source_api)
        total += cnt

    _upsert_metric(conn, date, "collector_volume", float(total), total)


def _compute_quarantine_regret(conn, date: str) -> None:
    """Compute quarantine_regret for a given UTC date.

    quarantine_regret = deferred-then-approved / total_deferred.
    Uses audit_log to track items that were deferred then later approved.
    """
    # Count items deferred on this date
    total_deferred = conn.execute(
        """SELECT COUNT(DISTINCT entity_id)
        FROM audit_log
        WHERE action_type = 'status_transition'
          AND entity_type = 'review_item'
          AND json_extract(details, '$.after.status') = 'deferred'
          AND created_at >= ? AND created_at < ?""",
        (date + "T00:00:00", _next_day(date) + "T00:00:00"),
    ).fetchone()[0] or 0

    if total_deferred == 0:
        _upsert_metric(conn, date, "quarantine_regret", None, 0)
        return

    # Count items deferred on this date that were later approved
    deferred_then_approved = conn.execute(
        """SELECT COUNT(DISTINCT al_deferred.entity_id)
        FROM audit_log al_deferred
        WHERE al_deferred.action_type = 'status_transition'
          AND al_deferred.entity_type = 'review_item'
          AND json_extract(al_deferred.details, '$.after.status') = 'deferred'
          AND al_deferred.created_at >= ? AND al_deferred.created_at < ?
          AND EXISTS (
            SELECT 1 FROM audit_log al_approved
            WHERE al_approved.entity_id = al_deferred.entity_id
              AND al_approved.action_type = 'status_transition'
              AND al_approved.entity_type = 'review_item'
              AND json_extract(al_approved.details, '$.after.status') = 'approved'
              AND al_approved.created_at > al_deferred.created_at
          )""",
        (date + "T00:00:00", _next_day(date) + "T00:00:00"),
    ).fetchone()[0] or 0

    regret = deferred_then_approved / total_deferred
    _upsert_metric(conn, date, "quarantine_regret", regret, total_deferred)


def _compute_calibration(conn, date: str) -> None:
    """Compute population-weighted Expected Calibration Error (D13).

    ECE = Σ_b (n_b / N) * |acc_b - conf_b|
    Bins with < 3 signals excluded.
    """
    rows = conn.execute(
        """SELECT
            CAST(ROUND(s.confidence * 10) AS INTEGER) AS bin_idx,
            COUNT(*) AS n_b,
            SUM(CASE WHEN sqm.human_label = 'TP' THEN 1 ELSE 0 END) AS tp_b,
            AVG(s.confidence) AS avg_conf_b
        FROM signals s
        JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
        WHERE sqm.human_label IN ('TP', 'FP')
          AND s.detected_at >= ? AND s.detected_at < ?
        GROUP BY bin_idx""",
        (date + "T00:00:00", _next_day(date) + "T00:00:00"),
    ).fetchall()

    # Filter bins with >= 3 signals
    valid_bins = [(n_b, tp_b, avg_conf) for _, n_b, tp_b, avg_conf in rows if n_b >= 3]

    if not valid_bins:
        _upsert_metric(conn, date, "confidence_calibration_ece", None, 0)
        return

    total_n = sum(n_b for n_b, _, _ in valid_bins)
    ece = 0.0
    for n_b, tp_b, avg_conf in valid_bins:
        acc_b = tp_b / n_b
        ece += (n_b / total_n) * abs(acc_b - avg_conf)

    _upsert_metric(conn, date, "confidence_calibration_ece", ece, total_n)


def _next_day(date: str) -> str:
    """Return YYYY-MM-DD for date + 1 day."""
    d = datetime.strptime(date, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def _compute_feature_metrics(conn, date: str) -> None:
    """Compute feature-level metrics from shadow_log for a given UTC date.

    Stores per-feature metrics:
    - feature_shadow_volume: count of shadow computations

    Segment model: segment_type="feature", segment_key=<feature_name>
    """
    # ---------------------------------------------------------------------
    # 1) feature_shadow_volume (if shadow_log exists)
    # ---------------------------------------------------------------------
    shadow_exists = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='shadow_log'"
    ).fetchone()

    if shadow_exists:
        rows = conn.execute(
            """SELECT feature_name, COUNT(*) AS cnt
            FROM shadow_log
            WHERE logged_at >= ? AND logged_at < ?
            GROUP BY feature_name""",
            (date + "T00:00:00", _next_day(date) + "T00:00:00"),
        ).fetchall()

        for feature_name, cnt in rows:
            _upsert_metric(
                conn, date, "feature_shadow_volume", float(cnt), cnt,
                segment_type="feature", segment_key=feature_name,
            )

        # Overall shadow volume across all features
        total = sum(cnt for _, cnt in rows) if rows else 0
        if total > 0:
            _upsert_metric(
                conn, date, "feature_shadow_volume", float(total), total,
            )

    # ---------------------------------------------------------------------
    # 2) claim_facts_volume (via allowlisted storage helper)
    # ---------------------------------------------------------------------
    cnt = count_claim_facts_in_range_sync(
        conn, date + "T00:00:00", _next_day(date) + "T00:00:00"
    )
    _upsert_metric(conn, date, "claim_facts_volume", float(cnt), cnt)


# =============================================================================
# Public API
# =============================================================================

def aggregate_daily_metrics(conn, date: str) -> dict:
    """Compute all metrics for one UTC date, UPSERT into quality_metrics_daily.

    Args:
        conn: sqlite3.Connection (synchronous)
        date: YYYY-MM-DD string (UTC)

    Returns:
        dict with metric names and their computed values.
    """
    results = {}

    _compute_fp_rate(conn, date)
    _compute_collector_volume(conn, date)
    _compute_quarantine_regret(conn, date)
    _compute_calibration(conn, date)
    _compute_feature_metrics(conn, date)

    # Read back what we wrote for the return
    rows = conn.execute(
        "SELECT metric_name, segment_type, segment_key, value, n "
        "FROM quality_metrics_daily WHERE metric_date = ?",
        (date,),
    ).fetchall()

    for metric_name, seg_type, seg_key, value, n in rows:
        key = metric_name if seg_type == "overall" else f"{metric_name}:{seg_type}:{seg_key}"
        results[key] = {"value": value, "n": n}

    conn.commit()
    return results


def backfill_daily_metrics(conn, days: int = 90) -> dict:
    """Iterate dates and aggregate metrics, skipping today.

    Recomputes the last SPC_RECOMPUTE_WINDOW_DAYS days regardless.
    Older dates are only computed if missing.

    Args:
        conn: sqlite3.Connection
        days: How many days back to check

    Returns:
        dict with counts: {"computed": N, "skipped": N}
    """
    today = _utc_today()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    # Generate date range ending yesterday (today excluded per partial-day rule)
    dates = _date_range(yesterday, min(days, 365))

    recompute_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=SPC_RECOMPUTE_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")

    computed = 0
    skipped = 0

    for date in dates:
        # Always recompute recent dates (within recompute window)
        if date >= recompute_cutoff:
            aggregate_daily_metrics(conn, date)
            computed += 1
            continue

        # For older dates, skip if already computed
        existing = conn.execute(
            "SELECT COUNT(*) FROM quality_metrics_daily WHERE metric_date = ?",
            (date,),
        ).fetchone()[0]

        if existing > 0:
            skipped += 1
        else:
            aggregate_daily_metrics(conn, date)
            computed += 1

    return {"computed": computed, "skipped": skipped}


def check_aggregator_health(conn) -> dict:
    """Return aggregator health status.

    Returns:
        dict with last_run_date, is_stale (>48h since last metric), metric_count
    """
    row = conn.execute(
        "SELECT MAX(updated_at) FROM quality_metrics_daily"
    ).fetchone()

    last_updated = row[0] if row and row[0] else None

    is_stale = True
    if last_updated:
        try:
            last_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - last_dt
            is_stale = age > timedelta(hours=48)
        except (ValueError, TypeError):
            is_stale = True

    metric_count = conn.execute(
        "SELECT COUNT(*) FROM quality_metrics_daily"
    ).fetchone()[0]

    if is_stale and metric_count > 0:
        logger.critical(
            "Daily aggregator is stale: last update was %s (>48h ago)",
            last_updated,
        )

    return {
        "last_run_date": last_updated,
        "is_stale": is_stale,
        "metric_count": metric_count,
    }
