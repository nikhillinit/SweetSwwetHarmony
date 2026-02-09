"""v29: ReviewItem state machine + CompanyFile thin files.

ReviewItem tracks the review lifecycle for promoted companies.
CompanyFile accumulates signals into thin files per company_id.
"""

V29_REVIEW_QUEUE_DDL = """
-- ============================================================================
-- v29: REVIEW QUEUE + COMPANY FILES
-- ============================================================================

-- ReviewItem state machine
CREATE TABLE review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'approved', 'rejected', 'deferred', 'published', 'publish_queued'
    )),
    evidence_bundle TEXT NOT NULL,  -- JSON: {"signal_ids": [...], "schema_version": 1}
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT
);

-- Only one active review per company (pending/approved/publish_queued)
-- Deferred excluded by design: new evidence creates fresh pending review
CREATE UNIQUE INDEX idx_review_one_active_per_company
    ON review_items(company_id)
    WHERE status IN ('pending', 'approved', 'publish_queued');

CREATE INDEX idx_review_status_created
    ON review_items(status, created_at);

CREATE INDEX idx_review_company_id
    ON review_items(company_id);


-- CompanyFile thin files (signal accumulator per company)
CREATE TABLE company_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL UNIQUE,
    company_name TEXT,
    canonical_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('thin', 'promoted', 'archived')),
    source_apis TEXT NOT NULL DEFAULT '[]',  -- JSON array of source_api strings
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    promoted_at TEXT,
    archived_at TEXT,
    metadata TEXT  -- JSON for extensibility (e.g., manual_promotion flag)
);

CREATE INDEX idx_company_file_status_seen
    ON company_files(status, last_seen_at, company_id);
"""
