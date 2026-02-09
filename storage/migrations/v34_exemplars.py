"""Migration v34: Thesis exemplars table.

Adds thesis_exemplars for curated TP exemplar patterns used by
exemplar matching and veto logic.

Phase 3 — case-law + exemplars.
"""

V34_EXEMPLARS_DDL = """
-- ============================================================================
-- v34: THESIS EXEMPLARS
-- ============================================================================

CREATE TABLE IF NOT EXISTS thesis_exemplars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exemplar_key TEXT NOT NULL,
    canonical_key TEXT,
    company_name TEXT,
    human_label TEXT NOT NULL DEFAULT 'TP',
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    corpus_text TEXT NOT NULL,
    tfidf_vector BLOB,
    vectorizer_version TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(exemplar_key, vectorizer_version)
);
CREATE INDEX IF NOT EXISTS idx_exemplars_category
    ON thesis_exemplars(category) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_exemplars_version
    ON thesis_exemplars(vectorizer_version);
CREATE INDEX IF NOT EXISTS idx_exemplars_active
    ON thesis_exemplars(is_active);
"""
