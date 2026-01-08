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
    from workflows.pipeline import PipelineStats

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA VERSION
# =============================================================================

CURRENT_SCHEMA_VERSION = 2

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
        status TEXT NOT NULL,  -- 'pending', 'pushed', 'rejected'
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
