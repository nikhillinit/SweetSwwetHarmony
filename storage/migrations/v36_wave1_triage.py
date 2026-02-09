"""Migration v36: Wave 1 triage infrastructure.

Adds:
- idempotency_keys: SQLite-backed idempotency storage (survives process restart).
  Scoped to (key, route, resource_id) with 24h TTL and periodic cleanup.
- idx_review_updated_id: Pagination index for triage list queries on review_items.
"""

V36_WAVE1_TRIAGE_DDL = """
-- ============================================================================
-- v36: IDEMPOTENCY KEYS (SQLite-backed, survives restart)
-- ============================================================================

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT NOT NULL,
    route TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    payload_hash TEXT,
    status_code INTEGER NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (key, route, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_created
    ON idempotency_keys(created_at);

-- ============================================================================
-- v36: REVIEW ITEMS PAGINATION INDEX
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_review_updated_id
    ON review_items(updated_at DESC, id DESC);
"""
