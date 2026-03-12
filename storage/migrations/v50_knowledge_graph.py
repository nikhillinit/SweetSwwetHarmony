"""v50 — Knowledge Graph tables.

SQLite-native knowledge graph layer that formalizes entity types and
relationships into explicit node/edge tables.  Serves RAG/LLM grounding,
pipeline enrichment, and analytics.

Tables:
  kg_runs          — run metadata (full / incremental)
  kg_run_sources   — per-source lifecycle records within a run
  kg_nodes         — typed graph nodes with provenance
  kg_edges         — typed directed edges with temporal validity
  kg_edge_evidence — supporting evidence rows per edge
  kg_provenance    — extraction-run provenance for audit

Views:
  kg_edges_undirected   — canonical undirected (valid_until IS NULL)
  kg_edges_bidirectional — UNION ALL both directions for traversal

Ontology seed rows are inserted for sector and evidence_family node types.
"""

# -- Valid domain enums (enforced by CHECK constraints) ----------------------

VALID_NODE_TYPES = (
    "company",
    "founder",
    "investor",
    "signal",
    "sector",
    "evidence_family",
    "organization",
    "location",
    "skill",
)

VALID_EDGE_TYPES = (
    "detected_by",
    "in_sector",
    "backed_by",
    "merged_into",
    "located_in",
    "has_evidence",
    "founded_by",
    "worked_at",
    "co_founded_with",
    "coworked_with",
)

UNDIRECTED_EDGE_TYPES = frozenset({"co_founded_with", "coworked_with"})

# -- Ontology seed data ------------------------------------------------------

SECTOR_SEEDS = [
    ("sector:cpg", "Consumer CPG"),
    ("sector:health_tech", "Consumer Health Tech"),
    ("sector:travel", "Travel & Hospitality"),
    ("sector:marketplace", "Consumer Marketplaces"),
]

EVIDENCE_FAMILY_SEEDS = [
    ("ef:developer", "developer"),
    ("ef:regulatory", "regulatory"),
    ("ef:web_presence", "web_presence"),
    ("ef:hiring", "hiring"),
    ("ef:public_buzz", "public_buzz"),
    ("ef:unknown", "unknown"),
]

# -- DDL ---------------------------------------------------------------------

_NODE_TYPE_CHECK = ", ".join(f"'{t}'" for t in VALID_NODE_TYPES)
_EDGE_TYPE_CHECK = ", ".join(f"'{t}'" for t in VALID_EDGE_TYPES)

