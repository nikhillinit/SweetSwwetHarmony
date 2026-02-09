"""Migration v31: Batch publish tables.

Adds publish_batches (batch lifecycle) and batch_items (per-item tracking)
for the git-style create → preview → commit/abort workflow.
"""

V31_BATCH_PUBLISH_DDL = """
-- ============================================================================
-- v31: BATCH PUBLISH WORKFLOW
-- ============================================================================

CREATE TABLE IF NOT EXISTS publish_batches (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN (
        'draft', 'committing', 'committed', 'committed_with_errors', 'aborted'
    )),
    item_count INTEGER NOT NULL DEFAULT 0,
    pushed_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL DEFAULT 'operator',
    created_at TEXT NOT NULL,
    committed_at TEXT,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_publish_batches_status
    ON publish_batches(status);

CREATE INDEX IF NOT EXISTS idx_publish_batches_created
    ON publish_batches(created_at DESC);

CREATE TABLE IF NOT EXISTS batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES publish_batches(id) ON DELETE CASCADE,
    review_id INTEGER NOT NULL,
    company_id TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'in_progress', 'pushed', 'skipped', 'error'
    )),
    notion_page_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, review_id)
);

CREATE INDEX IF NOT EXISTS idx_batch_items_batch_id
    ON batch_items(batch_id);

CREATE INDEX IF NOT EXISTS idx_batch_items_batch_status
    ON batch_items(batch_id, status);
"""
