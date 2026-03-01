"""v47 — Governance triggers for audit_events metadata enforcement.

SQLite BEFORE INSERT triggers that enforce metadata contracts at the DB level
for governance action types (feature_promote, regret_check, feature_demote).

These triggers act as a safety net — the primary validation happens in
governance/contracts.py (Pydantic) and governance/writer.py. The triggers
catch any direct SQL INSERT that bypasses the writer.
"""

V47_GOVERNANCE_TRIGGERS_DDL = """
-- =============================================================================
-- GOVERNANCE TRIGGERS — DB-level metadata enforcement
-- =============================================================================

-- Trigger: feature_promote events must have required metadata
CREATE TRIGGER IF NOT EXISTS trg_audit_feature_promote_metadata
BEFORE INSERT ON audit_events
WHEN NEW.action_type = 'feature_promote'
BEGIN
    SELECT CASE
        WHEN NEW.metadata IS NULL THEN
            RAISE(ABORT, 'feature_promote: metadata must not be NULL')
        WHEN NOT json_valid(NEW.metadata) THEN
            RAISE(ABORT, 'feature_promote: metadata must be valid JSON')
        WHEN json_extract(NEW.metadata, '$.feature_name') IS NULL THEN
            RAISE(ABORT, 'feature_promote: metadata.feature_name is required')
        WHEN json_extract(NEW.metadata, '$.from_state') IS NULL THEN
            RAISE(ABORT, 'feature_promote: metadata.from_state is required')
        WHEN json_extract(NEW.metadata, '$.to_state') IS NULL THEN
            RAISE(ABORT, 'feature_promote: metadata.to_state is required')
        WHEN json_extract(NEW.metadata, '$.regret_due_at') IS NULL THEN
            RAISE(ABORT, 'feature_promote: metadata.regret_due_at is required')
        WHEN json_extract(NEW.metadata, '$.config_snapshot_hash') IS NULL THEN
            RAISE(ABORT, 'feature_promote: metadata.config_snapshot_hash is required')
    END;
END;

-- Trigger: regret_check events must have required metadata
CREATE TRIGGER IF NOT EXISTS trg_audit_regret_check_metadata
BEFORE INSERT ON audit_events
WHEN NEW.action_type = 'regret_check'
BEGIN
    SELECT CASE
        WHEN NEW.metadata IS NULL THEN
            RAISE(ABORT, 'regret_check: metadata must not be NULL')
        WHEN NOT json_valid(NEW.metadata) THEN
            RAISE(ABORT, 'regret_check: metadata must be valid JSON')
        WHEN json_extract(NEW.metadata, '$.verdict') IS NULL THEN
            RAISE(ABORT, 'regret_check: metadata.verdict is required')
        WHEN json_extract(NEW.metadata, '$.canary_verdict') IS NULL THEN
            RAISE(ABORT, 'regret_check: metadata.canary_verdict is required')
        WHEN json_extract(NEW.metadata, '$.drift_status') IS NULL THEN
            RAISE(ABORT, 'regret_check: metadata.drift_status is required')
    END;
END;

-- Trigger: feature_demote events must have required metadata
CREATE TRIGGER IF NOT EXISTS trg_audit_feature_demote_metadata
BEFORE INSERT ON audit_events
WHEN NEW.action_type = 'feature_demote'
BEGIN
    SELECT CASE
        WHEN NEW.metadata IS NULL THEN
            RAISE(ABORT, 'feature_demote: metadata must not be NULL')
        WHEN NOT json_valid(NEW.metadata) THEN
            RAISE(ABORT, 'feature_demote: metadata must be valid JSON')
        WHEN json_extract(NEW.metadata, '$.from_state') IS NULL THEN
            RAISE(ABORT, 'feature_demote: metadata.from_state is required')
        WHEN json_extract(NEW.metadata, '$.to_state') IS NULL THEN
            RAISE(ABORT, 'feature_demote: metadata.to_state is required')
    END;
END;
"""
