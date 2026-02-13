"""Tests for v41 drift monitoring migration.

Covers:
- quality_metrics_daily table creation with correct schema
- canary_drift_alerts recreation with new columns
- UNIQUE constraint on (metric_date, metric_name, segment_type, segment_key)
- Non-null segment normalization (D17)
- Partial unique index on signature_key (D18)
- Snooze reopen index (D20)
- Retention indexes (D16)
- Data migration from old canary_drift_alerts
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.migrations.v41_drift_monitoring import (
    V41_DRIFT_MONITORING_DDL,
    V41_DOWNGRADE_DDL,
)


@pytest.fixture
def db():
    """Create an in-memory DB with prerequisite tables (v38 canary schema)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")

    # Minimal run_history stub for FK reference
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_history (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)

    # Minimal canary_runs stub for FK reference
    conn.execute("""
        CREATE TABLE IF NOT EXISTS canary_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            golden_set_size INTEGER NOT NULL,
            golden_set_hash TEXT NOT NULL,
            golden_set_version TEXT,
            config_hash TEXT,
            total_scored INTEGER NOT NULL DEFAULT 0,
            passed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            pass_rate REAL,
            verdict TEXT NOT NULL,
            drift_threshold REAL,
            pass_rate_threshold REAL,
            duration_ms REAL,
            results_json TEXT,
            stratification_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES run_history(id) ON DELETE CASCADE
        )
    """)

    # Original v38 canary_drift_alerts
    conn.execute("""
        CREATE TABLE IF NOT EXISTS canary_drift_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canary_run_id INTEGER NOT NULL,
            alert_type TEXT NOT NULL
                CHECK(alert_type IN (
                    'pass_rate_drop','individual_drift','archetype_regression',
                    'pass_rate_improvement','archetype_improvement'
                )),
            severity TEXT NOT NULL DEFAULT 'warning'
                CHECK(severity IN ('info','warning','critical')),
            signal_id INTEGER,
            canonical_key TEXT,
            metric_name TEXT NOT NULL,
            expected_value REAL,
            actual_value REAL,
            delta REAL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open','acknowledged','resolved')),
            acknowledged_by TEXT,
            acknowledged_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(canary_run_id) REFERENCES canary_runs(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    yield conn
    conn.close()


class TestV41Migration:
    """Test v41 migration DDL application."""

    def test_migration_applies_cleanly(self, db):
        """Migration DDL executes without error on a fresh v38 base."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        # Should not raise

    def test_quality_metrics_daily_created(self, db):
        """quality_metrics_daily table exists after migration."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='quality_metrics_daily'"
        )
        assert cursor.fetchone() is not None

    def test_quality_metrics_daily_columns(self, db):
        """quality_metrics_daily has all expected columns."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        cursor = db.execute("PRAGMA table_info(quality_metrics_daily)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {
            'id', 'metric_date', 'metric_name', 'segment_type', 'segment_key',
            'value', 'n', 'created_at', 'updated_at',
        }
        assert expected == cols

    def test_quality_metrics_daily_unique_constraint(self, db):
        """UNIQUE on (metric_date, metric_name, segment_type, segment_key)."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        now = '2026-02-10T00:00:00Z'
        db.execute(
            "INSERT INTO quality_metrics_daily (metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
            "VALUES ('2026-02-10', 'overall_fp_rate', 'overall', '', 0.25, 100, ?, ?)",
            (now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO quality_metrics_daily (metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
                "VALUES ('2026-02-10', 'overall_fp_rate', 'overall', '', 0.30, 100, ?, ?)",
                (now, now),
            )

    def test_nonnull_segment_normalization_d17(self, db):
        """segment_type and segment_key default to non-null values (D17)."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        now = '2026-02-10T00:00:00Z'
        db.execute(
            "INSERT INTO quality_metrics_daily (metric_date, metric_name, value, n, created_at, updated_at) "
            "VALUES ('2026-02-10', 'overall_fp_rate', 0.25, 100, ?, ?)",
            (now, now),
        )
        row = db.execute(
            "SELECT segment_type, segment_key FROM quality_metrics_daily WHERE metric_date='2026-02-10'"
        ).fetchone()
        assert row[0] == 'overall'
        assert row[1] == ''

    def test_canary_drift_alerts_new_columns(self, db):
        """canary_drift_alerts has all new columns after migration."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        cursor = db.execute("PRAGMA table_info(canary_drift_alerts)")
        cols = {row[1] for row in cursor.fetchall()}
        new_cols = {
            'drift_category', 'signature_key', 'occurrence_count',
            'last_seen_at', 'correlation_ids_json', 'snoozed_until',
            'snooze_count', 'resolved_by', 'resolved_at', 'resolution',
        }
        assert new_cols.issubset(cols)

    def test_canary_drift_alerts_snoozed_status(self, db):
        """canary_drift_alerts CHECK allows 'snoozed' status."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        now = '2026-02-10T00:00:00Z'
        db.execute(
            "INSERT INTO canary_drift_alerts "
            "(alert_type, severity, metric_name, message, status, created_at) "
            "VALUES ('spc_violation', 'warning', 'overall_fp_rate', 'test', 'snoozed', ?)",
            (now,),
        )
        row = db.execute("SELECT status FROM canary_drift_alerts").fetchone()
        assert row[0] == 'snoozed'

    def test_data_migration_preserves_existing_alerts(self, db):
        """Existing canary_drift_alerts rows are preserved during migration."""
        # Insert test data into old table
        db.execute(
            "INSERT INTO canary_drift_alerts "
            "(canary_run_id, alert_type, severity, metric_name, message, status, created_at) "
            "VALUES (1, 'pass_rate_drop', 'critical', 'pass_rate', 'dropped', 'open', '2026-02-09T00:00:00Z')"
        )
        db.commit()

        # Run migration
        db.executescript(V41_DRIFT_MONITORING_DDL)

        # Verify data preserved
        row = db.execute(
            "SELECT alert_type, severity, metric_name, message, status FROM canary_drift_alerts"
        ).fetchone()
        assert row == ('pass_rate_drop', 'critical', 'pass_rate', 'dropped', 'open')


class TestV41Indexes:
    """Test that v41 creates all required indexes."""

    def _get_indexes(self, db, table):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table,),
        )
        return {row[0] for row in cursor.fetchall()}

    def test_quality_metrics_daily_indexes(self, db):
        db.executescript(V41_DRIFT_MONITORING_DDL)
        indexes = self._get_indexes(db, 'quality_metrics_daily')
        assert 'idx_qmd_metric' in indexes
        assert 'idx_qmd_segment' in indexes
        assert 'idx_qmd_retention' in indexes

    def test_canary_drift_alerts_indexes(self, db):
        db.executescript(V41_DRIFT_MONITORING_DDL)
        indexes = self._get_indexes(db, 'canary_drift_alerts')
        assert 'idx_canary_drift_status' in indexes
        assert 'idx_canary_drift_run' in indexes
        assert 'idx_cda_active_sig' in indexes
        assert 'idx_cda_snooze_reopen' in indexes
        assert 'idx_cda_retention' in indexes

    def test_partial_unique_index_dedup_d18(self, db):
        """Active-alert dedup: same signature_key with open status = conflict."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        now = '2026-02-10T00:00:00Z'
        db.execute(
            "INSERT INTO canary_drift_alerts "
            "(alert_type, severity, metric_name, message, status, signature_key, created_at) "
            "VALUES ('spc_violation', 'warning', 'overall_fp_rate', 'test1', 'open', 'sig_abc', ?)",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO canary_drift_alerts "
                "(alert_type, severity, metric_name, message, status, signature_key, created_at) "
                "VALUES ('spc_violation', 'warning', 'overall_fp_rate', 'test2', 'open', 'sig_abc', ?)",
                (now,),
            )

    def test_partial_unique_allows_resolved_duplicate(self, db):
        """Resolved alerts don't block new alerts with same signature_key."""
        db.executescript(V41_DRIFT_MONITORING_DDL)
        now = '2026-02-10T00:00:00Z'
        db.execute(
            "INSERT INTO canary_drift_alerts "
            "(alert_type, severity, metric_name, message, status, signature_key, created_at) "
            "VALUES ('spc_violation', 'warning', 'overall_fp_rate', 'test1', 'resolved', 'sig_abc', ?)",
            (now,),
        )
        # This should NOT raise because resolved is not in the partial index
        db.execute(
            "INSERT INTO canary_drift_alerts "
            "(alert_type, severity, metric_name, message, status, signature_key, created_at) "
            "VALUES ('spc_violation', 'warning', 'overall_fp_rate', 'test2', 'open', 'sig_abc', ?)",
            (now,),
        )
        count = db.execute("SELECT COUNT(*) FROM canary_drift_alerts").fetchone()[0]
        assert count == 2
