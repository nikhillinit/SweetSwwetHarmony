"""v28: Add company_id column to signals table.

Adds the identity column that links each signal to its resolved
company entity (SHA256[:16] via EntityIdentityStore.entity_id_for_seed()).
"""

V28_CANONICAL_IDENTITY_DDL = """
-- ============================================================================
-- v28: CANONICAL IDENTITY
-- ============================================================================

ALTER TABLE signals ADD COLUMN company_id TEXT;

-- Index for company_id lookups (backfill, merge cascade, company queries)
CREATE INDEX IF NOT EXISTS idx_signals_company_id
    ON signals(company_id);

-- Compound index for company + time queries (evidence gathering, sweep)
CREATE INDEX IF NOT EXISTS idx_signals_company_created
    ON signals(company_id, created_at DESC);
"""
