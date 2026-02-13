"""Performance baseline tests for Wave 5 (W5.12).

Pass policy: Report-only — tests ALWAYS pass. Measurements are logged
and compared against SLO thresholds. Warning threshold (2x SLO) logged
but does NOT fail the test. This prevents flaky CI from hardware variance.

SLOs:
- drift check: <2s
- alert listing: <500ms
- daily aggregation: <5s/date
- recommendation: <3s
"""

import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def perf_store():
    """Create a store pre-loaded with test data for performance measurement."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()

    db = s._db
    await db.execute("PRAGMA foreign_keys = OFF")

    # Seed signals (200)
    now = datetime.now(timezone.utc)
    for i in range(200):
        detected = (now - timedelta(days=i % 90)).isoformat()
        await db.execute(
            "INSERT INTO signals (signal_type, source_api, canonical_key, company_name, "
            "confidence, raw_data, detected_at, created_at, company_id) "
            "VALUES ('funding', ?, ?, ?, ?, '{}', ?, ?, ?)",
            (
                f"collector_{i % 5}",
                f"domain:co{i}.com",
                f"Company {i}",
                0.3 + (i % 7) * 0.1,
                detected, detected,
                f"comp_{i:04d}",
            ),
        )

    # Seed canary runs (30)
    for i in range(30):
        created = (now - timedelta(days=i)).isoformat()
        await db.execute(
            "INSERT INTO canary_runs "
            "(run_id, golden_set_size, golden_set_hash, total_scored, passed, failed, "
            "skipped, pass_rate, verdict, drift_threshold, pass_rate_threshold, "
            "duration_ms, created_at) "
            "VALUES (?, 20, 'hash', 20, ?, ?, 0, ?, ?, 0.15, 0.80, 200, ?)",
            (
                f"run-{i:03d}", 20 - (i % 3), i % 3,
                (20 - (i % 3)) / 20,
                "pass" if i % 3 == 0 else "fail",
                created,
            ),
        )

    # Seed drift alerts (100)
    alert_types = ["pass_rate_drop", "individual_drift", "archetype_regression"]
    for i in range(100):
        created = (now - timedelta(days=i % 60)).isoformat()
        await db.execute(
            "INSERT INTO canary_drift_alerts "
            "(canary_run_id, alert_type, severity, metric_name, message, status, created_at) "
            "VALUES (?, ?, ?, 'pass_rate', ?, ?, ?)",
            (
                (i % 30) + 1,
                alert_types[i % 3],
                "warning" if i % 2 == 0 else "critical",
                f"Alert {i}",
                "open" if i % 4 != 0 else "resolved",
                created,
            ),
        )

    # Seed quality_metrics_daily (90 days)
    for day_offset in range(90):
        date = (now - timedelta(days=day_offset + 1)).strftime("%Y-%m-%d")
        ts = (now - timedelta(days=day_offset + 1)).isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO quality_metrics_daily "
            "(metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
            "VALUES (?, 'overall_fp_rate', 'overall', '', ?, ?, ?, ?)",
            (date, 0.2 + (day_offset % 10) * 0.02, 50 + day_offset, ts, ts),
        )

    await db.commit()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def sync_conn(perf_store):
    """Open a sync sqlite3 connection to the same DB for sync functions."""
    conn = sqlite3.connect(str(perf_store.db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


def _report_slo(name: str, elapsed_s: float, slo_s: float):
    """Report SLO measurement. Always passes."""
    status = "OK" if elapsed_s <= slo_s else (
        "WARN" if elapsed_s <= slo_s * 2 else "SLOW"
    )
    print(f"  SLO [{status}] {name}: {elapsed_s:.3f}s (target: <{slo_s}s)")


class TestWave5SLOs:
    """Performance baselines — report-only, never hard-fail."""

    @pytest.mark.asyncio
    async def test_spc_check_latency(self, perf_store, sync_conn):
        """SPC check should complete within SLO."""
        from monitoring.spc_monitor import SPCMonitor

        monitor = SPCMonitor()
        start = time.perf_counter()
        limits = monitor.compute_control_limits(
            sync_conn, "overall_fp_rate",
        )
        if limits:
            monitor.check_metric(
                sync_conn, "overall_fp_rate", limits.mean,
            )
        elapsed = time.perf_counter() - start
        _report_slo("drift_check", elapsed, 2.0)
        # Always pass — report only
        assert True

    @pytest.mark.asyncio
    async def test_alert_listing_latency(self, perf_store):
        """Alert listing should complete within SLO."""
        start = time.perf_counter()
        cursor = await perf_store._db.execute(
            "SELECT * FROM canary_drift_alerts ORDER BY created_at DESC LIMIT 50"
        )
        await cursor.fetchall()
        elapsed = time.perf_counter() - start
        _report_slo("alert_listing", elapsed, 0.5)
        assert True

    @pytest.mark.asyncio
    async def test_daily_aggregation_latency(self, perf_store, sync_conn):
        """Daily aggregation should complete within SLO per date."""
        from monitoring.daily_aggregator import aggregate_daily_metrics

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        start = time.perf_counter()
        aggregate_daily_metrics(sync_conn, yesterday)
        elapsed = time.perf_counter() - start
        _report_slo("daily_aggregation", elapsed, 5.0)
        assert True

    @pytest.mark.asyncio
    async def test_recommendation_latency(self, perf_store):
        """Recommendation generation should complete within SLO."""
        from monitoring.drift_recommendations import generate_recommendations

        start = time.perf_counter()
        await generate_recommendations(perf_store, lookback_days=7)
        elapsed = time.perf_counter() - start
        _report_slo("recommendation", elapsed, 3.0)
        assert True

    @pytest.mark.asyncio
    async def test_mtta_computation_latency(self, perf_store):
        """MTTA computation should complete within SLO."""
        from monitoring.alert_escalation import compute_mtta

        start = time.perf_counter()
        await compute_mtta(perf_store, lookback_days=30)
        elapsed = time.perf_counter() - start
        _report_slo("mtta_computation", elapsed, 1.0)
        assert True

    @pytest.mark.asyncio
    async def test_calibration_curve_latency(self, perf_store, sync_conn):
        """Calibration curve computation should complete within SLO."""
        from monitoring.spc_monitor import SPCMonitor

        monitor = SPCMonitor()
        start = time.perf_counter()
        monitor.compute_calibration_curve(sync_conn, bins=10)
        elapsed = time.perf_counter() - start
        _report_slo("calibration_curve", elapsed, 2.0)
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
