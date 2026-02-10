"""v40 — Merge Lifecycle (Wave 4 Write Activation).

Creates the merge_proposals table for tracking merge proposal state transitions:
proposed → approved → applied → rolled_back | rejected

Includes partial unique index to prevent duplicate active proposals while
allowing re-proposal after rejection/rollback.
"""

V40_MERGE_LIFECYCLE_DDL = """
-- =============================================================================
-- merge_proposals — State-machine lifecycle for entity merges
-- =============================================================================
CREATE TABLE IF NOT EXISTS merge_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id INTEGER NOT NULL,
    entity_a_company_id TEXT NOT NULL,
    entity_b_company_id TEXT NOT NULL,
    winner_company_id TEXT NOT NULL,
    loser_company_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed','approved','applied','rolled_back','rejected')),
    reason TEXT,
    proposed_by TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    applied_at TEXT,
    rolled_back_at TEXT,
    rollback_reason TEXT,
    before_snapshot TEXT,
    after_snapshot TEXT,
    target_version_snapshot TEXT,
    cascade_report TEXT,
    correlation_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_merge_proposals_suggestion
    ON merge_proposals(suggestion_id);
CREATE INDEX IF NOT EXISTS idx_merge_proposals_status
    ON merge_proposals(status, applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_merge_proposals_winner
    ON merge_proposals(winner_company_id, status, applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_merge_proposals_loser
    ON merge_proposals(loser_company_id, status, applied_at DESC);

-- Partial unique index: allows re-proposal after rejection/rollback (SQLite 3.8.0+)
CREATE UNIQUE INDEX IF NOT EXISTS idx_merge_proposals_active
    ON merge_proposals(suggestion_id)
    WHERE status NOT IN ('rejected', 'rolled_back');
"""
