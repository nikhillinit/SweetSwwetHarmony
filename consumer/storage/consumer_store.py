"""
Consumer Signal Storage Layer

Provides persistent SQLite storage for consumer discovery signals with:
- Content hash deduplication (SHA256 based)
- LLM classification audit trail
- User action tracking (synced from Notion)
- Collector run health monitoring
- Cost tracking for API usage

Tables:
  - signals: Raw signals from collectors (links only)
  - companies: Enriched company records
  - user_actions: Notion feedback sync
  - llm_classifications: LLM decision audit trail
  - collector_runs: Health monitoring

Usage:
    async with consumer_store("consumer_signals.db") as store:
        signal_id = await store.save_signal({
            "source_api": "hn",
            "source_id": "12345678",
            "title": "Show HN: My Consumer App",
            "url": "https://example.com"
        })
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncIterator, Tuple

import aiosqlite

from .deduplication import compute_content_hash, normalize_source_id

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA VERSION
# =============================================================================

CURRENT_SCHEMA_VERSION = 1

MIGRATIONS = {
    1: """
    -- =====================================================
    -- SIGNALS TABLE
    -- Stores raw signals from all collectors (links only)
    -- =====================================================
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Source identification
        source_api TEXT NOT NULL,
        source_id TEXT NOT NULL,
        signal_type TEXT NOT NULL,

        -- Idempotency: SHA256(source_api|source_id)[:32]
        content_hash TEXT NOT NULL,

        -- Core content (links only - no full body)
        title TEXT,
        url TEXT,
        source_context TEXT,
        raw_metadata TEXT,

        -- Entity reference
        company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
        extracted_company_name TEXT,

        -- Filter result
        filter_result TEXT,
        filter_stage TEXT,

        -- Review status
        status TEXT DEFAULT 'pending',
        notion_page_id TEXT,

        -- Timestamps
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_idempotent
        ON signals(source_api, source_id);
    CREATE INDEX IF NOT EXISTS idx_signals_hash ON signals(content_hash);
    CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
    CREATE INDEX IF NOT EXISTS idx_signals_filter ON signals(filter_result);
    CREATE INDEX IF NOT EXISTS idx_signals_company ON signals(company_id);
    CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

    -- =====================================================
    -- COMPANIES TABLE
    -- Canonical company records
    -- =====================================================
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        canonical_key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        domain TEXT,
        description TEXT,

        category TEXT,
        stage TEXT,
        headquarters_location TEXT,
        founding_year INTEGER,

        overall_status TEXT DEFAULT 'candidate',
        notion_page_id TEXT,

        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(overall_status);
    CREATE INDEX IF NOT EXISTS idx_companies_category ON companies(category);

    -- =====================================================
    -- LLM_CLASSIFICATIONS TABLE
    -- Audit trail for LLM decisions
    -- =====================================================
    CREATE TABLE IF NOT EXISTS llm_classifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,

        -- Model info
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,

        -- Classification result
        thesis_match INTEGER NOT NULL,
        confidence REAL NOT NULL,
        categories TEXT,
        reasoning TEXT,

        -- Usage tracking
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms INTEGER,

        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_llm_signal ON llm_classifications(signal_id);
    CREATE INDEX IF NOT EXISTS idx_llm_model ON llm_classifications(model, prompt_version);
    CREATE INDEX IF NOT EXISTS idx_llm_match ON llm_classifications(thesis_match);

    -- =====================================================
    -- USER_ACTIONS TABLE
    -- Tracks human decisions (synced from Notion)
    -- =====================================================
    CREATE TABLE IF NOT EXISTS user_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        signal_id INTEGER REFERENCES signals(id) ON DELETE CASCADE,
        company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        notion_page_id TEXT,

        action TEXT NOT NULL,
        rejection_reason TEXT,
        rejection_notes TEXT,
        thesis_score_at_action REAL,

        synced_from_notion_at TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_user_actions_signal ON user_actions(signal_id);
    CREATE INDEX IF NOT EXISTS idx_user_actions_action ON user_actions(action, created_at);

    -- =====================================================
    -- COLLECTOR_RUNS TABLE
    -- Health monitoring
    -- =====================================================
    CREATE TABLE IF NOT EXISTS collector_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        collector_name TEXT NOT NULL,
        status TEXT NOT NULL,

        signals_found INTEGER DEFAULT 0,
        signals_new INTEGER DEFAULT 0,

        started_at TEXT NOT NULL,
        completed_at TEXT,
        duration_seconds REAL,

        error_message TEXT,
        api_calls_made INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_collector_runs_name
        ON collector_runs(collector_name, started_at);

    -- =====================================================
    -- COST_TRACKING TABLE
    -- API usage monitoring
    -- =====================================================
    CREATE TABLE IF NOT EXISTS cost_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        service TEXT NOT NULL,
        operation TEXT NOT NULL,

        units_consumed INTEGER DEFAULT 1,
        estimated_cost_usd REAL,

        triggered_by TEXT,
        related_signal_id INTEGER,

        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_cost_tracking_service
        ON cost_tracking(service, created_at);

    -- =====================================================
    -- SCHEMA MIGRATIONS TABLE
    -- =====================================================
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT
    );
    """
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StoredSignal:
    """A signal loaded from the database."""
    id: int
    source_api: str
    source_id: str
    signal_type: str
    content_hash: str
    title: Optional[str]
    url: Optional[str]
    source_context: Optional[str]
    raw_metadata: Optional[Dict[str, Any]]
    status: str
    filter_result: Optional[str]
    filter_stage: Optional[str]
    extracted_company_name: Optional[str]
    notion_page_id: Optional[str]
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    company_id: Optional[int] = None


@dataclass
class LLMClassification:
    """LLM classification result."""
    id: int
    signal_id: int
    model: str
    prompt_version: str
    thesis_match: bool
    confidence: float
    categories: List[str]
    reasoning: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    latency_ms: Optional[int]
    created_at: datetime


@dataclass
class CollectorRun:
    """Collector execution record."""
    id: int
    collector_name: str
    status: str
    signals_found: int
    signals_new: int
    started_at: datetime
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    error_message: Optional[str]
    api_calls_made: int


# =============================================================================
# CONSUMER STORE
# =============================================================================

class ConsumerStore:
    """
    Async SQLite storage for Consumer Discovery Engine.

    Features:
    - Content hash deduplication
    - LLM classification audit trail
    - User action sync from Notion
    - Collector health monitoring
    - Cost tracking
    """

    def __init__(self, db_path: str | Path = "consumer_signals.db"):
        self.db_path = Path(db_path)
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize database and apply migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._apply_migrations()
        logger.info(f"ConsumerStore initialized: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for transactions."""
        if not self._db:
            raise RuntimeError("Database not initialized")

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

        try:
            cursor = await self._db.execute(
                "SELECT MAX(version) FROM schema_migrations"
            )
            row = await cursor.fetchone()
            current_version = row[0] if row and row[0] else 0
        except aiosqlite.OperationalError:
            current_version = 0

        for version in sorted(MIGRATIONS.keys()):
            if version <= current_version:
                continue

            logger.info(f"Applying migration v{version}...")

            async with self.transaction() as conn:
                await conn.executescript(MIGRATIONS[version])
                await conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                    (version, datetime.now(timezone.utc).isoformat(), f"Schema version {version}")
                )

            logger.info(f"Migration v{version} applied")

    # =========================================================================
    # SIGNAL OPERATIONS
    # =========================================================================

    async def save_signal(
        self,
        source_api: str,
        source_id: str,
        signal_type: str = "mention",
        title: Optional[str] = None,
        url: Optional[str] = None,
        source_context: Optional[str] = None,
        raw_metadata: Optional[Dict[str, Any]] = None,
        extracted_company_name: Optional[str] = None,
    ) -> Tuple[int, bool]:
        """
        Save a signal, handling deduplication via upsert.

        Returns:
            (signal_id, is_new) - is_new is True if newly created
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Normalize and hash
        norm_source_id = normalize_source_id(source_api, source_id)
        content_hash = compute_content_hash(source_api, norm_source_id)
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            # Check if exists
            cursor = await conn.execute(
                "SELECT id FROM signals WHERE source_api = ? AND source_id = ?",
                (source_api, norm_source_id)
            )
            existing = await cursor.fetchone()

            if existing:
                # Update last_seen_at
                await conn.execute(
                    "UPDATE signals SET last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, existing[0])
                )
                return existing[0], False

            # Insert new
            cursor = await conn.execute(
                """
                INSERT INTO signals (
                    source_api, source_id, signal_type, content_hash,
                    title, url, source_context, raw_metadata,
                    extracted_company_name, status,
                    first_seen_at, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    source_api, norm_source_id, signal_type, content_hash,
                    title, url, source_context,
                    json.dumps(raw_metadata) if raw_metadata else None,
                    extracted_company_name,
                    now, now, now, now
                )
            )
            signal_id = cursor.lastrowid

        logger.debug(f"Saved new signal {signal_id}: {source_api}/{norm_source_id}")
        return signal_id, True

    async def get_signal(self, signal_id: int) -> Optional[StoredSignal]:
        """Get a signal by ID."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, source_api, source_id, signal_type, content_hash,
                   title, url, source_context, raw_metadata,
                   status, filter_result, filter_stage,
                   extracted_company_name, notion_page_id, company_id,
                   first_seen_at, last_seen_at, created_at, updated_at
            FROM signals WHERE id = ?
            """,
            (signal_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_signal(row) if row else None

    async def get_pending_signals(self, limit: int = 100) -> List[StoredSignal]:
        """Get signals pending classification/review."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, source_api, source_id, signal_type, content_hash,
                   title, url, source_context, raw_metadata,
                   status, filter_result, filter_stage,
                   extracted_company_name, notion_page_id, company_id,
                   first_seen_at, last_seen_at, created_at, updated_at
            FROM signals
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_signal(row) for row in rows]

    async def is_duplicate(self, source_api: str, source_id: str) -> bool:
        """Check if signal already exists."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        norm_source_id = normalize_source_id(source_api, source_id)
        cursor = await self._db.execute(
            "SELECT 1 FROM signals WHERE source_api = ? AND source_id = ?",
            (source_api, norm_source_id)
        )
        return await cursor.fetchone() is not None

    async def update_signal_filter_result(
        self,
        signal_id: int,
        filter_result: str,
        filter_stage: str,
    ) -> None:
        """Update signal with filter decision."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signals
                SET filter_result = ?, filter_stage = ?, updated_at = ?
                WHERE id = ?
                """,
                (filter_result, filter_stage, now, signal_id)
            )

    async def update_signal_status(
        self,
        signal_id: int,
        status: str,
        notion_page_id: Optional[str] = None,
    ) -> None:
        """Update signal status."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        async with self.transaction() as conn:
            if notion_page_id:
                await conn.execute(
                    "UPDATE signals SET status = ?, notion_page_id = ?, updated_at = ? WHERE id = ?",
                    (status, notion_page_id, now, signal_id)
                )
            else:
                await conn.execute(
                    "UPDATE signals SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, signal_id)
                )

    # =========================================================================
    # LLM CLASSIFICATION
    # =========================================================================

    async def save_classification(
        self,
        signal_id: int,
        model: str,
        prompt_version: str,
        thesis_match: bool,
        confidence: float,
        categories: List[str],
        reasoning: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
    ) -> int:
        """Save LLM classification result."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO llm_classifications (
                    signal_id, model, prompt_version,
                    thesis_match, confidence, categories, reasoning,
                    input_tokens, output_tokens, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id, model, prompt_version,
                    1 if thesis_match else 0, confidence,
                    json.dumps(categories), reasoning,
                    input_tokens, output_tokens, latency_ms, now
                )
            )
            return cursor.lastrowid

    async def get_classification(self, signal_id: int) -> Optional[LLMClassification]:
        """Get latest classification for a signal."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, signal_id, model, prompt_version,
                   thesis_match, confidence, categories, reasoning,
                   input_tokens, output_tokens, latency_ms, created_at
            FROM llm_classifications
            WHERE signal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (signal_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        return LLMClassification(
            id=row[0],
            signal_id=row[1],
            model=row[2],
            prompt_version=row[3],
            thesis_match=bool(row[4]),
            confidence=row[5],
            categories=json.loads(row[6]) if row[6] else [],
            reasoning=row[7] or "",
            input_tokens=row[8],
            output_tokens=row[9],
            latency_ms=row[10],
            created_at=datetime.fromisoformat(row[11]),
        )

    # =========================================================================
    # COLLECTOR RUNS
    # =========================================================================

    async def start_collector_run(self, collector_name: str) -> int:
        """Record start of a collector run."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO collector_runs (collector_name, status, started_at)
                VALUES (?, 'running', ?)
                """,
                (collector_name, now)
            )
            return cursor.lastrowid

    async def complete_collector_run(
        self,
        run_id: int,
        status: str,
        signals_found: int,
        signals_new: int,
        error_message: Optional[str] = None,
        api_calls_made: int = 0,
    ) -> None:
        """Record completion of a collector run."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)

        async with self.transaction() as conn:
            # Get start time for duration
            cursor = await conn.execute(
                "SELECT started_at FROM collector_runs WHERE id = ?",
                (run_id,)
            )
            row = await cursor.fetchone()
            started_at = datetime.fromisoformat(row[0]) if row else now
            duration = (now - started_at).total_seconds()

            await conn.execute(
                """
                UPDATE collector_runs
                SET status = ?, signals_found = ?, signals_new = ?,
                    completed_at = ?, duration_seconds = ?,
                    error_message = ?, api_calls_made = ?
                WHERE id = ?
                """,
                (
                    status, signals_found, signals_new,
                    now.isoformat(), duration,
                    error_message, api_calls_made, run_id
                )
            )

    async def get_recent_runs(
        self,
        collector_name: Optional[str] = None,
        limit: int = 10,
    ) -> List[CollectorRun]:
        """Get recent collector runs."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        if collector_name:
            cursor = await self._db.execute(
                """
                SELECT id, collector_name, status, signals_found, signals_new,
                       started_at, completed_at, duration_seconds, error_message, api_calls_made
                FROM collector_runs
                WHERE collector_name = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (collector_name, limit)
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT id, collector_name, status, signals_found, signals_new,
                       started_at, completed_at, duration_seconds, error_message, api_calls_made
                FROM collector_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,)
            )

        rows = await cursor.fetchall()
        return [
            CollectorRun(
                id=row[0],
                collector_name=row[1],
                status=row[2],
                signals_found=row[3],
                signals_new=row[4],
                started_at=datetime.fromisoformat(row[5]),
                completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
                duration_seconds=row[7],
                error_message=row[8],
                api_calls_made=row[9],
            )
            for row in rows
        ]

    # =========================================================================
    # COST TRACKING
    # =========================================================================

    async def track_cost(
        self,
        service: str,
        operation: str,
        units_consumed: int = 1,
        estimated_cost_usd: Optional[float] = None,
        triggered_by: Optional[str] = None,
        related_signal_id: Optional[int] = None,
    ) -> None:
        """Track API usage cost."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO cost_tracking (
                    service, operation, units_consumed, estimated_cost_usd,
                    triggered_by, related_signal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service, operation, units_consumed, estimated_cost_usd,
                    triggered_by, related_signal_id, now
                )
            )

    async def get_cost_summary(self, days: int = 30) -> Dict[str, float]:
        """Get cost summary by service."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cutoff = (datetime.now(timezone.utc) -
                  __import__('datetime').timedelta(days=days)).isoformat()

        cursor = await self._db.execute(
            """
            SELECT service, SUM(estimated_cost_usd)
            FROM cost_tracking
            WHERE created_at >= ? AND estimated_cost_usd IS NOT NULL
            GROUP BY service
            """,
            (cutoff,)
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] or 0.0 for row in rows}

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """Get overall database statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Signal counts by status
        cursor = await self._db.execute(
            "SELECT status, COUNT(*) FROM signals GROUP BY status"
        )
        status_counts = dict(await cursor.fetchall())

        # Signal counts by source
        cursor = await self._db.execute(
            "SELECT source_api, COUNT(*) FROM signals GROUP BY source_api"
        )
        source_counts = dict(await cursor.fetchall())

        # Filter results
        cursor = await self._db.execute(
            "SELECT filter_result, COUNT(*) FROM signals WHERE filter_result IS NOT NULL GROUP BY filter_result"
        )
        filter_counts = dict(await cursor.fetchall())

        # Total signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        # Total classifications
        cursor = await self._db.execute("SELECT COUNT(*) FROM llm_classifications")
        total_classifications = (await cursor.fetchone())[0]

        return {
            "total_signals": total_signals,
            "total_classifications": total_classifications,
            "signals_by_status": status_counts,
            "signals_by_source": source_counts,
            "signals_by_filter_result": filter_counts,
            "database_path": str(self.db_path),
        }

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _row_to_signal(self, row: tuple) -> StoredSignal:
        """Convert database row to StoredSignal."""
        return StoredSignal(
            id=row[0],
            source_api=row[1],
            source_id=row[2],
            signal_type=row[3],
            content_hash=row[4],
            title=row[5],
            url=row[6],
            source_context=row[7],
            raw_metadata=json.loads(row[8]) if row[8] else None,
            status=row[9],
            filter_result=row[10],
            filter_stage=row[11],
            extracted_company_name=row[12],
            notion_page_id=row[13],
            company_id=row[14],
            first_seen_at=datetime.fromisoformat(row[15]),
            last_seen_at=datetime.fromisoformat(row[16]),
            created_at=datetime.fromisoformat(row[17]),
            updated_at=datetime.fromisoformat(row[18]),
        )


# =============================================================================
# CONTEXT MANAGER
# =============================================================================

@asynccontextmanager
async def consumer_store(
    db_path: str | Path = "consumer_signals.db",
) -> AsyncIterator[ConsumerStore]:
    """
    Context manager for ConsumerStore.

    Usage:
        async with consumer_store("signals.db") as store:
            await store.save_signal(...)
    """
    store = ConsumerStore(db_path)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()
