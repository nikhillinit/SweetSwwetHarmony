"""Migration v35: Platform hardening tables (Wave 0).

Adds:
- audit_events: Enhanced immutable audit log with before/after state,
  reason, correlation_id (supplements v27 audit_log).
- run_history: Generic run/job tracking for all async workflows
  (hunter, canary, ACH, entity resolution, etc.).
"""

V35_PLATFORM_HARDENING_DDL = """
-- ============================================================================
-- v35: AUDIT EVENTS (enhanced immutable log)
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_email TEXT,
    actor_role TEXT,
    before_state TEXT,           -- JSON snapshot
    after_state TEXT,            -- JSON snapshot
    reason TEXT,                 -- operator justification
    correlation_id TEXT,         -- X-Request-ID
    metadata TEXT,               -- JSON action-specific data
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created
    ON audit_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_entity
    ON audit_events(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_audit_events_action
    ON audit_events(action_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_actor
    ON audit_events(actor_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_correlation
    ON audit_events(correlation_id);

-- ============================================================================
-- v35: RUN HISTORY (generic async job tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS run_history (
    id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
        'queued', 'running', 'completed', 'failed', 'cancelled'
    )),
    actor_id TEXT,
    actor_email TEXT,
    inputs_summary TEXT,         -- JSON summary of run inputs
    inputs_hash TEXT,            -- SHA256[:16] for reproducibility
    result TEXT,                 -- JSON output (populated on completion)
    error_message TEXT,
    progress_pct INTEGER,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_history_type_status
    ON run_history(run_type, status);

CREATE INDEX IF NOT EXISTS idx_run_history_created
    ON run_history(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_run_history_status
    ON run_history(status);
"""
