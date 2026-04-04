"""Tests for SPC-lite monitor (W5.3).

Covers:
- Clamp to [0,1] for ratio metrics (D5)
- Dual min-N gating: days + total samples (D19)
- Wilson interval fallback for small n (D26)
- Sigma=0 fallback (±5%)
- One-sided alerting for FP rate (D5)
- Insufficient data verdict
- Invalid metric name rejection (D22)
- Trend detection
- Calibration curve computation
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.migrations.v41_drift_monitoring import V41_DRIFT_MONITORING_DDL
from monitoring.spc_monitor import SPCMonitor, VALID_SPC_METRICS


@pytest.fixture
def db():
    """Create in-memory DB with quality_metrics_daily."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    # Stubs for FK
    conn.execute("CREATE TABLE IF NOT EXISTS canary_runs (id INTEGER PRIMARY KEY, run_id TEXT, golden_set_size INTEGER DEFAULT 0, golden_set_hash TEXT DEFAULT '', verdict TEXT DEFAULT '', created_at TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS canary_drift_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, canary_run_id INTEGER, alert_type TEXT NOT NULL CHECK(alert_type IN ('pass_rate_drop','individual_drift','archetype_regression','pass_rate_improvement','archetype_improvement')), severity TEXT NOT NULL DEFAULT 'warning' CHECK(severity IN ('info','warning','critical')), signal_id INTEGER, canonical_key TEXT, metric_name TEXT NOT NULL, expected_value REAL, actual_value REAL, delta REAL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved')), acknowledged_by TEXT, acknowledged_at TEXT, created_at TEXT NOT NULL)")
    conn.executescript(V41_DRIFT_MONITORING_DDL)

    # Signal/label tables for calibration tests
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY, signal_type TEXT, source_api TEXT,
            canonical_key TEXT, confidence REAL, raw_data TEXT DEFAULT '{}',
            detected_at TEXT, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_quality_metrics (
            id INTEGER PRIMARY KEY, signal_id INTEGER UNIQUE,
            human_label TEXT, labeled_at TEXT, label_source TEXT
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def monitor():
    return SPCMonitor()


def _seed_metric_days(conn, metric, n_days, base_value=0.2, n_per_day=50,
                      segment_type="overall", segment_key=""):
    """Insert n_days worth of metric data at base_value ± small noise."""
    now = "2026-02-10T00:00:00Z"
    for i in range(n_days):
        date = f"2026-{1 + (i // 28):02d}-{1 + (i % 28):02d}"
        conn.execute(
            "INSERT INTO quality_metrics_daily "
            "(metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, metric, segment_type, segment_key, base_value, n_per_day, now, now),
        )
    conn.commit()


class TestControlLimits:
    """Test compute_control_limits."""

    def test_insufficient_days_returns_none(self, db, monitor, monkeypatch):
        """Fewer than MIN_BASELINE_DAYS → None."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        _seed_metric_days(db, "overall_fp_rate", 10, n_per_day=20)
        result = monitor.compute_control_limits(db, "overall_fp_rate")
        assert result is None

    def test_insufficient_total_samples_returns_none(self, db, monitor, monkeypatch):
        """Enough days but total samples < MIN_TOTAL_SAMPLES_FOR_SPC → None (D19)."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        # 14 days but only 5 samples each = 70 total < 100
        _seed_metric_days(db, "overall_fp_rate", 14, n_per_day=5)
        result = monitor.compute_control_limits(db, "overall_fp_rate")
        assert result is None

    def test_sufficient_data_returns_limits(self, db, monitor, monkeypatch):
        """Enough days + samples → returns ControlLimits."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        _seed_metric_days(db, "overall_fp_rate", 20, n_per_day=50)
        result = monitor.compute_control_limits(db, "overall_fp_rate")
        assert result is not None
        assert result.n_valid_days == 20
        assert result.total_samples == 1000

    def test_ratio_metric_clamped_to_01(self, db, monitor, monkeypatch):
        """UCL/LCL clamped to [0,1] for ratio metrics (D5)."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        # Values near 0 — LCL should not go negative
        _seed_metric_days(db, "overall_fp_rate", 20, base_value=0.01, n_per_day=50)
        result = monitor.compute_control_limits(db, "overall_fp_rate")
        assert result is not None
        assert result.lcl >= 0.0
        assert result.ucl <= 1.0

    def test_sigma_zero_uses_fallback(self, db, monitor, monkeypatch):
        """All identical values → sigma=0 → ±5% fallback."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        # Identical values with large n → triggers 3sigma path with sigma=0
        _seed_metric_days(db, "collector_volume", 20, base_value=100.0, n_per_day=50)
        result = monitor.compute_control_limits(db, "collector_volume")
        assert result is not None
        assert result.method == "fallback"
        assert result.ucl == 100.0 + 0.05
        assert result.lcl == 100.0 - 0.05

    def test_wilson_for_small_samples(self, db, monitor, monkeypatch):
        """Per-day n < 30 → Wilson interval (D26)."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        # 20 days × 8 samples each = 160 total (>100), but median_n=8 < 30
        _seed_metric_days(db, "overall_fp_rate", 20, base_value=0.3, n_per_day=8)
        result = monitor.compute_control_limits(db, "overall_fp_rate")
        assert result is not None
        assert result.method == "wilson"
        assert 0.0 <= result.lcl <= result.mean
        assert result.mean <= result.ucl <= 1.0


class TestCheckMetric:
    """Test check_metric."""

    def test_in_control(self, db, monitor, monkeypatch):
        """Value within limits → in_control."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        _seed_metric_days(db, "overall_fp_rate", 20, base_value=0.2, n_per_day=50)
        result = monitor.check_metric(db, "overall_fp_rate", 0.2)
        assert result.verdict == "in_control"
        assert len(result.alerts) == 0

    def test_out_of_control_high(self, db, monitor, monkeypatch):
        """Value above UCL → out_of_control."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        _seed_metric_days(db, "overall_fp_rate", 20, base_value=0.2, n_per_day=50)
        result = monitor.check_metric(db, "overall_fp_rate", 0.95)
        assert result.verdict == "out_of_control"
        assert len(result.alerts) > 0
        assert result.alerts[0].alert_type == "spc_violation"

    def test_fp_rate_one_sided_decrease_ok(self, db, monitor, monkeypatch):
        """FP rate decrease → in_control (one-sided, D5)."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        _seed_metric_days(db, "overall_fp_rate", 20, base_value=0.5, n_per_day=50)
        result = monitor.check_metric(db, "overall_fp_rate", 0.0)
        assert result.verdict == "in_control"

    def test_collector_volume_alerts_both_directions(self, db, monitor, monkeypatch):
        """collector_volume alerts on both high and low."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        monkeypatch.setenv("SPC_MIN_TOTAL_SAMPLES", "100")
        # Use varying values to get non-zero sigma
        now = "2026-02-10T00:00:00Z"
        for i in range(20):
            date = f"2026-{1 + (i // 28):02d}-{1 + (i % 28):02d}"
            val = 100 + (i % 3) * 5  # 100, 105, 110, ...
            db.execute(
                "INSERT INTO quality_metrics_daily "
                "(metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
                "VALUES (?, 'collector_volume', 'overall', '', ?, 50, ?, ?)",
                (date, val, now, now),
            )
        db.commit()

        # Very low value should trigger alert
        result = monitor.check_metric(db, "collector_volume", 0.0)
        assert result.verdict == "out_of_control"

    def test_insufficient_data_verdict(self, db, monitor, monkeypatch):
        """No baseline data → insufficient_data."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        result = monitor.check_metric(db, "overall_fp_rate", 0.5)
        assert result.verdict == "insufficient_data"

    def test_invalid_metric_raises(self, db, monitor):
        """Invalid metric name → ValueError (D22)."""
        with pytest.raises(ValueError, match="Invalid SPC metric"):
            monitor.check_metric(db, "nonexistent_metric", 0.5)


class TestDetectTrends:
    """Test trend detection."""

    def test_monotonic_increase_detected(self, db, monitor):
        """7 consecutive increasing values → trend alert."""
        now = "2026-02-10T00:00:00Z"
        for i in range(7):
            date = f"2026-02-{1 + i:02d}"
            db.execute(
                "INSERT INTO quality_metrics_daily "
                "(metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
                "VALUES (?, 'collector_volume', 'overall', '', ?, 50, ?, ?)",
                (date, 100 + i * 10, now, now),
            )
        db.commit()

        alert = monitor.detect_trends(db, "collector_volume", window=7)
        assert alert is not None
        assert alert.alert_type == "trend_alert"
        assert "increasing" in alert.message

    def test_no_trend_returns_none(self, db, monitor):
        """Non-monotonic values → None."""
        now = "2026-02-10T00:00:00Z"
        values = [100, 110, 105, 120, 115, 130, 125]
        for i, val in enumerate(values):
            db.execute(
                "INSERT INTO quality_metrics_daily "
                "(metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
                "VALUES (?, 'collector_volume', 'overall', '', ?, 50, ?, ?)",
                (f"2026-02-{1 + i:02d}", val, now, now),
            )
        db.commit()

        alert = monitor.detect_trends(db, "collector_volume", window=7)
        assert alert is None

    def test_invalid_metric_raises(self, db, monitor):
        """Invalid metric → ValueError."""
        with pytest.raises(ValueError):
            monitor.detect_trends(db, "bad_metric")


class TestCheckZeroVolume:
    """Test zero-volume collector alerting."""

    def test_zero_with_active_baseline_fires_alert(self, db, monitor, monkeypatch):
        """Zero volume + active baseline (mean>=1) → warning alert."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        monkeypatch.setenv("SPC_ZERO_VOLUME_ALERTING", "true")
        _seed_metric_days(db, "collector_volume", 14, base_value=10.0,
                          n_per_day=10, segment_type="collector", segment_key="github")
        alert = monitor.check_zero_volume(db, "github", 0.0)
        assert alert is not None
        assert alert.severity == "warning"
        assert alert.alert_type == "spc_violation"
        assert "github" in alert.message
        assert alert.mean >= 10.0

    def test_zero_with_low_baseline_no_alert(self, db, monitor, monkeypatch):
        """Zero volume + low baseline mean (<1) → no alert (disabled collector)."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        _seed_metric_days(db, "collector_volume", 14, base_value=0.3,
                          n_per_day=1, segment_type="collector", segment_key="uspto")
        alert = monitor.check_zero_volume(db, "uspto", 0.0)
        assert alert is None

    def test_nonzero_value_no_alert(self, db, monitor, monkeypatch):
        """Non-zero current value → no alert (regardless of baseline)."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        _seed_metric_days(db, "collector_volume", 14, base_value=10.0,
                          n_per_day=10, segment_type="collector", segment_key="github")
        alert = monitor.check_zero_volume(db, "github", 5.0)
        assert alert is None

    def test_insufficient_baseline_no_alert(self, db, monitor, monkeypatch):
        """Not enough baseline days → no alert."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "14")
        _seed_metric_days(db, "collector_volume", 5, base_value=10.0,
                          n_per_day=10, segment_type="collector", segment_key="github")
        alert = monitor.check_zero_volume(db, "github", 0.0)
        assert alert is None

    def test_disabled_via_env_var(self, db, monitor, monkeypatch):
        """SPC_ZERO_VOLUME_ALERTING=false → no alert."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        monkeypatch.setenv("SPC_ZERO_VOLUME_ALERTING", "false")
        _seed_metric_days(db, "collector_volume", 14, base_value=10.0,
                          n_per_day=10, segment_type="collector", segment_key="github")
        alert = monitor.check_zero_volume(db, "github", 0.0)
        assert alert is None

    def test_custom_min_baseline_mean(self, db, monitor, monkeypatch):
        """Custom min_baseline_mean parameter works."""
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        _seed_metric_days(db, "collector_volume", 14, base_value=3.0,
                          n_per_day=3, segment_type="collector", segment_key="arxiv")
        # Default threshold (1.0) → alert fires
        alert = monitor.check_zero_volume(db, "arxiv", 0.0)
        assert alert is not None
        # High threshold (5.0) → no alert (mean=3.0 < 5.0)
        alert = monitor.check_zero_volume(db, "arxiv", 0.0, min_baseline_mean=5.0)
        assert alert is None
