"""Migration v37: ACH analyses table.

Adds the ach_analyses table for storing deterministic ACH matrix results.
Cache identity is (company_id, builder_version, rubric_version, inputs_hash).
"""

V37_ACH_ANALYSES_DDL = """
-- ============================================================================
-- v37: ACH ANALYSES (Deterministic Analysis of Competing Hypotheses)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ach_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    review_id INTEGER,
    builder_version TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    matrix_json TEXT NOT NULL,
    top_hypothesis TEXT,
    top_score REAL,
    bull_summary TEXT,
    bear_summary TEXT,
    differentiator_count INTEGER DEFAULT 0,
    evidence_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(review_id) REFERENCES review_items(id) ON DELETE SET NULL
);

-- A2: Unique constraint on cache identity — company-scoped (NOT review-scoped)
CREATE UNIQUE INDEX IF NOT EXISTS idx_ach_cache_identity
    ON ach_analyses(company_id, builder_version, rubric_version, inputs_hash);

CREATE INDEX IF NOT EXISTS idx_ach_review
    ON ach_analyses(review_id);

-- Deterministic latest-row selection: ORDER BY created_at DESC, id DESC
CREATE INDEX IF NOT EXISTS idx_ach_latest
    ON ach_analyses(company_id, created_at DESC, id DESC);
"""
