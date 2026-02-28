"""v46 — Upgrade evidence_key index to UNIQUE.

Prerequisite: run backfill_evidence_keys.py to resolve duplicates.
If duplicates remain, CREATE UNIQUE INDEX will fail with a clear error.
"""

V46_EVIDENCE_KEY_UNIQUE_DDL = """
DROP INDEX IF EXISTS idx_signals_evidence_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_evidence_key
    ON signals(evidence_key)
    WHERE evidence_key IS NOT NULL AND evidence_key != '';
"""
