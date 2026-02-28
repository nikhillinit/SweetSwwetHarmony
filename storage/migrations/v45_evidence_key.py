"""v45 — Add evidence_key column to signals.

Content-addressed dedup key: sha256(source_api + source_url)[:32].
NULL = legacy/pre-backfill signal, non-NULL = strict dedup mode.
Non-unique index in v45; upgraded to UNIQUE in v46 after backfill.
"""

V45_EVIDENCE_KEY_DDL = """
ALTER TABLE signals ADD COLUMN evidence_key TEXT;
CREATE INDEX IF NOT EXISTS idx_signals_evidence_key
    ON signals(evidence_key)
    WHERE evidence_key IS NOT NULL AND evidence_key != '';
"""
