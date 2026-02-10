"""
Wave 3 DDL: Active Hunter Sandbox tables.

5 tables:
1. hunter_queries — Search queries per hunter run
2. hunter_results — Raw results from hunter queries
3. hunter_budget — Daily budget summary per collector + global
4. hunter_budget_transactions — Append-only ledger for budget accounting
5. hunter_negative_keywords — Excluded keywords for query generation

FK aligned: run_history.id is TEXT PRIMARY KEY (from v35).
json_valid() used per v38 pattern.
"""

V39_ACTIVE_HUNTER_DDL = """
-- =============================================================================
-- 1. hunter_queries — Search queries per hunter run
-- =============================================================================
CREATE TABLE IF NOT EXISTS hunter_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    collector TEXT NOT NULL,
    query_text TEXT NOT NULL,
    query_type TEXT NOT NULL DEFAULT 'pattern'
        CHECK(query_type IN ('pattern','bootstrap','manual')),
    source_pattern TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','executing','completed','failed','skipped')),
    results_count INTEGER NOT NULL DEFAULT 0,
    cost_units_reserved REAL NOT NULL DEFAULT 0.0,
    cost_units_final REAL,
    inputs_hash TEXT,
    timeout_seconds INTEGER NOT NULL DEFAULT 30,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    metadata TEXT CHECK(metadata IS NULL OR json_valid(metadata)),
    FOREIGN KEY(run_id) REFERENCES run_history(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hq_run ON hunter_queries(run_id);
CREATE INDEX IF NOT EXISTS idx_hq_collector_status ON hunter_queries(collector, status);
CREATE INDEX IF NOT EXISTS idx_hq_created ON hunter_queries(created_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hq_run_hash ON hunter_queries(run_id, inputs_hash);

-- =============================================================================
-- 2. hunter_results — Raw results from hunter queries
-- =============================================================================
CREATE TABLE IF NOT EXISTS hunter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    query_id INTEGER NOT NULL,
    result_dedupe_key TEXT NOT NULL,
    company_name TEXT NOT NULL,
    canonical_key TEXT,
    company_id TEXT,
    source_api TEXT NOT NULL,
    raw_data TEXT NOT NULL CHECK(json_valid(raw_data)),
    confidence_score REAL,
    exemplar_similarity REAL,
    thesis_fit_score REAL,
    already_known INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','relevant','not_relevant','already_known','promoted')),
    operator_feedback TEXT,
    promoted_signal_id INTEGER,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    promoted_at TEXT,
    updated_at TEXT NOT NULL,
    metadata TEXT CHECK(metadata IS NULL OR json_valid(metadata)),
    FOREIGN KEY(query_id) REFERENCES hunter_queries(id) ON DELETE CASCADE,
    UNIQUE(result_dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_hr_run_status ON hunter_results(run_id, status);
CREATE INDEX IF NOT EXISTS idx_hr_canonical_status ON hunter_results(canonical_key, status);
CREATE INDEX IF NOT EXISTS idx_hr_review ON hunter_results(status, thesis_fit_score DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hr_created ON hunter_results(created_at DESC, id DESC);

-- =============================================================================
-- 3. hunter_budget — Summary rows: one per collector per day + one __global__
-- =============================================================================
CREATE TABLE IF NOT EXISTS hunter_budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_date TEXT NOT NULL,
    collector TEXT NOT NULL,
    queries_executed INTEGER NOT NULL DEFAULT 0,
    queries_cap INTEGER,
    cost_units REAL NOT NULL DEFAULT 0.0,
    cost_cap REAL,
    circuit_breaker_tripped INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(budget_date, collector)
);

-- =============================================================================
-- 4. hunter_budget_transactions — Append-only ledger
-- =============================================================================
CREATE TABLE IF NOT EXISTS hunter_budget_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_date TEXT NOT NULL,
    collector TEXT NOT NULL,
    run_id TEXT,
    query_id INTEGER,
    delta_queries INTEGER NOT NULL DEFAULT 0,
    delta_cost REAL NOT NULL DEFAULT 0.0,
    reason TEXT NOT NULL CHECK(reason IN ('reserve','settle','overrun','manual_adjust')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hbt_date_collector ON hunter_budget_transactions(budget_date, collector);
CREATE INDEX IF NOT EXISTS idx_hbt_run ON hunter_budget_transactions(run_id);

-- =============================================================================
-- 5. hunter_negative_keywords — Excluded keywords for query generation
-- =============================================================================
CREATE TABLE IF NOT EXISTS hunter_negative_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    collector TEXT,
    category TEXT,
    source TEXT NOT NULL CHECK(source IN ('operator_reject','manual')),
    source_result_id INTEGER,
    review_required INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    deactivated_at TEXT,
    metadata TEXT CHECK(metadata IS NULL OR json_valid(metadata)),
    UNIQUE(keyword, collector, category)
);
CREATE INDEX IF NOT EXISTS idx_hnk_active ON hunter_negative_keywords(active, collector);
CREATE INDEX IF NOT EXISTS idx_hnk_source ON hunter_negative_keywords(source_result_id);
"""
