"""Audit log table definition -- single source of truth.

Records all operator and pipeline actions for traceability.
Used by triage CLI (approve/reject/defer), manual push, batch push,
and delivery policy blocks.

Imported by:
  - storage/signal_store.py (migration 27)
"""

AUDIT_LOG_DDL = """
-- ============================================================================
-- AUDIT LOG TABLE
-- ============================================================================

-- Structured audit trail for all pipeline and operator actions.
-- Each row records a single action with its context.
-- Drop legacy audit_log (v24 schema) to replace with new structured version.
DROP TABLE IF EXISTS audit_log;
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,    -- e.g., 'triage_approve', 'triage_reject',
                                 --       'triage_defer', 'manual_push',
                                 --       'batch_push', 'delivery_policy_block'
    entity_type TEXT NOT NULL,   -- e.g., 'signal', 'batch', 'company'
    entity_id TEXT NOT NULL,     -- ID of the affected entity
    actor TEXT,                  -- who performed the action (e.g., 'operator', 'pipeline')
    details TEXT,                -- JSON blob with action-specific metadata
    created_at TEXT NOT NULL     -- ISO 8601 UTC
);

-- For time-range queries (e.g., "show me last 24h of actions")
CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON audit_log(created_at DESC);

-- For entity lookups (e.g., "show all actions on signal 42")
CREATE INDEX IF NOT EXISTS idx_audit_log_entity
    ON audit_log(entity_type, entity_id);

-- For action-type queries (e.g., "show all triage_reject actions this week")
CREATE INDEX IF NOT EXISTS idx_audit_log_action
    ON audit_log(action_type, created_at DESC);
"""
