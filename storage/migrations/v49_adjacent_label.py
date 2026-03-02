"""v49 — Add ADJ (thesis-adjacent) label to CHECK constraints.

SQLite can't ALTER CHECK constraints, so we rebuild both tables:
- quality_feedback: CHECK(label IN ('TP','FP','UNSURE','ADJ'))
- signal_quality_metrics: CHECK(human_label IN ('TP','FP','UNSURE','ADJ'))

Pattern: ensure source table exists (IF NOT EXISTS) → CREATE new → INSERT SELECT
→ DROP old → RENAME new → recreate indexes.

The initial CREATE IF NOT EXISTS guards against DBs where quality tables were never
created (e.g. test DBs bootstrapped at a schema version > 25).
"""

V49_ADJACENT_LABEL_DDL = """
-- ---------------------------------------------------------------
-- 0) Ensure prerequisite tables exist (no-op on normal DBs)
-- ---------------------------------------------------------------
-- notion_status_events is needed for FK reference in signal_quality_metrics.
-- On test DBs bootstrapped at schema > 25, this table may be absent.
CREATE TABLE IF NOT EXISTS notion_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL,
    new_status TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL
);

-- ---------------------------------------------------------------
-- 1) Rebuild quality_feedback with ADJ in CHECK
-- ---------------------------------------------------------------

-- Ensure source table exists (no-op if already present from v25)
CREATE TABLE IF NOT EXISTS quality_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    reason TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS quality_feedback_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    label TEXT CHECK(label IN ('TP', 'FP', 'UNSURE', 'ADJ')) NOT NULL,
    reason TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    metadata TEXT,
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
);

INSERT OR IGNORE INTO quality_feedback_new
    (id, signal_id, label, reason, notes, created_by, created_at, metadata)
SELECT id, signal_id, label, reason, notes, created_by, created_at, metadata
FROM quality_feedback;

DROP TABLE IF EXISTS quality_feedback;

ALTER TABLE quality_feedback_new RENAME TO quality_feedback;

CREATE INDEX IF NOT EXISTS idx_quality_feedback_signal
    ON quality_feedback(signal_id);

CREATE INDEX IF NOT EXISTS idx_quality_feedback_created
    ON quality_feedback(created_at DESC);


-- ---------------------------------------------------------------
-- 2) Rebuild signal_quality_metrics with ADJ in CHECK
-- ---------------------------------------------------------------

-- Ensure source table exists (no-op if already present from v25)
CREATE TABLE IF NOT EXISTS signal_quality_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    canonical_key TEXT NOT NULL,
    human_label TEXT NOT NULL,
    label_source TEXT NOT NULL,
    labeled_by TEXT,
    labeled_at TEXT NOT NULL,
    notion_page_id TEXT,
    notion_status TEXT,
    status_event_id INTEGER,
    days_to_outcome REAL,
    notes TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS signal_quality_metrics_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL UNIQUE,
    canonical_key TEXT NOT NULL,
    human_label TEXT CHECK(human_label IN ('TP', 'FP', 'UNSURE', 'ADJ')) NOT NULL,
    label_source TEXT NOT NULL,
    labeled_by TEXT,
    labeled_at TEXT NOT NULL,

    notion_page_id TEXT,
    notion_status TEXT,

    status_event_id INTEGER,
    days_to_outcome REAL,

    notes TEXT,
    metadata TEXT,

    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE,
    FOREIGN KEY(status_event_id) REFERENCES notion_status_events(id) ON DELETE SET NULL
);

INSERT OR IGNORE INTO signal_quality_metrics_new
    (id, signal_id, canonical_key, human_label, label_source, labeled_by, labeled_at,
     notion_page_id, notion_status, status_event_id, days_to_outcome, notes, metadata)
SELECT id, signal_id, canonical_key, human_label, label_source, labeled_by, labeled_at,
       notion_page_id, notion_status, status_event_id, days_to_outcome, notes, metadata
FROM signal_quality_metrics;

DROP TABLE IF EXISTS signal_quality_metrics;

ALTER TABLE signal_quality_metrics_new RENAME TO signal_quality_metrics;

CREATE INDEX IF NOT EXISTS idx_signal_quality_label
    ON signal_quality_metrics(human_label);

CREATE INDEX IF NOT EXISTS idx_signal_quality_source
    ON signal_quality_metrics(label_source);

CREATE INDEX IF NOT EXISTS idx_signal_quality_labeled_at
    ON signal_quality_metrics(labeled_at DESC);

CREATE INDEX IF NOT EXISTS idx_signal_quality_key
    ON signal_quality_metrics(canonical_key);
"""
