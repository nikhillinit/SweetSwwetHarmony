"""
Wave 2 DDL: Shadow Entity Resolution + Canary Baseline tables.

5 tables:
1. shadow_entity_runs — Shadow mode comparison run records
2. shadow_disagreements — Per-signal disagreements between Phase 1a and Phase G
3. merge_suggestions — Operator-reviewable merge candidates
4. canary_runs — Canary scoring run records
5. canary_drift_alerts — Drift alerts from canary comparisons

NOTE: Uses `canary_drift_alerts` (not `drift_alerts`) to avoid collision
with the existing evaluation-specific `drift_alerts` table in signal_store.py.
"""

V38_WAVE2_SHADOW_CANARY_DDL = """
-- =============================================================================
-- 1. shadow_entity_runs — Shadow mode comparison run records
-- =============================================================================
CREATE TABLE IF NOT EXISTS shadow_entity_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','completed','failed','timeout','skipped')),
    total_signals INTEGER NOT NULL DEFAULT 0,
    phase1a_groups INTEGER NOT NULL DEFAULT 0,
    phase_g_groups INTEGER NOT NULL DEFAULT 0,
    agreements INTEGER NOT NULL DEFAULT 0,
    disagreements INTEGER NOT NULL DEFAULT 0,
    agreement_rate REAL,
    metrics_json TEXT CHECK(metrics_json IS NULL OR json_valid(metrics_json)),
    duration_ms REAL,
    inputs_hash TEXT,
    config_json TEXT CHECK(config_json IS NULL OR json_valid(config_json)),
    error_summary TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    truncation_reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES run_history(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_created ON shadow_entity_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_status ON shadow_entity_runs(status, created_at DESC);

-- =============================================================================
-- 2. shadow_disagreements — Per-signal disagreements
-- =============================================================================
CREATE TABLE IF NOT EXISTS shadow_disagreements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_run_id INTEGER NOT NULL,
    signal_id INTEGER NOT NULL,
    canonical_key TEXT NOT NULL,
    phase1a_company_id TEXT,
    phase_g_entity_id TEXT,
    phase_g_group_key TEXT,
    disagreement_type TEXT NOT NULL CHECK(disagreement_type IN ('over_merge','over_split')),
    collector TEXT,
    confidence REAL,
    confidence_band TEXT CHECK(confidence_band IS NULL OR confidence_band IN ('high','medium','low')),
    canonical_key_type TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(shadow_run_id) REFERENCES shadow_entity_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_shadow_disagree_run ON shadow_disagreements(shadow_run_id);
CREATE INDEX IF NOT EXISTS idx_shadow_disagree_type ON shadow_disagreements(disagreement_type, collector, confidence_band);
CREATE INDEX IF NOT EXISTS idx_shadow_disagree_signal ON shadow_disagreements(signal_id);

-- =============================================================================
-- 3. merge_suggestions — Operator-reviewable merge candidates
-- =============================================================================
CREATE TABLE IF NOT EXISTS merge_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_run_id INTEGER,
    pair_key TEXT NOT NULL,
    entity_a_company_id TEXT NOT NULL,
    entity_b_company_id TEXT NOT NULL,
    entity_a_canonical_key TEXT NOT NULL,
    entity_b_canonical_key TEXT NOT NULL,
    entity_a_company_name TEXT,
    entity_b_company_name TEXT,
    match_type TEXT NOT NULL CHECK(match_type IN ('fuzzy_name','shared_alias','shared_domain','blocking_token')),
    similarity_score REAL NOT NULL,
    scoring_version TEXT NOT NULL DEFAULT '1.0.0',
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','superseded')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    blast_radius_json TEXT CHECK(blast_radius_json IS NULL OR json_valid(blast_radius_json)),
    created_at TEXT NOT NULL,
    FOREIGN KEY(shadow_run_id) REFERENCES shadow_entity_runs(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_merge_pair_key ON merge_suggestions(pair_key);
CREATE INDEX IF NOT EXISTS idx_merge_suggestions_status ON merge_suggestions(status, similarity_score DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_merge_suggestions_created ON merge_suggestions(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_merge_entity_a_status ON merge_suggestions(entity_a_company_id, status);
CREATE INDEX IF NOT EXISTS idx_merge_entity_b_status ON merge_suggestions(entity_b_company_id, status);

-- =============================================================================
-- 4. canary_runs — Canary scoring run records
-- =============================================================================
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
    results_json TEXT CHECK(results_json IS NULL OR json_valid(results_json)),
    stratification_json TEXT CHECK(stratification_json IS NULL OR json_valid(stratification_json)),
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES run_history(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_canary_runs_created ON canary_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_canary_runs_verdict ON canary_runs(verdict, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_canary_runs_hash ON canary_runs(golden_set_hash, created_at DESC);

-- =============================================================================
-- 5. canary_drift_alerts — Drift alerts (distinct from existing drift_alerts)
-- =============================================================================
CREATE TABLE IF NOT EXISTS canary_drift_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canary_run_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL
        CHECK(alert_type IN ('pass_rate_drop','individual_drift','archetype_regression','pass_rate_improvement','archetype_improvement')),
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
CREATE INDEX IF NOT EXISTS idx_canary_drift_status ON canary_drift_alerts(status, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_canary_drift_run ON canary_drift_alerts(canary_run_id);
"""
