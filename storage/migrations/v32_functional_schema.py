"""Migration v32: Functional schemas table.

Adds functional_schemas for storing LLM-extracted company functional
profiles (problem, customer, approach, archetypes) with versioned history,
confidence gating, and advisory flagging.
"""

V32_FUNCTIONAL_SCHEMA_DDL = """
-- ============================================================================
-- v32: FUNCTIONAL SCHEMAS
-- ============================================================================

CREATE TABLE IF NOT EXISTS functional_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    problem_solved_text TEXT,
    customer_text TEXT,
    approach_text TEXT,
    customer_archetype TEXT,
    problem_archetypes TEXT,
    schema_confidence REAL,
    is_advisory INTEGER NOT NULL DEFAULT 0,
    evidence_signal_ids TEXT,
    extraction_model TEXT,
    extraction_prompt_version TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    superseded_by INTEGER REFERENCES functional_schemas(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(company_id, schema_version)
);

CREATE INDEX IF NOT EXISTS idx_fs_company_active
    ON functional_schemas(company_id, is_active);

CREATE INDEX IF NOT EXISTS idx_fs_archetype
    ON functional_schemas(customer_archetype) WHERE is_active = 1;
"""
