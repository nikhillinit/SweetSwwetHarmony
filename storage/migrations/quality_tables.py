"""Quality Ops table definitions — single source of truth.

Imported by:
  - storage/signal_store.py (migration 25)
  - ops/quality/db.py (ensure_quality_tables standalone path)
"""
QUALITY_TABLES_DDL = """
-- ============================================================================
-- QUALITY OPS TABLES
-- ============================================================================

-- 1) notion_status_events
-- Stores observed status transitions from Notion (or more generally: the CRM).
-- This is an EVENT LOG: each row is an observation at time t.
CREATE TABLE IF NOT EXISTS notion_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL,
    notion_page_id TEXT,
    old_status TEXT,
    new_status TEXT NOT NULL,
    observed_at TEXT NOT NULL,   -- ISO 8601 (UTC)
    source TEXT NOT NULL,        -- e.g., 'sync_suppression'
    metadata TEXT                -- JSON
);

CREATE INDEX IF NOT EXISTS idx_notion_status_events_key_time
    ON notion_status_events(canonical_key, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_notion_status_events_new_status_time
    ON notion_status_events(new_status, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_notion_status_events_page
    ON notion_status_events(notion_page_id);

-- 2) quality_feedback
-- Raw/manual feedback (audit trail). Multiple rows per signal allowed.
CREATE TABLE IF NOT EXISTS quality_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    label TEXT CHECK(label IN ('TP', 'FP', 'UNSURE')) NOT NULL,
    reason TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,    -- ISO 8601 (UTC)
    metadata TEXT,
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_feedback_signal
    ON quality_feedback(signal_id);

CREATE INDEX IF NOT EXISTS idx_quality_feedback_created
    ON quality_feedback(created_at DESC);

-- 3) signal_quality_metrics
-- Latest "resolved" label per signal (1 row per signal).
-- Sources can be manual feedback, inferred from Notion status, etc.
CREATE TABLE IF NOT EXISTS signal_quality_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    canonical_key TEXT NOT NULL,
    human_label TEXT CHECK(human_label IN ('TP', 'FP', 'UNSURE')) NOT NULL,
    label_source TEXT NOT NULL,  -- 'manual', 'notion_status_event', 'notion_snapshot', 'auto'
    labeled_by TEXT,
    labeled_at TEXT NOT NULL,    -- ISO 8601 (UTC)

    -- Optional linkage to Notion
    notion_page_id TEXT,
    notion_status TEXT,

    -- Optional linkage to event that drove outcome
    status_event_id INTEGER,
    days_to_outcome REAL,

    notes TEXT,
    metadata TEXT,

    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE,
    FOREIGN KEY(status_event_id) REFERENCES notion_status_events(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_quality_label
    ON signal_quality_metrics(human_label);

CREATE INDEX IF NOT EXISTS idx_signal_quality_source
    ON signal_quality_metrics(label_source);

CREATE INDEX IF NOT EXISTS idx_signal_quality_labeled_at
    ON signal_quality_metrics(labeled_at DESC);

CREATE INDEX IF NOT EXISTS idx_signal_quality_key
    ON signal_quality_metrics(canonical_key);
"""