V50_KNOWLEDGE_GRAPH_DDL = f"""
-- ============================================================
-- kg_runs: top-level run metadata
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_runs (
    run_id       TEXT PRIMARY KEY,
    mode         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    completed_at TEXT,
    source_watermark TEXT,
    nodes_upserted    INTEGER DEFAULT 0,
    edges_upserted    INTEGER DEFAULT 0,
    nodes_tombstoned  INTEGER DEFAULT 0,
    edges_expired     INTEGER DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'running',
    CHECK(mode   IN ('full', 'incremental')),
    CHECK(status IN ('running', 'completed', 'failed'))
);

-- ============================================================
-- kg_run_sources: per-source lifecycle records within a run
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_run_sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES kg_runs(run_id),
    source_name      TEXT NOT NULL,
    refresh_strategy TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    started_at       TEXT NOT NULL,
    completed_at     TEXT,
    watermark_before TEXT,
    watermark_after  TEXT,
    rows_scanned     INTEGER DEFAULT 0,
    rows_written     INTEGER DEFAULT 0,
    duration_ms      REAL,
    lock_retries     INTEGER DEFAULT 0,
    error_text       TEXT,
    UNIQUE(run_id, source_name),
    CHECK(status IN ('running', 'completed', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_kg_run_sources_run
    ON kg_run_sources(run_id);

-- ============================================================
-- kg_nodes: typed graph nodes
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_nodes (
    id           TEXT PRIMARY KEY,
    node_type    TEXT NOT NULL,
    label        TEXT,
    properties   TEXT,                -- JSON
    source_table TEXT,
    source_id    TEXT,
    is_tombstone INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_run_id  TEXT,
    CHECK(node_type IN ({_NODE_TYPE_CHECK})),
    CHECK(is_tombstone IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_type
    ON kg_nodes(node_type) WHERE is_tombstone = 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_nodes_source_unique
    ON kg_nodes(source_table, source_id) WHERE source_table IS NOT NULL;

-- ============================================================
-- kg_edges: typed directed edges with temporal validity
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_edges (
    id              TEXT PRIMARY KEY,
    edge_type       TEXT NOT NULL,
    source_node_id  TEXT NOT NULL REFERENCES kg_nodes(id),
    target_node_id  TEXT NOT NULL REFERENCES kg_nodes(id),
    weight          REAL DEFAULT 1.0,
    properties      TEXT,             -- JSON
    valid_from      TEXT,
    valid_until     TEXT,
    is_directed     INTEGER NOT NULL DEFAULT 1,
    last_run_id     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK(edge_type IN ({_EDGE_TYPE_CHECK})),
    CHECK(is_directed IN (0, 1)),
    CHECK(source_node_id != target_node_id)
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source
    ON kg_edges(source_node_id) WHERE valid_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_kg_edges_target
    ON kg_edges(target_node_id) WHERE valid_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_kg_edges_type
    ON kg_edges(edge_type) WHERE valid_until IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_edges_natural
    ON kg_edges(edge_type, source_node_id, target_node_id)
    WHERE valid_until IS NULL;

-- ============================================================
-- kg_edge_evidence: supporting evidence per edge
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_edge_evidence (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id    TEXT NOT NULL REFERENCES kg_edges(id),
    source     TEXT NOT NULL,
    detail     TEXT,                  -- JSON
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kg_edge_evidence_edge
    ON kg_edge_evidence(edge_id);

-- ============================================================
-- kg_provenance: extraction-run provenance for audit
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_provenance (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES kg_runs(run_id),
    node_id    TEXT REFERENCES kg_nodes(id),
    edge_id    TEXT REFERENCES kg_edges(id),
    action     TEXT NOT NULL,
    detail     TEXT,                  -- JSON
    created_at TEXT NOT NULL,
    CHECK(action IN ('create', 'update', 'tombstone', 'expire', 'restore'))
);

CREATE INDEX IF NOT EXISTS idx_kg_provenance_run
    ON kg_provenance(run_id);

CREATE INDEX IF NOT EXISTS idx_kg_provenance_node
    ON kg_provenance(node_id) WHERE node_id IS NOT NULL;

-- ============================================================
-- Views: live edges only
-- ============================================================

-- Undirected view: canonical direction (source < target for undirected)
CREATE VIEW IF NOT EXISTS kg_edges_undirected AS
SELECT id, edge_type, source_node_id, target_node_id,
       weight, properties, valid_from, is_directed,
       last_run_id, created_at, updated_at
FROM kg_edges
WHERE valid_until IS NULL;

-- Bidirectional view: UNION ALL for traversal (undirected edges appear twice)
CREATE VIEW IF NOT EXISTS kg_edges_bidirectional AS
SELECT id, edge_type, source_node_id, target_node_id,
       weight, properties, valid_from, is_directed,
       last_run_id, created_at, updated_at
FROM kg_edges
WHERE valid_until IS NULL
UNION ALL
SELECT id, edge_type, target_node_id AS source_node_id,
       source_node_id AS target_node_id,
       weight, properties, valid_from, is_directed,
       last_run_id, created_at, updated_at
FROM kg_edges
WHERE valid_until IS NULL AND is_directed = 0;

-- ============================================================
-- Ontology seed data: sectors
-- ============================================================
INSERT OR IGNORE INTO kg_nodes (id, node_type, label, source_table, source_id, is_tombstone, created_at, updated_at)
VALUES
    ('sector:cpg',         'sector', 'Consumer CPG',           'ontology', 'sector:cpg',         0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('sector:health_tech', 'sector', 'Consumer Health Tech',   'ontology', 'sector:health_tech', 0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('sector:travel',      'sector', 'Travel & Hospitality',   'ontology', 'sector:travel',      0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('sector:marketplace', 'sector', 'Consumer Marketplaces',  'ontology', 'sector:marketplace', 0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z');

-- ============================================================
-- Ontology seed data: evidence families
-- ============================================================
INSERT OR IGNORE INTO kg_nodes (id, node_type, label, source_table, source_id, is_tombstone, created_at, updated_at)
VALUES
    ('ef:developer',     'evidence_family', 'developer',     'ontology', 'ef:developer',     0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('ef:regulatory',    'evidence_family', 'regulatory',    'ontology', 'ef:regulatory',    0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('ef:web_presence',  'evidence_family', 'web_presence',  'ontology', 'ef:web_presence',  0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('ef:hiring',        'evidence_family', 'hiring',        'ontology', 'ef:hiring',        0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('ef:public_buzz',   'evidence_family', 'public_buzz',   'ontology', 'ef:public_buzz',   0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z'),
    ('ef:unknown',       'evidence_family', 'unknown',       'ontology', 'ef:unknown',       0, '2026-03-12T00:00:00Z', '2026-03-12T00:00:00Z');
"""
