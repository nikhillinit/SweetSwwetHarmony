"""Tests for publish_fp_rate metric (TDD RED phase).

This metric measures the false-positive rate among signals that were actually
pushed to Notion (i.e., have status='pushed' in signal_processing and a
notion_page_id set). This is a more operationally meaningful metric than
overall_fp_rate, which counts all signals regardless of whether they reached
the CRM.

Tests cover:
- Registration in SPC monitor constants (VALID_SPC_METRICS, RATIO_METRICS, ONE_SIDED_INCREASE_METRICS)
- Computation logic: basic ratio, exclusion of rejected signals, min-N gating, date filtering
- Integration with aggregate_daily_metrics
- Activation gate policy (optional at step 4)
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_tables(conn):
    """Create all prerequisite tables for publish_fp_rate tests."""
    conn.execute("PRAGMA foreign_keys = OFF")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL DEFAULT '{}',
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            notion_page_id TEXT,
            processed_at TEXT,
            error_message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL UNIQUE,
            human_label TEXT CHECK(human_label IN ('TP','FP','UNSURE','ADJ')),
            labeled_at TEXT,
            label_source TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS quality_metrics_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_date TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            segment_type TEXT NOT NULL DEFAULT 'overall',
            segment_key TEXT NOT NULL DEFAULT '',
            value REAL,
            n INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(metric_date, metric_name, segment_type, segment_key)
        )
    """)

    # Stub tables needed by daily_aggregator imports
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_at TEXT,
            decided_by TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Canary stubs for drift monitoring migration
    conn.execute("CREATE TABLE IF NOT EXISTS canary_runs (id INTEGER PRIMARY KEY, run_id TEXT, golden_set_size INTEGER DEFAULT 0, golden_set_hash TEXT DEFAULT '', verdict TEXT DEFAULT 'pass', created_at TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS canary_drift_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, canary_run_id INTEGER, alert_type TEXT NOT NULL CHECK(alert_type IN ('pass_rate_drop','individual_drift','archetype_regression','pass_rate_improvement','archetype_improvement')), severity TEXT NOT NULL DEFAULT 'warning' CHECK(severity IN ('info','warning','critical')), signal_id INTEGER, canonical_key TEXT, metric_name TEXT NOT NULL, expected_value REAL, actual_value REAL, delta REAL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved')), acknowledged_by TEXT, acknowledged_at TEXT, created_at TEXT NOT NULL)")

    # Apply v41 drift monitoring DDL (creates quality_metrics_daily if not exists)
    from storage.migrations.v41_drift_monitoring import V41_DRIFT_MONITORING_DDL
    conn.executescript(V41_DRIFT_MONITORING_DDL)

    conn.commit()


def _insert_signal(conn, signal_id, source_api, confidence, detected_at):
    """Insert a test signal."""
    conn.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, confidence, raw_data, detected_at, created_at) "
        "VALUES (?, 'test', ?, 'key_' || ?, ?, '{}', ?, ?)",
        (signal_id, source_api, signal_id, confidence, detected_at, detected_at),
    )


def _insert_processing(conn, signal_id, status, notion_page_id=None):
    """Insert a signal_processing record."""
    now = "2026-03-10T12:00:00+00:00"
    conn.execute(
        "INSERT INTO signal_processing (signal_id, status, notion_page_id, processed_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (signal_id, status, notion_page_id, now, now, now),
    )


def _insert_label(conn, signal_id, label, labeled_at=None):
    """Insert a quality label."""
    conn.execute(
        "INSERT INTO signal_quality_metrics (signal_id, human_label, labeled_at, label_source) "
        "VALUES (?, ?, ?, 'manual')",
        (signal_id, label, labeled_at or "2026-03-10T12:00:00+00:00"),
    )


@pytest.fixture
def db():
    """Create in-memory DB with all prerequisite tables."""
    conn = sqlite3.connect(":memory:")
    _create_tables(conn)
    yield conn
    conn.close()


# ===========================================================================
# Test 1: publish_fp_rate registered in VALID_SPC_METRICS
# ===========================================================================

