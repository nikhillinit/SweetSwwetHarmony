"""v41 — Drift Monitoring DDL (Wave 5).

Creates quality_metrics_daily (row-based metric/segment model) and enhances
canary_drift_alerts with snoozed status, drift_category, signature-based
dedup, occurrence tracking, and correlation IDs.

Design decisions:
- D1:  Row-based metric model (not JSON "God row")
- D11: True UPSERT (ON CONFLICT DO UPDATE), not INSERT OR REPLACE
- D17: Non-null segment normalization for UNIQUE constraint correctness
- D18: DB-enforced active-alert dedup via partial unique index
- D20: Snooze reopen scan index for fast 15-min cron
- D16: Retention indexes for efficient DELETE by date
"""

V41_DRIFT_MONITORING_DDL = """
-- =============================================================================
-- 1. quality_metrics_daily — Row-based metric/segment model (D1)
-- =============================================================================
CREATE TABLE IF NOT EXISTS quality_metrics_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_date TEXT NOT NULL,           -- YYYY-MM-DD (UTC)
    metric_name TEXT NOT NULL,           -- overall_fp_rate, collector_volume, etc.
    segment_type TEXT NOT NULL DEFAULT 'overall',  -- D17: NOT NULL for UNIQUE correctness
    segment_key TEXT NOT NULL DEFAULT '',           -- D17: NOT NULL for UNIQUE correctness
    value REAL,                          -- NULL if insufficient data
    n INTEGER NOT NULL DEFAULT 0,        -- sample size (denominator)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,            -- tracks re-aggregation via UPSERT (D11)
    UNIQUE(metric_date, metric_name, segment_type, segment_key)
);

-- Query by metric name + date range
CREATE INDEX IF NOT EXISTS idx_qmd_metric
    ON quality_metrics_daily(metric_name, metric_date DESC);

-- Query by metric + segment + date range
CREATE INDEX IF NOT EXISTS idx_qmd_segment
    ON quality_metrics_daily(metric_name, segment_type, segment_key, metric_date DESC);

-- Retention: efficient DELETE WHERE metric_date < ? (D16)
CREATE INDEX IF NOT EXISTS idx_qmd_retention
    ON quality_metrics_daily(metric_date);

-- =============================================================================
-- 2. Recreate canary_drift_alerts with enhanced schema
--    SQLite requires CREATE→INSERT→DROP→RENAME for CHECK constraint changes
-- =============================================================================

-- 2a. Create new table with full schema
CREATE TABLE IF NOT EXISTS canary_drift_alerts_v41 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canary_run_id INTEGER,              -- NULL for SPC-generated alerts
    alert_type TEXT NOT NULL
        CHECK(alert_type IN (
            'pass_rate_drop','individual_drift','archetype_regression',
            'pass_rate_improvement','archetype_improvement',
            'spc_violation','trend_alert','calibration_drift'
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
        CHECK(status IN ('open','acknowledged','snoozed','resolved')),
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    resolution TEXT,
    drift_category TEXT
        CHECK(drift_category IS NULL OR drift_category IN (
            'data_drift','concept_drift','model_drift'
        )),
    signature_key TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT,
    correlation_ids_json TEXT
        CHECK(correlation_ids_json IS NULL OR json_valid(correlation_ids_json)),
    snoozed_until TEXT,
    snooze_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(canary_run_id) REFERENCES canary_runs(id) ON DELETE CASCADE
);

-- 2b. Copy existing data
INSERT INTO canary_drift_alerts_v41 (
    id, canary_run_id, alert_type, severity, signal_id, canonical_key,
    metric_name, expected_value, actual_value, delta, message,
    status, acknowledged_by, acknowledged_at, created_at
)
SELECT
    id, canary_run_id, alert_type, severity, signal_id, canonical_key,
    metric_name, expected_value, actual_value, delta, message,
    status, acknowledged_by, acknowledged_at, created_at
FROM canary_drift_alerts;

-- 2c. Drop old table, rename new
DROP TABLE IF EXISTS canary_drift_alerts;
ALTER TABLE canary_drift_alerts_v41 RENAME TO canary_drift_alerts;

-- 2d. Recreate original indexes
CREATE INDEX IF NOT EXISTS idx_canary_drift_status
    ON canary_drift_alerts(status, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_canary_drift_run
    ON canary_drift_alerts(canary_run_id);

-- 2e. DB-enforced active-alert dedup via partial unique index (D18)
CREATE UNIQUE INDEX IF NOT EXISTS idx_cda_active_sig
    ON canary_drift_alerts(signature_key)
    WHERE status IN ('open','snoozed');

-- 2f. Snooze reopen scan index (D20)
CREATE INDEX IF NOT EXISTS idx_cda_snooze_reopen
    ON canary_drift_alerts(status, snoozed_until)
    WHERE status = 'snoozed';

-- 2g. Retention index (D16): 180 days for alerts
CREATE INDEX IF NOT EXISTS idx_cda_retention
    ON canary_drift_alerts(created_at);
"""

# Downgrade DDL: v41 → v40
V41_DOWNGRADE_DDL = """
-- Drop quality_metrics_daily
DROP TABLE IF EXISTS quality_metrics_daily;

-- Recreate original canary_drift_alerts (v38 schema)
CREATE TABLE IF NOT EXISTS canary_drift_alerts_v38 (
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
);

-- Copy compatible data back (drop snoozed→open, drop new alert types)
INSERT OR IGNORE INTO canary_drift_alerts_v38 (
    id, canary_run_id, alert_type, severity, signal_id, canonical_key,
    metric_name, expected_value, actual_value, delta, message,
    status, acknowledged_by, acknowledged_at, created_at
)
SELECT
    id, canary_run_id, alert_type, severity, signal_id, canonical_key,
    metric_name, expected_value, actual_value, delta, message,
    CASE WHEN status = 'snoozed' THEN 'open' ELSE status END,
    acknowledged_by, acknowledged_at, created_at
FROM canary_drift_alerts
WHERE alert_type IN (
    'pass_rate_drop','individual_drift','archetype_regression',
    'pass_rate_improvement','archetype_improvement'
)
AND canary_run_id IS NOT NULL;

DROP TABLE IF EXISTS canary_drift_alerts;
ALTER TABLE canary_drift_alerts_v38 RENAME TO canary_drift_alerts;

-- Recreate v38 indexes
CREATE INDEX IF NOT EXISTS idx_canary_drift_status
    ON canary_drift_alerts(status, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_canary_drift_run
    ON canary_drift_alerts(canary_run_id);
"""
