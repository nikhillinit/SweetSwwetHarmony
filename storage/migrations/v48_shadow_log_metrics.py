"""v48 — Shadow log metrics extension table.

Extends the existing shadow_log table (v23) with per-computation metrics.
This is an extension table keyed by shadow_log.id — NOT a replacement.

Columns:
  - latency_ms: wall-clock time for the computation
  - upstream_data_version: version/run ID of the data used
  - missingness_reason: why a feature value was NULL
  - api_calls_made: number of external API calls
  - error: error message if computation failed
"""

V48_SHADOW_LOG_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS shadow_log_metrics (
    shadow_log_id INTEGER PRIMARY KEY
        REFERENCES shadow_log(id) ON DELETE CASCADE,
    latency_ms REAL,
    upstream_data_version TEXT,
    missingness_reason TEXT,
    api_calls_made INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_log_metrics_version
    ON shadow_log_metrics(upstream_data_version)
    WHERE upstream_data_version IS NOT NULL;
"""
