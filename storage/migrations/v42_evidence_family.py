"""v42 — Add evidence_family column to signals.

Classifies each signal into a family (developer, regulatory, web_presence,
hiring, public_buzz, unknown) for convergence KPI computation.

NULL default: backfill-evidence-family command targets NULL rows.
Partial index: only index non-NULL rows for efficient convergence queries.
"""

V42_EVIDENCE_FAMILY_DDL = """
ALTER TABLE signals ADD COLUMN evidence_family TEXT;
CREATE INDEX IF NOT EXISTS idx_signals_evidence_family
    ON signals(evidence_family) WHERE evidence_family IS NOT NULL;
"""
