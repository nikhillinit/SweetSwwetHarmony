"""v43 — Add canonical_key_v2 column to signals.

Domain-first canonical key that enables multi-source convergence KPI.
NULL default: rehydrate-canonical-keys-v2 command targets NULL rows.
Partial index: only index non-NULL rows for efficient convergence queries.
"""

V43_CANONICAL_KEY_V2_DDL = """
ALTER TABLE signals ADD COLUMN canonical_key_v2 TEXT;
CREATE INDEX IF NOT EXISTS idx_signals_canonical_key_v2
    ON signals(canonical_key_v2) WHERE canonical_key_v2 IS NOT NULL;
"""