class TestPublishFPRateRegistration:
    """Verify publish_fp_rate is registered in SPC monitor constants."""

    def test_publish_fp_rate_in_valid_metrics(self):
        """publish_fp_rate must be a recognized SPC metric."""
        from monitoring.spc_monitor import VALID_SPC_METRICS

        assert "publish_fp_rate" in VALID_SPC_METRICS, (
            "publish_fp_rate should be listed in VALID_SPC_METRICS so the "
            "SPC monitor can compute control limits and check for violations"
        )

    def test_publish_fp_rate_is_ratio_metric(self):
        """publish_fp_rate is a ratio in [0,1] and must be clamped accordingly."""
        from monitoring.spc_monitor import RATIO_METRICS

        assert "publish_fp_rate" in RATIO_METRICS, (
            "publish_fp_rate is a ratio (FP / total pushed+labeled) and must "
            "be in RATIO_METRICS so UCL/LCL are clamped to [0,1]"
        )

    def test_publish_fp_rate_is_one_sided(self):
        """publish_fp_rate should only alert on increase (decrease = improvement)."""
        from monitoring.spc_monitor import ONE_SIDED_INCREASE_METRICS

        assert "publish_fp_rate" in ONE_SIDED_INCREASE_METRICS, (
            "publish_fp_rate should be one-sided: a decrease in FP rate among "
            "pushed signals is an improvement and should never trigger an alert"
        )


# ===========================================================================
# Tests 4-7: _compute_publish_fp_rate logic
# ===========================================================================

class TestComputePublishFPRate:
    """Test the _compute_publish_fp_rate aggregator function."""

    def test_compute_publish_fp_rate_basic(self, db):
        """10 pushed+labeled signals (8 TP, 2 FP) -> rate = 0.2, n = 10."""
        from monitoring.daily_aggregator import _compute_publish_fp_rate

        date = "2026-03-10"
        detected = f"{date}T10:00:00+00:00"

        # Insert 10 signals, all pushed to Notion, all labeled
        for i in range(1, 11):
            _insert_signal(db, i, "github", 0.6, detected)
            _insert_processing(db, i, "pushed", notion_page_id=f"page_{i}")
            label = "FP" if i <= 2 else "TP"
            _insert_label(db, i, label)
        db.commit()

        _compute_publish_fp_rate(db, date)

        row = db.execute(
            "SELECT value, n FROM quality_metrics_daily "
            "WHERE metric_name = 'publish_fp_rate' AND metric_date = ?",
            (date,),
        ).fetchone()

        assert row is not None, "publish_fp_rate row should exist in quality_metrics_daily"
        value, n = row
        assert n == 10, f"Expected n=10 pushed+labeled signals, got {n}"
        assert abs(value - 0.2) < 1e-9, f"Expected publish_fp_rate=0.2, got {value}"

    def test_compute_publish_fp_rate_excludes_rejected(self, db):
        """Only pushed signals count; rejected signals are excluded."""
        from monitoring.daily_aggregator import _compute_publish_fp_rate

        date = "2026-03-10"
        detected = f"{date}T10:00:00+00:00"

        # 5 pushed signals: 3 TP, 2 FP
        for i in range(1, 6):
            _insert_signal(db, i, "github", 0.6, detected)
            _insert_processing(db, i, "pushed", notion_page_id=f"page_{i}")
            label = "FP" if i <= 2 else "TP"
            _insert_label(db, i, label)

        # 5 rejected signals: all FP (these should NOT count)
        for i in range(6, 11):
            _insert_signal(db, i, "github", 0.3, detected)
            _insert_processing(db, i, "rejected", notion_page_id=None)
            _insert_label(db, i, "FP")

        db.commit()

        _compute_publish_fp_rate(db, date)

        row = db.execute(
            "SELECT value, n FROM quality_metrics_daily "
            "WHERE metric_name = 'publish_fp_rate' AND metric_date = ?",
            (date,),
        ).fetchone()

        assert row is not None, "publish_fp_rate row should exist"
        value, n = row
        # Only the 5 pushed signals should count
        assert n == 5, f"Expected n=5 (pushed only), got {n}"
        assert abs(value - 0.4) < 1e-9, f"Expected 2/5=0.4, got {value}"

    def test_compute_publish_fp_rate_null_when_insufficient(self, db, monkeypatch):
        """Fewer than SPC_MIN_LABELED_PER_DAY pushed+labeled -> NULL value."""
        from monitoring.daily_aggregator import _compute_publish_fp_rate

        monkeypatch.setenv("SPC_MIN_LABELED_PER_DAY", "10")

        date = "2026-03-10"
        detected = f"{date}T10:00:00+00:00"

        # Only 3 pushed+labeled signals (below the min of 10)
        for i in range(1, 4):
            _insert_signal(db, i, "github", 0.6, detected)
            _insert_processing(db, i, "pushed", notion_page_id=f"page_{i}")
            _insert_label(db, i, "TP")
        db.commit()

        # Re-import to pick up monkeypatched env
        import importlib
        import monitoring.daily_aggregator as da
        importlib.reload(da)
        da._compute_publish_fp_rate(db, date)

        row = db.execute(
            "SELECT value, n FROM quality_metrics_daily "
            "WHERE metric_name = 'publish_fp_rate' AND metric_date = ?",
            (date,),
        ).fetchone()

        assert row is not None, "publish_fp_rate row should exist even with insufficient data"
        value, n = row
        assert value is None, f"Expected NULL value for insufficient labeled signals, got {value}"
        assert n == 3, f"Expected n=3, got {n}"

    def test_compute_publish_fp_rate_date_filtering(self, db):
        """Only signals detected on the target date are counted."""
        from monitoring.daily_aggregator import _compute_publish_fp_rate

        target_date = "2026-03-10"
        other_date = "2026-03-09"

        # 10 signals on target date: 8 TP, 2 FP
        for i in range(1, 11):
            _insert_signal(db, i, "github", 0.6, f"{target_date}T10:00:00+00:00")
            _insert_processing(db, i, "pushed", notion_page_id=f"page_{i}")
            label = "FP" if i <= 2 else "TP"
            _insert_label(db, i, label)

        # 10 signals on a DIFFERENT date: all FP (should not affect target date)
        for i in range(11, 21):
            _insert_signal(db, i, "github", 0.3, f"{other_date}T10:00:00+00:00")
            _insert_processing(db, i, "pushed", notion_page_id=f"page_{i}")
            _insert_label(db, i, "FP")

        db.commit()

        _compute_publish_fp_rate(db, target_date)

        row = db.execute(
            "SELECT value, n FROM quality_metrics_daily "
            "WHERE metric_name = 'publish_fp_rate' AND metric_date = ?",
            (target_date,),
        ).fetchone()

        assert row is not None
        value, n = row
        assert n == 10, f"Expected n=10 (only target date), got {n}"
        assert abs(value - 0.2) < 1e-9, f"Expected 2/10=0.2, got {value}"

        # Verify the other date was NOT computed
        other_row = db.execute(
            "SELECT value FROM quality_metrics_daily "
            "WHERE metric_name = 'publish_fp_rate' AND metric_date = ?",
            (other_date,),
        ).fetchone()
        assert other_row is None, "Other date should not have publish_fp_rate computed"


