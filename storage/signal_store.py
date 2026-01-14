"""
Signal Storage Layer for Discovery Engine

Provides persistent SQLite storage for signals with:
- Deduplication via canonical keys
- Processing state tracking
- Notion suppression cache
- Migration support
- Connection pooling via aiosqlite

Tables:
  - signals: Raw signals from collectors
  - signal_processing: Processing state and Notion linkage
  - suppression_cache: Local cache of Notion DB to avoid duplicate pushes
  - schema_migrations: Track applied migrations

Usage:
    store = SignalStore("signals.db")
    await store.initialize()

    # Save a signal
    signal_id = await store.save_signal({
        "signal_type": "github_spike",
        "source_api": "github",
        "canonical_key": "domain:acme.ai",
        "company_name": "Acme Inc",
        "confidence": 0.85,
        "raw_data": {...}
    })

    # Check for duplicates
    is_dup = await store.is_duplicate("domain:acme.ai")

    # Get pending signals
    pending = await store.get_pending_signals()

    # Mark as pushed
    await store.mark_pushed(signal_id, notion_page_id="abc-123")
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncIterator, TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from workflows.pipeline import PipelineStats, CollectorMetrics

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA VERSION
# =============================================================================

CURRENT_SCHEMA_VERSION = 11

# SQL for creating tables (migrations applied in order)
MIGRATIONS = {
    1: """
    -- Signals table: raw signals from collectors
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_type TEXT NOT NULL,
        source_api TEXT NOT NULL,
        canonical_key TEXT NOT NULL,
        company_name TEXT,
        confidence REAL NOT NULL,
        raw_data TEXT NOT NULL,  -- JSON
        detected_at TEXT NOT NULL,  -- ISO 8601
        created_at TEXT NOT NULL,  -- ISO 8601

        -- Indexes for fast lookups
        UNIQUE(canonical_key, signal_type, source_api, detected_at)
    );

    CREATE INDEX IF NOT EXISTS idx_signals_canonical_key ON signals(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_signals_signal_type ON signals(signal_type);
    CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
    CREATE INDEX IF NOT EXISTS idx_signals_detected_at ON signals(detected_at);

    -- Signal processing: track what's been pushed/rejected
    CREATE TABLE IF NOT EXISTS signal_processing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        status TEXT NOT NULL,  -- 'pending', 'queued', 'pushed', 'rejected'
        notion_page_id TEXT,
        processed_at TEXT,  -- ISO 8601
        error_message TEXT,
        metadata TEXT,  -- JSON for extra context
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_processing_signal_id ON signal_processing(signal_id);
    CREATE INDEX IF NOT EXISTS idx_processing_status ON signal_processing(status);
    CREATE INDEX IF NOT EXISTS idx_processing_notion_page_id ON signal_processing(notion_page_id);

    -- Suppression cache: local copy of what's in Notion
    CREATE TABLE IF NOT EXISTS suppression_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,
        notion_page_id TEXT NOT NULL,
        status TEXT NOT NULL,  -- Notion status: Source, Tracking, etc.
        company_name TEXT,
        cached_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        metadata TEXT  -- JSON for extra Notion fields
    );

    CREATE INDEX IF NOT EXISTS idx_suppression_canonical_key ON suppression_cache(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_suppression_notion_page_id ON suppression_cache(notion_page_id);
    CREATE INDEX IF NOT EXISTS idx_suppression_expires_at ON suppression_cache(expires_at);

    -- Schema migrations tracking
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT
    );
    """,
    2: """
    -- Pipeline runs: track pipeline execution metrics
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        started_at TEXT NOT NULL,  -- ISO 8601
        completed_at TEXT,  -- ISO 8601
        duration_seconds REAL,

        -- Collector stats
        collectors_run INTEGER NOT NULL DEFAULT 0,
        collectors_succeeded INTEGER NOT NULL DEFAULT 0,
        collectors_failed INTEGER NOT NULL DEFAULT 0,
        signals_collected INTEGER NOT NULL DEFAULT 0,

        -- Storage stats
        signals_stored INTEGER NOT NULL DEFAULT 0,
        signals_deduplicated INTEGER NOT NULL DEFAULT 0,

        -- Verification stats
        signals_processed INTEGER NOT NULL DEFAULT 0,
        signals_auto_push INTEGER NOT NULL DEFAULT 0,
        signals_needs_review INTEGER NOT NULL DEFAULT 0,
        signals_held INTEGER NOT NULL DEFAULT 0,
        signals_rejected INTEGER NOT NULL DEFAULT 0,

        -- Notion stats
        prospects_created INTEGER NOT NULL DEFAULT 0,
        prospects_updated INTEGER NOT NULL DEFAULT 0,
        prospects_skipped INTEGER NOT NULL DEFAULT 0,

        -- Errors and health
        errors TEXT,  -- JSON array
        health_report TEXT,  -- JSON object

        created_at TEXT NOT NULL  -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_run_id ON pipeline_runs(run_id);
    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at ON pipeline_runs(started_at);
    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_created_at ON pipeline_runs(created_at);
    """
    ,
    3: """
    -- Notion outbox: durable queue for Notion writes
    CREATE TABLE IF NOT EXISTS notion_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotency_key TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,  -- 'pending', 'sent', 'failed'
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_outbox_status ON notion_outbox(status);
    CREATE INDEX IF NOT EXISTS idx_outbox_next_attempt ON notion_outbox(next_attempt_at);
    CREATE INDEX IF NOT EXISTS idx_outbox_created_at ON notion_outbox(created_at);
    """,
    4: """
    -- Collector metrics: per-collector timing and API stats
    CREATE TABLE IF NOT EXISTS collector_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        collector_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        duration_seconds REAL,
        signals_found INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        api_calls INTEGER NOT NULL DEFAULT 0,
        rate_limit_hits INTEGER NOT NULL DEFAULT 0,
        retries INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        error_messages TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_collector_metrics_run_id ON collector_metrics(run_id);
    CREATE INDEX IF NOT EXISTS idx_collector_metrics_collector ON collector_metrics(collector_name);
    CREATE INDEX IF NOT EXISTS idx_collector_metrics_started_at ON collector_metrics(started_at);
    """,
    5: """
    -- Thesis classifications: persist LLM classification results
    CREATE TABLE IF NOT EXISTS thesis_classifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        canonical_key TEXT NOT NULL,

        -- Keyword matcher results (stage 1)
        keyword_score REAL,
        keyword_category TEXT,
        negative_keywords TEXT,  -- JSON array

        -- LLM classifier results (stage 2)
        thesis_match BOOLEAN,
        thesis_fit_score REAL,
        category TEXT,
        stage_estimate TEXT,
        confidence TEXT,
        rationale TEXT,
        key_signals TEXT,  -- JSON array

        -- Audit trail
        prompt_version TEXT,
        model TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms INTEGER,

        -- Competitor detection
        competitor_flag BOOLEAN DEFAULT 0,
        competitor_match TEXT,  -- JSON: matched portfolio company

        classified_at TEXT NOT NULL,  -- ISO 8601

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_thesis_class_signal_id ON thesis_classifications(signal_id);
    CREATE INDEX IF NOT EXISTS idx_thesis_class_canonical ON thesis_classifications(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_thesis_class_category ON thesis_classifications(category);
    CREATE INDEX IF NOT EXISTS idx_thesis_class_classified_at ON thesis_classifications(classified_at);
    """,
    6: """
    -- Signal correlation: link signals to founders (Deal Intelligence Engine Phase 1)
    ALTER TABLE signals ADD COLUMN correlated_founder_id INTEGER;
    ALTER TABLE signals ADD COLUMN correlation_confidence REAL;
    ALTER TABLE signals ADD COLUMN correlation_type TEXT;

    CREATE INDEX IF NOT EXISTS idx_signals_correlated_founder ON signals(correlated_founder_id);
    """,
    7: """
    -- Traction scores: momentum metrics (Deal Intelligence Engine Phase 2)
    CREATE TABLE IF NOT EXISTS traction_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL,

        -- GitHub momentum
        github_stars_growth_30d REAL DEFAULT 0.0,
        github_commit_velocity REAL DEFAULT 0.0,

        -- Hiring velocity
        job_posting_velocity REAL DEFAULT 0.0,
        job_count_growth_30d REAL DEFAULT 0.0,

        -- Social momentum
        ph_vote_growth_30d REAL DEFAULT 0.0,
        hn_mention_growth_30d REAL DEFAULT 0.0,

        -- Composite score
        composite_momentum REAL NOT NULL DEFAULT 0.0,
        momentum_percentile INTEGER DEFAULT 50,

        -- Audit
        calculated_at TEXT NOT NULL,  -- ISO 8601

        UNIQUE(canonical_key)
    );

    CREATE INDEX IF NOT EXISTS idx_traction_canonical ON traction_scores(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_traction_composite ON traction_scores(composite_momentum);
    CREATE INDEX IF NOT EXISTS idx_traction_calculated ON traction_scores(calculated_at);
    """,
    8: """
    -- Investor network: centrality rankings (Deal Intelligence Engine Phase 3)
    CREATE TABLE IF NOT EXISTS investor_rankings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        investor_name TEXT NOT NULL UNIQUE,

        -- Network metrics
        centrality_score REAL NOT NULL DEFAULT 0.0,
        coinvestment_count INTEGER NOT NULL DEFAULT 0,
        rank INTEGER,

        -- Audit
        calculated_at TEXT NOT NULL  -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_investor_name ON investor_rankings(investor_name);
    CREATE INDEX IF NOT EXISTS idx_investor_centrality ON investor_rankings(centrality_score);
    CREATE INDEX IF NOT EXISTS idx_investor_rank ON investor_rankings(rank);

    -- Company investor scores
    CREATE TABLE IF NOT EXISTS company_investor_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,

        -- Investor quality metrics
        investor_quality_score REAL NOT NULL DEFAULT 0.0,
        top_investor TEXT,
        known_investors_count INTEGER DEFAULT 0,
        total_investors_count INTEGER DEFAULT 0,

        -- Audit
        calculated_at TEXT NOT NULL  -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_company_investor_canonical ON company_investor_scores(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_company_investor_quality ON company_investor_scores(investor_quality_score);
    """,
    9: """
    -- Founder intent signals (Deal Intelligence Engine Phase 4)
    CREATE TABLE IF NOT EXISTS founder_intent_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        canonical_key TEXT NOT NULL,

        -- Intent detection
        intent_type TEXT NOT NULL,  -- 'new_venture', 'activity_spike', 'career_transition', 'stealth_mode', 'cofounder_seeking'
        founder_id INTEGER NOT NULL,
        founder_name TEXT,
        confidence REAL NOT NULL DEFAULT 0.0,
        evidence TEXT,

        -- Audit
        detected_at TEXT NOT NULL,  -- ISO 8601

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_intent_signal_id ON founder_intent_signals(signal_id);
    CREATE INDEX IF NOT EXISTS idx_intent_canonical ON founder_intent_signals(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_intent_founder ON founder_intent_signals(founder_id);
    CREATE INDEX IF NOT EXISTS idx_intent_type ON founder_intent_signals(intent_type);
    CREATE INDEX IF NOT EXISTS idx_intent_detected ON founder_intent_signals(detected_at);
    """,
    10: """
    -- Deal quality scores (Deal Intelligence Engine Phase 5)
    CREATE TABLE IF NOT EXISTS deal_quality_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,

        -- Component scores [0, 1]
        thesis_fit REAL NOT NULL DEFAULT 0.0,
        traction REAL NOT NULL DEFAULT 0.0,
        investor_quality REAL NOT NULL DEFAULT 0.0,
        founder REAL NOT NULL DEFAULT 0.0,

        -- Unified score
        raw_score REAL NOT NULL DEFAULT 0.0,  -- Weighted combination
        percentile REAL NOT NULL DEFAULT 0.5,  -- Rank vs historical

        -- Routing
        routing_recommendation TEXT NOT NULL,  -- 'source', 'tracking', 'hold', 'pass'

        -- Audit
        calculated_at TEXT NOT NULL  -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_deal_quality_canonical ON deal_quality_scores(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_deal_quality_raw_score ON deal_quality_scores(raw_score);
    CREATE INDEX IF NOT EXISTS idx_deal_quality_percentile ON deal_quality_scores(percentile);
    CREATE INDEX IF NOT EXISTS idx_deal_quality_routing ON deal_quality_scores(routing_recommendation);
    """,
    11: """
    -- Signal embeddings for semantic search (Deal Intelligence Engine Phase 6)
    CREATE TABLE IF NOT EXISTS signal_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        canonical_key TEXT NOT NULL,

        -- Embedding vector stored as JSON array
        embedding TEXT NOT NULL,  -- JSON array of 768 floats

        -- Searchable text that was embedded
        text TEXT NOT NULL,
        company_name TEXT,

        -- Audit
        created_at TEXT NOT NULL,  -- ISO 8601

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE,
        UNIQUE(signal_id)  -- One embedding per signal
    );

    CREATE INDEX IF NOT EXISTS idx_embedding_signal_id ON signal_embeddings(signal_id);
    CREATE INDEX IF NOT EXISTS idx_embedding_canonical ON signal_embeddings(canonical_key);
    """
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StoredSignal:
    """A signal loaded from the database"""
    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    company_name: Optional[str]
    confidence: float
    raw_data: Dict[str, Any]
    detected_at: datetime
    created_at: datetime

    # Processing info (if joined)
    processing_status: Optional[str] = None
    notion_page_id: Optional[str] = None
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class SuppressionEntry:
    """Entry in the suppression cache"""
    canonical_key: str
    notion_page_id: str
    status: str
    company_name: Optional[str] = None
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=7))
    metadata: Optional[Dict[str, Any]] = None


# =============================================================================
# SIGNAL STORE
# =============================================================================

class SignalStore:
    """
    Async SQLite storage for Discovery Engine signals.

    Features:
    - Connection pooling via aiosqlite
    - Automatic schema migrations
    - Transaction support
    - JSON serialization for complex fields
    - TTL-based suppression cache
    """

    def __init__(
        self,
        db_path: str | Path = "signals.db",
        suppression_ttl_days: int = 7,
    ):
        """
        Initialize signal store.

        Args:
            db_path: Path to SQLite database file
            suppression_ttl_days: How long to cache Notion entries before re-checking
        """
        self.db_path = Path(db_path)
        self.suppression_ttl_days = suppression_ttl_days
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Initialize database connection and apply migrations.
        Should be called once at startup.
        """
        # Create parent directories if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to database
        self._db = await aiosqlite.connect(str(self.db_path))

        # Enable foreign keys
        await self._db.execute("PRAGMA foreign_keys = ON")

        # Apply migrations
        await self._apply_migrations()

        # Create FTS5 virtual table for search
        await self._create_fts_table()

        # Create filter presets table
        await self._create_filter_presets_table()

        logger.info(f"SignalStore initialized: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Context manager for transactions.

        Usage:
            async with store.transaction() as conn:
                await conn.execute(...)
                await conn.execute(...)
                # Commits on success, rolls back on exception
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        async with self._lock:
            try:
                await self._db.execute("BEGIN")
                yield self._db
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise

    # =========================================================================
    # MIGRATIONS
    # =========================================================================

    async def _apply_migrations(self) -> None:
        """Apply pending schema migrations."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Get current version
        try:
            cursor = await self._db.execute(
                "SELECT MAX(version) FROM schema_migrations"
            )
            row = await cursor.fetchone()
            current_version = row[0] if row and row[0] else 0
        except aiosqlite.OperationalError:
            # Table doesn't exist yet
            current_version = 0

        # Apply each pending migration
        for version in sorted(MIGRATIONS.keys()):
            if version <= current_version:
                continue

            logger.info(f"Applying migration v{version}...")

            async with self.transaction() as conn:
                # Execute migration SQL
                await conn.executescript(MIGRATIONS[version])

                # Record migration
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (?, ?, ?)
                    """,
                    (
                        version,
                        datetime.now(timezone.utc).isoformat(),
                        f"Schema version {version}"
                    )
                )

            logger.info(f"Migration v{version} applied successfully")

    async def _create_fts_table(self) -> None:
        """Create FTS5 virtual table for fuzzy search."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS signals_fts USING fts5(
                signal_id UNINDEXED,
                company_name,
                searchable_text,
                vertical,
                source_api,
                tokenize='porter unicode61'
            )
        """)
        await self._db.commit()

    async def _create_filter_presets_table(self) -> None:
        """Create filter presets table for saved searches."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS filter_presets (
                preset_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                filters TEXT NOT NULL,
                schema_version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                last_used TIMESTAMP
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_presets_name ON filter_presets(name)"
        )
        await self._db.commit()

    # =========================================================================
    # FILTER PRESET CRUD OPERATIONS
    # =========================================================================

    async def save_filter_preset(self, name: str, filters: Dict[str, Any]) -> str:
        """Save a new filter preset."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Check for duplicate
        cursor = await self._db.execute(
            "SELECT preset_id FROM filter_presets WHERE name = ?", (name,)
        )
        if await cursor.fetchone():
            raise ValueError(f"Preset '{name}' already exists")

        preset_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        await self._db.execute("""
            INSERT INTO filter_presets (preset_id, name, filters, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (preset_id, name, json.dumps(filters), now, now))
        await self._db.commit()

        return preset_id

    async def load_filter_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a filter preset by name, updating last_used."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT * FROM filter_presets WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # Update last_used
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "UPDATE filter_presets SET last_used = ? WHERE name = ?",
            (now, name)
        )
        await self._db.commit()

        # Parse result
        columns = ['preset_id', 'name', 'filters', 'schema_version', 'created_at', 'updated_at', 'last_used']
        result = dict(zip(columns, row))
        result["filters"] = json.loads(result["filters"])
        return result

    async def list_filter_presets(self) -> List[Dict[str, Any]]:
        """List all filter presets."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT preset_id, name, created_at, last_used FROM filter_presets ORDER BY name"
        )
        rows = await cursor.fetchall()
        columns = ['preset_id', 'name', 'created_at', 'last_used']
        return [dict(zip(columns, row)) for row in rows]

    async def delete_filter_preset(self, name: str) -> None:
        """Delete a filter preset by name."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute("DELETE FROM filter_presets WHERE name = ?", (name,))
        await self._db.commit()

    async def update_filter_preset(self, name: str, filters: Dict[str, Any]) -> None:
        """Update filters for an existing preset."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        await self._db.execute("""
            UPDATE filter_presets SET filters = ?, updated_at = ? WHERE name = ?
        """, (json.dumps(filters), now, name))
        await self._db.commit()

    # =========================================================================
    # FTS INDEXING
    # =========================================================================

    def _build_searchable_text(self, raw_data: dict) -> str:
        """Extract searchable text from raw_data JSON."""
        searchable_parts = []

        # Common fields to extract
        for field in ['description', 'summary', 'tagline', 'tags', 'keywords', 'category']:
            if field in raw_data:
                value = raw_data[field]
                if isinstance(value, list):
                    searchable_parts.extend(str(v) for v in value)
                else:
                    searchable_parts.append(str(value))

        return " ".join(searchable_parts)

    async def index_signal_for_search(self, signal_id: int, vertical: str = "unknown") -> None:
        """
        Add or update a signal in the FTS index.

        Args:
            signal_id: The integer ID of the signal to index
            vertical: The vertical category for the signal (e.g., "health", "fintech")
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Fetch signal data
        cursor = await self._db.execute(
            "SELECT company_name, raw_data, source_api FROM signals WHERE id = ?",
            (signal_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return

        company_name, raw_data_json, source_api = row
        raw_data = json.loads(raw_data_json) if raw_data_json else {}
        searchable_text = self._build_searchable_text(raw_data)

        # Upsert into FTS (delete then insert)
        # FTS5 doesn't support UPDATE, so we delete and re-insert
        signal_id_str = str(signal_id)
        await self._db.execute("DELETE FROM signals_fts WHERE signal_id = ?", (signal_id_str,))
        await self._db.execute(
            "INSERT INTO signals_fts (signal_id, company_name, searchable_text, vertical, source_api) VALUES (?, ?, ?, ?, ?)",
            (signal_id_str, company_name, searchable_text, vertical, source_api)
        )
        await self._db.commit()

    async def get_fts_index_stats(self) -> Dict[str, int]:
        """Get FTS index statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Count total signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        # Count indexed signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals_fts")
        indexed_signals = (await cursor.fetchone())[0]

        return {
            "total_signals": total_signals,
            "indexed_signals": indexed_signals,
            "unindexed": total_signals - indexed_signals
        }

    async def rebuild_fts_index(self) -> int:
        """Rebuild entire FTS index from signals table. Returns count indexed."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Clear existing index
        await self._db.execute("DELETE FROM signals_fts")

        # Get all signal IDs
        cursor = await self._db.execute("SELECT id FROM signals")
        signal_ids = [row[0] for row in await cursor.fetchall()]

        await self._db.commit()

        # Index each signal
        count = 0
        for signal_id in signal_ids:
            await self.index_signal_for_search(signal_id)
            count += 1

        return count

    async def search_signals_fts(
        self,
        query: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fuzzy search signals using FTS5.

        Args:
            query: Search query (supports partial matches)
            limit: Maximum results to return

        Returns:
            List of matching signals with relevance rank
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        if not query or not query.strip():
            return []

        query = query.strip()

        # Limit query length
        if len(query) > 500:
            query = query[:500]

        # Escape special FTS5 characters
        # FTS5 special chars: " * - + ( ) : ^
        safe_query = query.replace('"', ' ').replace('*', ' ').replace('-', ' ')
        safe_query = safe_query.replace('+', ' ').replace('(', ' ').replace(')', ' ')
        safe_query = safe_query.replace(':', ' ').replace('^', ' ')
        safe_query = ' '.join(safe_query.split())  # Normalize whitespace

        if not safe_query:
            return []

        # Add * for prefix matching
        fts_query = f'{safe_query}*'

        try:
            cursor = await self._db.execute("""
                SELECT
                    f.signal_id,
                    f.company_name,
                    f.vertical,
                    f.source_api,
                    s.confidence,
                    s.signal_type,
                    s.created_at,
                    bm25(signals_fts) as rank
                FROM signals_fts f
                JOIN signals s ON f.signal_id = CAST(s.id AS TEXT)
                WHERE signals_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit))

            rows = await cursor.fetchall()
            columns = ['signal_id', 'company_name', 'vertical', 'source_api', 'confidence', 'signal_type', 'created_at', 'rank']
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.warning(f"FTS search failed for query '{query[:50]}': {e}")
            return []

    async def get_filtered_signals(
        self,
        verticals: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        signal_types: Optional[List[str]] = None,
        min_confidence: Optional[float] = None,
        max_confidence: Optional[float] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get signals matching filter criteria.

        All filters are ANDed together. Empty/None filters are ignored.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        conditions = []
        params = []

        # Filter by vertical (from FTS table)
        if verticals:
            placeholders = ",".join("?" * len(verticals))
            conditions.append(f"f.vertical IN ({placeholders})")
            params.extend(verticals)

        # Filter by source
        if sources:
            placeholders = ",".join("?" * len(sources))
            conditions.append(f"s.source_api IN ({placeholders})")
            params.extend(sources)

        # Filter by signal type
        if signal_types:
            placeholders = ",".join("?" * len(signal_types))
            conditions.append(f"s.signal_type IN ({placeholders})")
            params.extend(signal_types)

        # Filter by confidence
        if min_confidence is not None:
            conditions.append("s.confidence >= ?")
            params.append(min_confidence)

        if max_confidence is not None:
            conditions.append("s.confidence <= ?")
            params.append(max_confidence)

        # Filter by date range
        if start_date:
            conditions.append("s.created_at >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("s.created_at <= ?")
            params.append(end_date)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await self._db.execute(f"""
            SELECT
                s.id as signal_id,
                s.company_name,
                s.signal_type,
                s.source_api,
                s.confidence,
                s.created_at,
                f.vertical
            FROM signals s
            JOIN signals_fts f ON CAST(s.id AS TEXT) = f.signal_id
            WHERE {where_clause}
            ORDER BY s.created_at DESC
            LIMIT ? OFFSET ?
        """, params)

        rows = await cursor.fetchall()
        columns = ['signal_id', 'company_name', 'signal_type', 'source_api', 'confidence', 'created_at', 'vertical']
        return [dict(zip(columns, row)) for row in rows]

    # =========================================================================
    # SIGNAL OPERATIONS
    # =========================================================================

    async def save_signal(
        self,
        signal_type: str,
        source_api: str,
        canonical_key: str,
        confidence: float,
        raw_data: Dict[str, Any],
        company_name: Optional[str] = None,
        detected_at: Optional[datetime] = None,
    ) -> int:
        """
        Save a new signal to the database.

        Returns the signal ID.
        Raises IntegrityError if duplicate (same canonical_key, signal_type, source_api, detected_at).
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        detected_at = detected_at or datetime.now(timezone.utc)
        created_at = datetime.now(timezone.utc)

        async with self.transaction() as conn:
            # Insert signal
            cursor = await conn.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_type,
                    source_api,
                    canonical_key,
                    company_name,
                    confidence,
                    json.dumps(raw_data),
                    detected_at.isoformat(),
                    created_at.isoformat(),
                )
            )

            signal_id = cursor.lastrowid

            # Create pending processing record
            await conn.execute(
                """
                INSERT INTO signal_processing (
                    signal_id, status, created_at, updated_at
                )
                VALUES (?, 'pending', ?, ?)
                """,
                (signal_id, created_at.isoformat(), created_at.isoformat())
            )

        logger.debug(f"Saved signal {signal_id}: {signal_type} for {canonical_key}")
        return signal_id

    async def get_signal(self, signal_id: int) -> Optional[StoredSignal]:
        """Get a signal by ID."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at,
                p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.id = ?
            """,
            (signal_id,)
        )

        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_signal(row)

    async def get_pending_signals(
        self,
        limit: Optional[int] = None,
        signal_type: Optional[str] = None,
    ) -> List[StoredSignal]:
        """
        Get signals that haven't been processed yet.

        Args:
            limit: Maximum number of signals to return
            signal_type: Filter by signal type (e.g., "github_spike")
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at,
                p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            INNER JOIN signal_processing p ON s.id = p.signal_id
            WHERE p.status = 'pending'
        """

        params: List[Any] = []

        if signal_type:
            query += " AND s.signal_type = ?"
            params.append(signal_type)

        query += " ORDER BY s.detected_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [self._row_to_signal(row) for row in rows]

    async def get_signals_for_company(
        self,
        canonical_key: str,
    ) -> List[StoredSignal]:
        """Get all signals for a company (by canonical key)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at,
                p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.canonical_key = ?
            ORDER BY s.detected_at DESC
            """,
            (canonical_key,)
        )

        rows = await cursor.fetchall()
        return [self._row_to_signal(row) for row in rows]

    async def get_signals_for_company_by_name(self, company_name: str) -> List[Dict[str, Any]]:
        """Get all signals for a specific company by name."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute("""
            SELECT
                s.id as signal_id,
                s.company_name,
                s.signal_type,
                s.source_api,
                s.confidence,
                s.created_at,
                s.raw_data,
                f.vertical
            FROM signals s
            LEFT JOIN signals_fts f ON CAST(s.id AS TEXT) = f.signal_id
            WHERE s.company_name = ?
            ORDER BY s.created_at DESC
        """, (company_name,))

        rows = await cursor.fetchall()
        columns = ['signal_id', 'company_name', 'signal_type', 'source_api', 'confidence', 'created_at', 'raw_data', 'vertical']
        return [dict(zip(columns, row)) for row in rows]

    async def is_duplicate(self, canonical_key: str) -> bool:
        """
        Check if we already have signals for this canonical key.
        Returns True if any signals exist, False otherwise.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM signals WHERE canonical_key = ?",
            (canonical_key,)
        )
        row = await cursor.fetchone()
        return row[0] > 0 if row else False

    # =========================================================================
    # PROCESSING STATE
    # =========================================================================

    async def mark_pushed(
        self,
        signal_id: int,
        notion_page_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as successfully pushed to Notion."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'pushed',
                    notion_page_id = ?,
                    processed_at = ?,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    notion_page_id,
                    now,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as pushed (Notion: {notion_page_id})")

    async def mark_rejected(
        self,
        signal_id: int,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as rejected (won't be pushed)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'rejected',
                    processed_at = ?,
                    error_message = ?,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    now,
                    reason,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as rejected: {reason}")

    async def mark_queued(
        self,
        signal_id: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as queued for Notion write."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'queued',
                    notion_page_id = NULL,
                    processed_at = ?,
                    error_message = NULL,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    now,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as queued for Notion")

    async def get_processing_stats(self) -> Dict[str, int]:
        """Get counts by processing status."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*)
            FROM signal_processing
            GROUP BY status
            """
        )

        rows = await cursor.fetchall()
        return {status: count for status, count in rows}

    async def update_signal_status(
        self,
        canonical_key: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        Update processing status for signals matching canonical key.

        Args:
            canonical_key: The canonical key to match
            status: New status ('pending', 'qualified', 'held', 'rejected', 'pushed')
            error_message: Optional error/reason message

        Returns:
            True if any signals were updated
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE signal_processing
                SET status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE signal_id IN (
                    SELECT id FROM signals WHERE canonical_key = ?
                )
                """,
                (status, error_message, now, canonical_key),
            )
            return cursor.rowcount > 0

    async def get_signals_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
    ) -> List[StoredSignal]:
        """
        Get signals with a specific processing status.

        Args:
            status: Status to filter by
            limit: Maximum number to return

        Returns:
            List of StoredSignal objects
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT s.id, s.signal_type, s.source_api, s.canonical_key,
                   s.company_name, s.confidence, s.raw_data,
                   s.detected_at, s.created_at,
                   p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            JOIN signal_processing p ON s.id = p.signal_id
            WHERE p.status = ?
            ORDER BY s.created_at DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor = await self._db.execute(query, (status,))
        rows = await cursor.fetchall()

        return [self._row_to_signal(row) for row in rows]

    async def get_status_counts(self) -> Dict[str, int]:
        """
        Get counts of signals by processing status.

        Returns:
            Dict mapping status to count, e.g. {"pending": 5, "qualified": 10}
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*) as count
            FROM signal_processing
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_signals_for_analytics(
        self,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Fetch signals with thesis and processing data for analytics dashboard.

        Uses a 4-way LEFT JOIN to combine:
        - signals: Core signal data
        - thesis_classifications: Thesis fit and category
        - signals_fts: Vertical information
        - signal_processing: Processing status

        Args:
            days: Number of days to look back (default 30). Use large value for all time.

        Returns:
            List of dicts with: signal_id, company_name, canonical_key, source_api,
            confidence, detected_at, vertical, category, thesis_fit_score,
            competitor_flag, processing_status
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.id as signal_id,
                s.company_name,
                s.canonical_key,
                s.source_api,
                s.confidence,
                s.detected_at,
                COALESCE(fts.vertical, 'Unknown') as vertical,
                COALESCE(tc.category, 'Unclassified') as category,
                COALESCE(tc.thesis_fit_score, 0) as thesis_fit_score,
                COALESCE(tc.competitor_flag, 0) as competitor_flag,
                COALESCE(sp.status, 'pending') as processing_status
            FROM signals s
            LEFT JOIN thesis_classifications tc ON s.id = tc.signal_id
            LEFT JOIN signals_fts fts ON CAST(s.id AS TEXT) = fts.signal_id
            LEFT JOIN signal_processing sp ON s.id = sp.signal_id
            WHERE s.detected_at >= datetime('now', '-' || ? || ' days')
            ORDER BY s.detected_at DESC
            LIMIT 5000
            """,
            (days,)
        )

        rows = await cursor.fetchall()
        columns = [
            "signal_id", "company_name", "canonical_key", "source_api",
            "confidence", "detected_at", "vertical", "category",
            "thesis_fit_score", "competitor_flag", "processing_status"
        ]

        return [dict(zip(columns, row)) for row in rows]

    # =========================================================================
    # SIGNAL CORRELATION (Deal Intelligence Engine Phase 1)
    # =========================================================================

    async def get_signals_for_correlation(
        self,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Get recent signals for founder correlation matching.

        Used by SignalCorrelator to search for founder identifiers
        in signal raw_data.

        Args:
            limit: Maximum number of signals to return

        Returns:
            List of signal dicts with id, canonical_key, signal_type, raw_data
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, signal_type, raw_data
            FROM signals
            WHERE correlated_founder_id IS NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            raw_data = row[3]
            if isinstance(raw_data, str):
                try:
                    raw_data = json.loads(raw_data)
                except json.JSONDecodeError:
                    raw_data = {}

            results.append({
                "id": row[0],
                "canonical_key": row[1],
                "signal_type": row[2],
                "raw_data": raw_data,
            })

        return results

    async def get_signals_for_founder(
        self,
        founder_id: int,
    ) -> List[Dict[str, Any]]:
        """
        Get all signals linked to a specific founder.

        Used by SignalCorrelator to find serial founder ventures.

        Args:
            founder_id: ID of the founder

        Returns:
            List of signal dicts with id, canonical_key, signal_type
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, signal_type
            FROM signals
            WHERE correlated_founder_id = ?
            ORDER BY detected_at DESC
            """,
            (founder_id,)
        )
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "canonical_key": row[1],
                "signal_type": row[2],
            }
            for row in rows
        ]

    async def update_signal_correlation(
        self,
        signal_id: int,
        founder_id: int,
        confidence: float,
        correlation_type: str,
    ) -> None:
        """
        Update a signal with founder correlation data.

        Args:
            signal_id: ID of the signal
            founder_id: ID of the correlated founder
            confidence: Correlation confidence (0.0-1.0)
            correlation_type: Type of match (email, github, domain, etc.)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute(
            """
            UPDATE signals
            SET correlated_founder_id = ?,
                correlation_confidence = ?,
                correlation_type = ?
            WHERE id = ?
            """,
            (founder_id, confidence, correlation_type, signal_id)
        )
        await self._db.commit()

        logger.debug(
            f"Updated signal {signal_id} with founder {founder_id} "
            f"(confidence={confidence}, type={correlation_type})"
        )

    # =========================================================================
    # TRACTION METHODS (PHASE 2)
    # =========================================================================

    async def get_signals_for_traction(
        self,
        canonical_key: str = None,
        signal_types: List[str] = None,
        days: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Get signals for traction calculation.

        Args:
            canonical_key: Optional canonical key to filter by
            signal_types: List of signal types to include
            days: Number of days to look back (default 60 for comparison)

        Returns:
            List of signal dicts with raw_data parsed
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = """
            SELECT id, signal_type, canonical_key, detected_at, raw_data
            FROM signals
            WHERE detected_at >= ?
        """
        params = [cutoff]

        if canonical_key:
            query += " AND canonical_key = ?"
            params.append(canonical_key)

        if signal_types:
            placeholders = ",".join("?" * len(signal_types))
            query += f" AND signal_type IN ({placeholders})"
            params.extend(signal_types)

        query += " ORDER BY detected_at ASC"

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            raw_data = {}
            if row[4]:
                try:
                    raw_data = json.loads(row[4])
                except json.JSONDecodeError:
                    pass

            # Parse detected_at
            detected_at = datetime.min.replace(tzinfo=timezone.utc)
            if row[3]:
                try:
                    detected_at = datetime.fromisoformat(row[3].replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass

            results.append({
                'id': row[0],
                'signal_type': row[1],
                'canonical_key': row[2],
                'detected_at': detected_at,
                'raw_data': raw_data,
            })

        return results

    async def get_historical_traction_scores(
        self,
        limit: int = 1000,
    ) -> List[float]:
        """
        Get historical composite momentum scores for percentile calculation.

        Args:
            limit: Maximum scores to return

        Returns:
            List of composite_momentum values
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Check if table exists (might not exist yet)
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='traction_scores'"
        )
        if not await cursor.fetchone():
            return []

        cursor = await self._db.execute(
            """
            SELECT composite_momentum
            FROM traction_scores
            ORDER BY calculated_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = await cursor.fetchall()

        return [row[0] for row in rows if row[0] is not None]

    async def save_traction_score(
        self,
        canonical_key: str,
        score: "TractionScore",
    ) -> None:
        """
        Save or update a traction score.

        Args:
            canonical_key: The canonical key for the company
            score: TractionScore dataclass with all metrics
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            INSERT INTO traction_scores (
                canonical_key,
                github_stars_growth_30d,
                github_commit_velocity,
                job_posting_velocity,
                job_count_growth_30d,
                ph_vote_growth_30d,
                hn_mention_growth_30d,
                composite_momentum,
                momentum_percentile,
                calculated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                github_stars_growth_30d = excluded.github_stars_growth_30d,
                github_commit_velocity = excluded.github_commit_velocity,
                job_posting_velocity = excluded.job_posting_velocity,
                job_count_growth_30d = excluded.job_count_growth_30d,
                ph_vote_growth_30d = excluded.ph_vote_growth_30d,
                hn_mention_growth_30d = excluded.hn_mention_growth_30d,
                composite_momentum = excluded.composite_momentum,
                momentum_percentile = excluded.momentum_percentile,
                calculated_at = excluded.calculated_at
            """,
            (
                canonical_key,
                score.github_stars_growth_30d,
                score.github_commit_velocity,
                score.job_posting_velocity,
                score.job_count_growth_30d,
                score.ph_vote_growth_30d,
                score.hn_mention_growth_30d,
                score.composite_momentum,
                score.momentum_percentile,
                now,
            )
        )
        await self._db.commit()

        logger.debug(
            f"Saved traction score for {canonical_key}: "
            f"composite={score.composite_momentum:.2f}, "
            f"percentile={score.momentum_percentile}"
        )

    async def get_traction_score(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get traction score for a company.

        Args:
            canonical_key: Company identifier

        Returns:
            Dict with traction metrics, or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT canonical_key, github_stars_growth_30d, github_commit_velocity,
                   job_posting_velocity, job_count_growth_30d,
                   ph_vote_growth_30d, hn_mention_growth_30d,
                   composite_momentum, momentum_percentile, calculated_at
            FROM traction_scores
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            'canonical_key': row[0],
            'github_stars_growth_30d': row[1],
            'github_commit_velocity': row[2],
            'job_posting_velocity': row[3],
            'job_count_growth_30d': row[4],
            'ph_vote_growth_30d': row[5],
            'hn_mention_growth_30d': row[6],
            'composite_momentum': row[7],
            'momentum_percentile': row[8],
            'calculated_at': row[9],
        }

    # =========================================================================
    # INVESTOR NETWORK METHODS (PHASE 3)
    # =========================================================================

    async def get_signals_for_network(
        self,
        days: int = 365,
    ) -> List[Dict[str, Any]]:
        """
        Get funding signals for investor network building.

        Args:
            days: Number of days to look back (default 365)

        Returns:
            List of funding signals with investor data
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        cursor = await self._db.execute(
            """
            SELECT id, signal_type, canonical_key, detected_at, raw_data
            FROM signals
            WHERE signal_type IN ('crunchbase_funding', 'sec_filing', 'funding_event')
            AND detected_at >= ?
            ORDER BY detected_at DESC
            """,
            (cutoff,)
        )
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            raw_data = {}
            if row[4]:
                try:
                    raw_data = json.loads(row[4])
                except json.JSONDecodeError:
                    pass

            # Only include signals with investor data
            if raw_data.get('investors'):
                results.append({
                    'id': row[0],
                    'signal_type': row[1],
                    'canonical_key': row[2],
                    'detected_at': row[3],
                    'raw_data': raw_data,
                })

        return results

    async def save_investor_rankings(
        self,
        rankings: List["InvestorRanking"],
    ) -> None:
        """
        Save investor centrality rankings.

        Args:
            rankings: List of InvestorRanking dataclasses
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        for ranking in rankings:
            await self._db.execute(
                """
                INSERT INTO investor_rankings (
                    investor_name, centrality_score, coinvestment_count,
                    rank, calculated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(investor_name) DO UPDATE SET
                    centrality_score = excluded.centrality_score,
                    coinvestment_count = excluded.coinvestment_count,
                    rank = excluded.rank,
                    calculated_at = excluded.calculated_at
                """,
                (
                    ranking.investor_name,
                    ranking.centrality_score,
                    ranking.coinvestment_count,
                    ranking.rank,
                    now,
                )
            )

        await self._db.commit()
        logger.debug(f"Saved {len(rankings)} investor rankings")

    async def save_company_investor_score(
        self,
        score: "CompanyInvestorScore",
    ) -> None:
        """
        Save company investor quality score.

        Args:
            score: CompanyInvestorScore dataclass
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            INSERT INTO company_investor_scores (
                canonical_key, investor_quality_score, top_investor,
                known_investors_count, total_investors_count, calculated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                investor_quality_score = excluded.investor_quality_score,
                top_investor = excluded.top_investor,
                known_investors_count = excluded.known_investors_count,
                total_investors_count = excluded.total_investors_count,
                calculated_at = excluded.calculated_at
            """,
            (
                score.canonical_key,
                score.investor_quality_score,
                score.top_investor,
                score.known_investors_count,
                score.total_investors_count,
                now,
            )
        )
        await self._db.commit()

        logger.debug(
            f"Saved investor score for {score.canonical_key}: "
            f"quality={score.investor_quality_score:.2f}"
        )

    async def get_company_investor_score(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get company investor quality score.

        Args:
            canonical_key: Company identifier

        Returns:
            Dict with investor metrics, or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT canonical_key, investor_quality_score, top_investor,
                   known_investors_count, total_investors_count, calculated_at
            FROM company_investor_scores
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            'canonical_key': row[0],
            'investor_quality_score': row[1],
            'top_investor': row[2],
            'known_investors_count': row[3],
            'total_investors_count': row[4],
            'calculated_at': row[5],
        }

    # =========================================================================
    # FOUNDER INTENT METHODS (PHASE 4)
    # =========================================================================

    async def save_founder_intent_signal(
        self,
        intent: "IntentSignal",
    ) -> None:
        """
        Save a detected founder intent signal.

        Args:
            intent: IntentSignal dataclass
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        detected_at = intent.detected_at
        if isinstance(detected_at, datetime):
            detected_at = detected_at.isoformat()

        await self._db.execute(
            """
            INSERT INTO founder_intent_signals (
                signal_id, canonical_key, intent_type, founder_id,
                founder_name, confidence, evidence, detected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.signal_id,
                intent.canonical_key,
                intent.intent_type,
                intent.founder_id,
                intent.founder_name,
                intent.confidence,
                intent.evidence,
                detected_at,
            )
        )
        await self._db.commit()

        logger.debug(
            f"Saved intent signal: {intent.intent_type} for founder {intent.founder_id} "
            f"(confidence={intent.confidence:.2f})"
        )

    async def get_founder_intent_signals(
        self,
        founder_id: int = None,
        intent_type: str = None,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get founder intent signals.

        Args:
            founder_id: Optional founder ID to filter by
            intent_type: Optional intent type to filter by
            days: Number of days to look back

        Returns:
            List of intent signal dicts
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = """
            SELECT id, signal_id, canonical_key, intent_type, founder_id,
                   founder_name, confidence, evidence, detected_at
            FROM founder_intent_signals
            WHERE detected_at >= ?
        """
        params = [cutoff]

        if founder_id:
            query += " AND founder_id = ?"
            params.append(founder_id)

        if intent_type:
            query += " AND intent_type = ?"
            params.append(intent_type)

        query += " ORDER BY detected_at DESC"

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                'id': row[0],
                'signal_id': row[1],
                'canonical_key': row[2],
                'intent_type': row[3],
                'founder_id': row[4],
                'founder_name': row[5],
                'confidence': row[6],
                'evidence': row[7],
                'detected_at': row[8],
            }
            for row in rows
        ]

    # =========================================================================
    # DEAL QUALITY METHODS (PHASE 5)
    # =========================================================================

    async def save_deal_quality_score(
        self,
        score: "DealQualityScore",
    ) -> None:
        """
        Save or update a deal quality score.

        Args:
            score: DealQualityScore dataclass
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        calculated_at = score.calculated_at
        if isinstance(calculated_at, datetime):
            calculated_at = calculated_at.isoformat()

        await self._db.execute(
            """
            INSERT OR REPLACE INTO deal_quality_scores (
                canonical_key, thesis_fit, traction, investor_quality,
                founder, raw_score, percentile, routing_recommendation,
                calculated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score.canonical_key,
                score.thesis_fit,
                score.traction,
                score.investor_quality,
                score.founder,
                score.raw_score,
                score.percentile,
                score.routing_recommendation.value if hasattr(score.routing_recommendation, 'value') else str(score.routing_recommendation),
                calculated_at,
            ),
        )
        await self._db.commit()

        logger.debug(
            f"Saved deal quality score: {score.canonical_key} "
            f"raw={score.raw_score:.2f} percentile={score.percentile:.2f} "
            f"routing={score.routing_recommendation}"
        )

    async def get_deal_quality_score(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get deal quality score for a company.

        Args:
            canonical_key: Company identifier

        Returns:
            Dict with score components, or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT canonical_key, thesis_fit, traction, investor_quality,
                   founder, raw_score, percentile, routing_recommendation,
                   calculated_at
            FROM deal_quality_scores
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            'canonical_key': row[0],
            'thesis_fit': row[1],
            'traction': row[2],
            'investor_quality': row[3],
            'founder': row[4],
            'raw_score': row[5],
            'percentile': row[6],
            'routing_recommendation': row[7],
            'calculated_at': row[8],
        }

    async def get_historical_deal_quality_scores(
        self,
        days: int = 90,
    ) -> List[Dict[str, Any]]:
        """
        Get historical deal quality scores for percentile calculation.

        Args:
            days: Number of days to look back (default 90)

        Returns:
            List of score dicts with raw_score
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        cursor = await self._db.execute(
            """
            SELECT raw_score
            FROM deal_quality_scores
            WHERE calculated_at >= ?
            ORDER BY raw_score ASC
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()

        return [{'raw_score': row[0]} for row in rows]

    # =========================================================================
    # SIGNAL EMBEDDING METHODS (PHASE 6)
    # =========================================================================

    async def save_signal_embedding(
        self,
        signal_id: int,
        canonical_key: str,
        embedding: List[float],
        text: str,
        company_name: Optional[str] = None,
    ) -> None:
        """
        Save or update signal embedding.

        Args:
            signal_id: Signal database ID
            canonical_key: Company identifier
            embedding: 768-dimensional vector
            text: Searchable text that was embedded
            company_name: Optional company name
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        # Store embedding as JSON array
        embedding_json = json.dumps(embedding)

        await self._db.execute(
            """
            INSERT OR REPLACE INTO signal_embeddings (
                signal_id, canonical_key, embedding, text, company_name, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                canonical_key,
                embedding_json,
                text,
                company_name,
                now,
            ),
        )
        await self._db.commit()

        logger.debug(f"Saved embedding for signal {signal_id} ({canonical_key})")

    async def get_all_signal_embeddings(self) -> List[Dict[str, Any]]:
        """
        Get all signal embeddings for similarity search.

        Returns:
            List of dicts with signal_id, canonical_key, embedding, text, company_name
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT signal_id, canonical_key, embedding, text, company_name
            FROM signal_embeddings
            """
        )
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            try:
                embedding = json.loads(row[2]) if row[2] else []
                results.append({
                    'signal_id': row[0],
                    'canonical_key': row[1],
                    'embedding': embedding,
                    'text': row[3],
                    'company_name': row[4],
                })
            except json.JSONDecodeError:
                logger.warning(f"Invalid embedding JSON for signal {row[0]}")

        return results

    async def get_signals_without_embeddings(
        self,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get signals that don't have embeddings yet.

        Args:
            limit: Maximum number of signals to return

        Returns:
            List of signal dicts
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT s.id, s.canonical_key, s.company_name, s.raw_data
            FROM signals s
            LEFT JOIN signal_embeddings e ON s.id = e.signal_id
            WHERE e.id IS NULL
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            try:
                raw_data = json.loads(row[3]) if row[3] else {}
            except json.JSONDecodeError:
                raw_data = {}

            results.append({
                'id': row[0],
                'canonical_key': row[1],
                'company_name': row[2],
                'raw_data': raw_data,
            })

        return results

    # =========================================================================
    # NOTION OUTBOX
    # =========================================================================

    async def enqueue_notion_write(
        self,
        idempotency_key: str,
        payload: Dict[str, Any],
    ) -> int:
        """
        Queue a Notion write in the outbox table.
        Returns the outbox ID.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO notion_outbox (
                    idempotency_key, payload_json, status,
                    attempts, created_at, updated_at
                )
                VALUES (?, ?, 'pending', 0, ?, ?)
                """,
                (
                    idempotency_key,
                    json.dumps(payload),
                    now,
                    now,
                )
            )

            cursor = await conn.execute(
                "SELECT id FROM notion_outbox WHERE idempotency_key = ?",
                (idempotency_key,)
            )
            row = await cursor.fetchone()
            outbox_id = row[0]

        logger.info(f"Enqueued Notion write: {outbox_id} ({idempotency_key})")
        return outbox_id

    async def get_pending_outbox(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get pending outbox entries (status='pending')."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, idempotency_key, payload_json, status, attempts,
                   next_attempt_at, last_error, created_at, updated_at
            FROM notion_outbox
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,)
        )

        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "idempotency_key": row[1],
                "payload": json.loads(row[2]),
                "status": row[3],
                "attempts": row[4],
                "next_attempt_at": row[5],
                "last_error": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }
            for row in rows
        ]

    async def mark_outbox_sent(
        self,
        outbox_id: int,
    ) -> None:
        """Mark an outbox entry as sent."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE notion_outbox
                SET status = 'sent',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, outbox_id)
            )

        logger.info(f"Marked outbox {outbox_id} as sent")

    async def mark_outbox_failed(
        self,
        outbox_id: int,
        error: str,
        next_attempt_at: Optional[str] = None,
    ) -> None:
        """Mark an outbox entry as failed with error details."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE notion_outbox
                SET status = 'failed',
                    last_error = ?,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error, next_attempt_at, now, outbox_id)
            )

        logger.info(f"Marked outbox {outbox_id} as failed: {error}")

    # =========================================================================
    # SUPPRESSION CACHE
    # =========================================================================

    async def update_suppression_cache(
        self,
        entries: List[SuppressionEntry],
    ) -> int:
        """
        Bulk update suppression cache from Notion sync.
        Returns number of entries updated.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        count = 0

        async with self.transaction() as conn:
            for entry in entries:
                await conn.execute(
                    """
                    INSERT INTO suppression_cache (
                        canonical_key, notion_page_id, status, company_name,
                        cached_at, expires_at, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_key) DO UPDATE SET
                        notion_page_id = excluded.notion_page_id,
                        status = excluded.status,
                        company_name = excluded.company_name,
                        cached_at = excluded.cached_at,
                        expires_at = excluded.expires_at,
                        metadata = excluded.metadata
                    """,
                    (
                        entry.canonical_key,
                        entry.notion_page_id,
                        entry.status,
                        entry.company_name,
                        entry.cached_at.isoformat(),
                        entry.expires_at.isoformat(),
                        json.dumps(entry.metadata) if entry.metadata else None,
                    )
                )
                count += 1

        logger.info(f"Updated {count} suppression cache entries")
        return count

    async def check_suppression(
        self,
        canonical_key: str,
    ) -> Optional[SuppressionEntry]:
        """
        Check if a canonical key is in the suppression cache.
        Returns None if not found or expired.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            SELECT
                canonical_key, notion_page_id, status, company_name,
                cached_at, expires_at, metadata
            FROM suppression_cache
            WHERE canonical_key = ? AND expires_at > ?
            """,
            (canonical_key, now)
        )

        row = await cursor.fetchone()
        if not row:
            return None

        return SuppressionEntry(
            canonical_key=row[0],
            notion_page_id=row[1],
            status=row[2],
            company_name=row[3],
            cached_at=datetime.fromisoformat(row[4]),
            expires_at=datetime.fromisoformat(row[5]),
            metadata=json.loads(row[6]) if row[6] else None,
        )

    async def clean_expired_cache(self) -> int:
        """
        Remove expired entries from suppression cache.
        Returns number of entries removed.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM suppression_cache WHERE expires_at <= ?",
                (now,)
            )
            count = cursor.rowcount

        if count > 0:
            logger.info(f"Cleaned {count} expired suppression cache entries")

        return count

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _row_to_signal(self, row: tuple) -> StoredSignal:
        """Convert database row to StoredSignal object."""
        # Helper to ensure timezone-aware datetimes
        def parse_datetime(dt_str: str) -> datetime:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return StoredSignal(
            id=row[0],
            signal_type=row[1],
            source_api=row[2],
            canonical_key=row[3],
            company_name=row[4],
            confidence=row[5],
            raw_data=json.loads(row[6]),
            detected_at=parse_datetime(row[7]),
            created_at=parse_datetime(row[8]),
            processing_status=row[9] if len(row) > 9 else None,
            notion_page_id=row[10] if len(row) > 10 else None,
            processed_at=parse_datetime(row[11]) if len(row) > 11 and row[11] else None,
            error_message=row[12] if len(row) > 12 else None,
        )

    async def get_stats(self) -> Dict[str, Any]:
        """Get overall database statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Signal counts by type
        cursor = await self._db.execute(
            """
            SELECT signal_type, COUNT(*)
            FROM signals
            GROUP BY signal_type
            """
        )
        signal_counts = dict(await cursor.fetchall())

        # Processing stats
        processing_stats = await self.get_processing_stats()

        # Suppression cache stats
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM suppression_cache WHERE expires_at > ?",
            (datetime.now(timezone.utc).isoformat(),)
        )
        active_cache_entries = (await cursor.fetchone())[0]

        # Total signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        return {
            "total_signals": total_signals,
            "signals_by_type": signal_counts,
            "processing_status": processing_stats,
            "active_suppression_entries": active_cache_entries,
            "database_path": str(self.db_path),
        }

    # =========================================================================
    # PIPELINE METRICS
    # =========================================================================

    async def save_pipeline_run(self, stats: PipelineStats) -> str:
        """
        Save pipeline run metrics to database.

        Args:
            stats: PipelineStats object from a pipeline run

        Returns:
            run_id: UUID string for this pipeline run
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Serialize errors list to JSON
        errors_json = json.dumps(stats.errors) if stats.errors else None

        # Serialize health_report to JSON if present
        health_json = None
        if stats.health_report:
            health_json = json.dumps(stats.health_report.to_dict())

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, started_at, completed_at, duration_seconds,
                    collectors_run, collectors_succeeded, collectors_failed, signals_collected,
                    signals_stored, signals_deduplicated,
                    signals_processed, signals_auto_push, signals_needs_review,
                    signals_held, signals_rejected,
                    prospects_created, prospects_updated, prospects_skipped,
                    errors, health_report, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stats.started_at.isoformat(),
                    stats.completed_at.isoformat() if stats.completed_at else None,
                    stats.duration_seconds,
                    stats.collectors_run,
                    stats.collectors_succeeded,
                    stats.collectors_failed,
                    stats.signals_collected,
                    stats.signals_stored,
                    stats.signals_deduplicated,
                    stats.signals_processed,
                    stats.signals_auto_push,
                    stats.signals_needs_review,
                    stats.signals_held,
                    stats.signals_rejected,
                    stats.prospects_created,
                    stats.prospects_updated,
                    stats.prospects_skipped,
                    errors_json,
                    health_json,
                    now,
                )
            )

        logger.info(f"Saved pipeline run {run_id} (duration: {stats.duration_seconds}s)")
        return run_id

    async def get_pipeline_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent pipeline runs in reverse chronological order.

        Args:
            limit: Maximum number of runs to return (default 10)

        Returns:
            List of pipeline run dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                run_id, started_at, completed_at, duration_seconds,
                collectors_run, collectors_succeeded, collectors_failed, signals_collected,
                signals_stored, signals_deduplicated,
                signals_processed, signals_auto_push, signals_needs_review,
                signals_held, signals_rejected,
                prospects_created, prospects_updated, prospects_skipped,
                errors, health_report
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        )

        rows = await cursor.fetchall()
        return [self._row_to_pipeline_run(row) for row in rows]

    async def get_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific pipeline run by ID.

        Args:
            run_id: UUID of the pipeline run

        Returns:
            Pipeline run dictionary or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                run_id, started_at, completed_at, duration_seconds,
                collectors_run, collectors_succeeded, collectors_failed, signals_collected,
                signals_stored, signals_deduplicated,
                signals_processed, signals_auto_push, signals_needs_review,
                signals_held, signals_rejected,
                prospects_created, prospects_updated, prospects_skipped,
                errors, health_report
            FROM pipeline_runs
            WHERE run_id = ?
            """,
            (run_id,)
        )

        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_pipeline_run(row)

    def _row_to_pipeline_run(self, row: tuple) -> Dict[str, Any]:
        """Convert database row to pipeline run dictionary."""
        return {
            "run_id": row[0],
            "started_at": row[1],
            "completed_at": row[2],
            "duration_seconds": row[3],
            "collectors_run": row[4],
            "collectors_succeeded": row[5],
            "collectors_failed": row[6],
            "signals_collected": row[7],
            "signals_stored": row[8],
            "signals_deduplicated": row[9],
            "signals_processed": row[10],
            "signals_auto_push": row[11],
            "signals_needs_review": row[12],
            "signals_held": row[13],
            "signals_rejected": row[14],
            "prospects_created": row[15],
            "prospects_updated": row[16],
            "prospects_skipped": row[17],
            "errors": json.loads(row[18]) if row[18] else [],
            "health_report": json.loads(row[19]) if row[19] else None,
        }

    async def save_collector_metrics(self, run_id: str, metrics: "CollectorMetrics") -> None:
        """
        Save collector metrics for a pipeline run.

        Args:
            run_id: Pipeline run ID to associate with
            metrics: CollectorMetrics object with timing and API stats
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        error_messages_json = json.dumps(metrics.error_messages) if metrics.error_messages else None

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO collector_metrics (
                    run_id, collector_name, started_at, completed_at, duration_seconds,
                    signals_found, status, api_calls, rate_limit_hits, retries,
                    errors, error_messages, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metrics.collector_name,
                    metrics.started_at.isoformat(),
                    metrics.completed_at.isoformat() if metrics.completed_at else None,
                    metrics.duration_seconds,
                    metrics.signals_found,
                    metrics.status,
                    metrics.api_calls,
                    metrics.rate_limit_hits,
                    metrics.retries,
                    metrics.errors,
                    error_messages_json,
                    now,
                )
            )

        logger.debug(f"Saved metrics for collector {metrics.collector_name} (run: {run_id})")

    async def get_collector_metrics(
        self,
        run_id: Optional[str] = None,
        collector_name: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query collector metrics with optional filters.

        Args:
            run_id: Filter to specific pipeline run
            collector_name: Filter to specific collector
            limit: Maximum results (default 100)

        Returns:
            List of collector metrics dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                run_id, collector_name, started_at, completed_at, duration_seconds,
                signals_found, status, api_calls, rate_limit_hits, retries,
                errors, error_messages
            FROM collector_metrics
            WHERE 1=1
        """
        params = []

        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if collector_name:
            query += " AND collector_name = ?"
            params.append(collector_name)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "run_id": row[0],
                "collector_name": row[1],
                "started_at": row[2],
                "completed_at": row[3],
                "duration_seconds": row[4],
                "signals_found": row[5],
                "status": row[6],
                "api_calls": row[7],
                "rate_limit_hits": row[8],
                "retries": row[9],
                "errors": row[10],
                "error_messages": json.loads(row[11]) if row[11] else [],
            }
            for row in rows
        ]

    # =========================================================================
    # THESIS CLASSIFICATION STORAGE
    # =========================================================================

    async def save_thesis_classification(
        self,
        signal_id: int,
        canonical_key: str,
        keyword_score: Optional[float] = None,
        keyword_category: Optional[str] = None,
        negative_keywords: Optional[List[str]] = None,
        thesis_match: Optional[bool] = None,
        thesis_fit_score: Optional[float] = None,
        category: Optional[str] = None,
        stage_estimate: Optional[str] = None,
        confidence: Optional[str] = None,
        rationale: Optional[str] = None,
        key_signals: Optional[List[str]] = None,
        prompt_version: Optional[str] = None,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        competitor_flag: bool = False,
        competitor_match: Optional[Dict] = None,
    ) -> int:
        """
        Save a thesis classification result.

        Args:
            signal_id: ID of the signal being classified
            canonical_key: Canonical key for the company
            keyword_score: Score from keyword matcher (stage 1)
            keyword_category: Category from keyword matcher
            negative_keywords: List of negative keywords found
            thesis_match: Whether LLM determined thesis match
            thesis_fit_score: Fit score from LLM (0-1)
            category: Thesis category from LLM
            stage_estimate: Estimated funding stage
            confidence: LLM confidence level
            rationale: LLM's explanation for the classification
            key_signals: Key signals identified by LLM
            prompt_version: Version of the prompt used
            model: LLM model name
            input_tokens: Input token count
            output_tokens: Output token count
            latency_ms: API call latency in milliseconds
            competitor_flag: Whether a competitor was detected
            competitor_match: Details of matched portfolio company

        Returns:
            The inserted row ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO thesis_classifications (
                    signal_id, canonical_key,
                    keyword_score, keyword_category, negative_keywords,
                    thesis_match, thesis_fit_score, category,
                    stage_estimate, confidence, rationale, key_signals,
                    prompt_version, model, input_tokens, output_tokens, latency_ms,
                    competitor_flag, competitor_match,
                    classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    canonical_key,
                    keyword_score,
                    keyword_category,
                    json.dumps(negative_keywords) if negative_keywords else None,
                    thesis_match,
                    thesis_fit_score,
                    category,
                    stage_estimate,
                    confidence,
                    rationale,
                    json.dumps(key_signals) if key_signals else None,
                    prompt_version,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    competitor_flag,
                    json.dumps(competitor_match) if competitor_match else None,
                    now,
                ),
            )
            return cursor.lastrowid

    async def get_thesis_classification(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent thesis classification for a canonical key.

        Args:
            canonical_key: The canonical key to look up

        Returns:
            Dictionary with classification details or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT signal_id, canonical_key,
                   keyword_score, keyword_category, negative_keywords,
                   thesis_match, thesis_fit_score, category,
                   stage_estimate, confidence, rationale, key_signals,
                   prompt_version, model, input_tokens, output_tokens, latency_ms,
                   competitor_flag, competitor_match,
                   classified_at
            FROM thesis_classifications
            WHERE canonical_key = ?
            ORDER BY classified_at DESC
            LIMIT 1
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            "signal_id": row[0],
            "canonical_key": row[1],
            "keyword_score": row[2],
            "keyword_category": row[3],
            "negative_keywords": json.loads(row[4]) if row[4] else [],
            "thesis_match": bool(row[5]) if row[5] is not None else None,
            "thesis_fit_score": row[6],
            "category": row[7],
            "stage_estimate": row[8],
            "confidence": row[9],
            "rationale": row[10],
            "key_signals": json.loads(row[11]) if row[11] else [],
            "prompt_version": row[12],
            "model": row[13],
            "input_tokens": row[14],
            "output_tokens": row[15],
            "latency_ms": row[16],
            "competitor_flag": bool(row[17]),
            "competitor_match": json.loads(row[18]) if row[18] else None,
            "classified_at": row[19],
        }

    async def get_recent_classification(
        self,
        canonical_key: str,
        days: int = 7,
    ) -> Optional[Dict[str, Any]]:
        """
        Get classification if within cache window.

        Args:
            canonical_key: The canonical key to look up
            days: Number of days to consider as "recent" (default 7)

        Returns:
            Dictionary with classification details or None if not found or expired
        """
        result = await self.get_thesis_classification(canonical_key)
        if not result:
            return None

        classified_at_str = result["classified_at"]
        # Handle both formats: with and without timezone
        if classified_at_str.endswith("Z"):
            classified_at_str = classified_at_str[:-1] + "+00:00"
        elif "+" not in classified_at_str and not classified_at_str.endswith("Z"):
            # Assume UTC if no timezone info
            classified_at_str = classified_at_str + "+00:00"

        classified_at = datetime.fromisoformat(classified_at_str)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        if classified_at >= cutoff:
            return result
        return None


# =============================================================================
# CONTEXT MANAGER FOR EASY USAGE
# =============================================================================

@asynccontextmanager
async def signal_store(
    db_path: str | Path = "signals.db",
    **kwargs
) -> AsyncIterator[SignalStore]:
    """
    Context manager for SignalStore that handles initialization and cleanup.

    Usage:
        async with signal_store("signals.db") as store:
            await store.save_signal(...)
    """
    store = SignalStore(db_path, **kwargs)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

async def example_usage():
    """Demonstrate signal store usage."""

    # Use context manager for automatic cleanup
    async with signal_store("example_signals.db") as store:
        # Save a signal
        signal_id = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            confidence=0.85,
            raw_data={
                "repo": "acme/awesome-ml",
                "stars": 1500,
                "recent_stars": 200,
                "topics": ["ai", "machine-learning"],
            }
        )
        print(f"Saved signal: {signal_id}")

        # Check for duplicates
        is_dup = await store.is_duplicate("domain:acme.ai")
        print(f"Is duplicate: {is_dup}")

        # Get pending signals
        pending = await store.get_pending_signals(limit=10)
        print(f"Pending signals: {len(pending)}")

        # Get signals for a company
        company_signals = await store.get_signals_for_company("domain:acme.ai")
        print(f"Signals for Acme: {len(company_signals)}")
        for sig in company_signals:
            print(f"  - {sig.signal_type} ({sig.confidence:.2f}) from {sig.source_api}")

        # Mark as pushed
        await store.mark_pushed(
            signal_id,
            notion_page_id="notion-abc-123",
            metadata={"status": "Source", "confidence": 0.85}
        )

        # Update suppression cache
        entries = [
            SuppressionEntry(
                canonical_key="domain:acme.ai",
                notion_page_id="notion-abc-123",
                status="Source",
                company_name="Acme Inc",
            )
        ]
        await store.update_suppression_cache(entries)

        # Check suppression
        suppressed = await store.check_suppression("domain:acme.ai")
        if suppressed:
            print(f"Suppressed: {suppressed.company_name} (Notion: {suppressed.notion_page_id})")

        # Get stats
        stats = await store.get_stats()
        print("\nDatabase stats:")
        print(f"  Total signals: {stats['total_signals']}")
        print(f"  By type: {stats['signals_by_type']}")
        print(f"  Processing status: {stats['processing_status']}")
        print(f"  Active cache entries: {stats['active_suppression_entries']}")


if __name__ == "__main__":
    # Run example
    import asyncio
    asyncio.run(example_usage())
