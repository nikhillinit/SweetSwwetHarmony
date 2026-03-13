"""
v51: Confidence Ledger — persists verification gate decisions for audit/debugging.

Stores the full ConfidenceBreakdown.to_dict() output alongside decision envelope,
enabling operators to inspect *why* a company was routed to a particular status.

Phase 1: captures pipeline gate decisions only (evaluation_origin='pipeline').
"""

V51_CONFIDENCE_LEDGER_DDL = """
-- ============================================================
-- confidence_ledger: audit trail for verification gate decisions
-- ============================================================
CREATE TABLE IF NOT EXISTS confidence_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id        TEXT,
    canonical_key       TEXT NOT NULL,
    company_id          TEXT,
    evaluation_origin   TEXT NOT NULL DEFAULT 'pipeline',
    is_dry_run          INTEGER NOT NULL DEFAULT 0,
    breakdown_kind      TEXT NOT NULL DEFAULT 'normal',
    -- Score semantics: decision uses gate_score, NOT reported_score
    gate_score          REAL NOT NULL,
    reported_score      REAL NOT NULL,
    -- Scoring components (denormalized; meaningful only when breakdown_kind='normal')
    base_score          REAL NOT NULL,
    multi_source_boost  REAL NOT NULL DEFAULT 1.0,
    convergence_boost   REAL NOT NULL DEFAULT 1.0,
    founder_boost       REAL NOT NULL DEFAULT 0.0,
    velocity_boost      REAL NOT NULL DEFAULT 0.0,
    enrichment_boost    REAL NOT NULL DEFAULT 0.0,
    community_sentiment_boost REAL NOT NULL DEFAULT 0.0,
    recalibration_factor REAL NOT NULL DEFAULT 1.0,
    policy_version      TEXT NOT NULL,
    breakdown_schema_version TEXT NOT NULL DEFAULT '1.0',
    signals_contributing INTEGER NOT NULL DEFAULT 0,
    sources_checked     INTEGER NOT NULL DEFAULT 0,
    -- Decision envelope
    decision            TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    reason              TEXT NOT NULL,
    -- Full payloads
    breakdown_json      TEXT NOT NULL,
    details_json        TEXT NOT NULL DEFAULT '[]',
    signal_ids_json     TEXT NOT NULL DEFAULT '[]',
    routing_config_json TEXT,
    -- Timestamps (app-side ISO UTC)
    evaluated_at        TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    -- JSON validity + shape constraints
    CHECK(json_valid(breakdown_json)),
    CHECK(json_type(breakdown_json) = 'object'),
    CHECK(json_valid(details_json)),
    CHECK(json_type(details_json) = 'array'),
    CHECK(json_valid(signal_ids_json)),
    CHECK(json_type(signal_ids_json) = 'array'),
    CHECK(routing_config_json IS NULL OR (json_valid(routing_config_json) AND json_type(routing_config_json) = 'object')),
    -- Pipeline rows MUST have routing config
    CHECK(evaluation_origin != 'pipeline' OR routing_config_json IS NOT NULL),
    -- Enum constraints
    CHECK(decision IN ('auto_push', 'needs_review', 'hold', 'reject')),
    CHECK(verification_status IN ('unverified', 'single_source', 'multi_source', 'conflicting', 'failed')),
    CHECK(evaluation_origin IN ('pipeline', 'pusher')),
    CHECK(breakdown_kind IN ('normal', 'hard_kill', 'empty_signals')),
    CHECK(is_dry_run IN (0, 1)),
    -- Range constraints
    CHECK(gate_score >= 0.0 AND gate_score <= 1.0),
    CHECK(reported_score >= 0.0 AND reported_score <= 1.0),
    CHECK(signals_contributing >= 0),
    CHECK(sources_checked >= 0)
);

-- Composite indexes for actual query paths in get_confidence_ledger()
CREATE INDEX IF NOT EXISTS idx_confidence_ledger_key_nondry
    ON confidence_ledger(canonical_key, is_dry_run, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_confidence_ledger_company_nondry
    ON confidence_ledger(company_id, is_dry_run, evaluated_at DESC);
-- General-purpose indexes
CREATE INDEX IF NOT EXISTS idx_confidence_ledger_evaluated
    ON confidence_ledger(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_confidence_ledger_decision
    ON confidence_ledger(decision);
CREATE INDEX IF NOT EXISTS idx_confidence_ledger_execution
    ON confidence_ledger(execution_id);
"""