# ===========================================================================
# Test 8: aggregate_daily_metrics includes publish_fp_rate
# ===========================================================================

class TestAggregateIncludesPublishFPRate:
    """Verify publish_fp_rate is computed as part of aggregate_daily_metrics."""

    def test_aggregate_includes_publish_fp_rate(self, db):
        """aggregate_daily_metrics result dict must contain publish_fp_rate key."""
        from monitoring.daily_aggregator import aggregate_daily_metrics

        date = "2026-03-10"
        detected = f"{date}T10:00:00+00:00"

        # Insert enough pushed+labeled signals to exceed min-N threshold
        for i in range(1, 16):
            _insert_signal(db, i, "github", 0.6, detected)
            _insert_processing(db, i, "pushed", notion_page_id=f"page_{i}")
            label = "FP" if i <= 3 else "TP"
            _insert_label(db, i, label)
        db.commit()

        result = aggregate_daily_metrics(db, date)

        assert "publish_fp_rate" in result, (
            "aggregate_daily_metrics should include publish_fp_rate in its "
            "result dictionary"
        )
        assert result["publish_fp_rate"]["value"] is not None
        assert result["publish_fp_rate"]["n"] == 15


# ===========================================================================
# Test 9: activation gate step 4 optional_spc_metrics
# ===========================================================================

class TestActivationGatePublishFPRate:
    """Verify publish_fp_rate is in step 4 optional_spc_metrics."""

    def test_activation_gate_optional_step4(self):
        """publish_fp_rate should be an optional SPC metric at step 4."""
        from monitoring.activation_gate import STEP_POLICY

        step4 = STEP_POLICY[4]
        assert "publish_fp_rate" in step4["optional_spc_metrics"], (
            "publish_fp_rate should be listed in step 4 optional_spc_metrics "
            "so the activation gate can evaluate it when data is available"
        )
