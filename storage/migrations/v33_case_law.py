"""Migration v33: Case-law precedents and anti-pattern proposals.

Adds:
- precedents table for precomputed TF-IDF case-law vectors
- anti_pattern_proposals table with propose→approve governance workflow

Phase 3 — case-law + exemplars.
"""

V33_CASE_LAW_DDL = """
-- ============================================================================
-- v33: CASE-LAW PRECEDENTS + ANTI-PATTERN PROPOSALS
-- ============================================================================

CREATE TABLE IF NOT EXISTS precedents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    canonical_key TEXT NOT NULL,
    company_id TEXT,
    human_label TEXT NOT NULL CHECK(human_label IN ('TP', 'FP')),
    corpus_text TEXT NOT NULL,
    tfidf_vector BLOB,
    similarity_text_hash TEXT,
    signal_created_at TEXT,
    vectorizer_version TEXT NOT NULL,
    label_reason TEXT,
    source_api TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(signal_id, vectorizer_version),
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_precedents_label ON precedents(human_label);
CREATE INDEX IF NOT EXISTS idx_precedents_company ON precedents(company_id);
CREATE INDEX IF NOT EXISTS idx_precedents_version ON precedents(vectorizer_version);

CREATE TABLE IF NOT EXISTS anti_pattern_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,
    pattern_key TEXT NOT NULL,
    description TEXT NOT NULL,
    proposed_action TEXT NOT NULL,
    evidence TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed', 'approved', 'rejected', 'expired', 'applied')),
    proposed_by TEXT NOT NULL DEFAULT 'system',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_one_active
    ON anti_pattern_proposals(pattern_type, pattern_key)
    WHERE status IN ('proposed', 'approved', 'applied');
CREATE INDEX IF NOT EXISTS idx_proposals_status ON anti_pattern_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_type ON anti_pattern_proposals(pattern_type);
"""
