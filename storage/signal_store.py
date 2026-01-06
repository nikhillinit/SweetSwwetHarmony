"""
Signal Store - SQLite-based persistence for Discovery Engine signals.

Provides:
- Signal storage and retrieval
- Deduplication checking via canonical keys
- Suppression cache for Notion sync
- Entity grouping by canonical key

Usage:
    from storage.signal_store import SignalStore

    store = SignalStore(db_path="signals.db")
    await store.initialize()

    # Save a signal
    signal_id = await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:acme.ai",
        confidence=0.85,
        raw_data={"repo": "acme/core"},
    )

    # Check for duplicates
    is_dup = await store.is_duplicate("domain:acme.ai")
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SuppressionEntry:
    """Entry in the suppression cache (already in Notion)."""
    canonical_key: str
    notion_page_id: str
    notion_status: str
    synced_at: datetime
    expires_at: datetime
    company_name: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


@dataclass
class StoredSignal:
    """A signal stored in the database."""
    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    confidence: float
    raw_data: Dict[str, Any]
    company_name: Optional[str]
    detected_at: datetime
    created_at: datetime
    pushed_to_notion: bool = False
    notion_page_id: Optional[str] = None


class SignalStore:
    """
    SQLite-based signal storage with async interface.

    Uses a thread pool for database operations since sqlite3 is synchronous.
    """

    def __init__(self, db_path: str = "signals.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize database and create tables if needed."""
        if self._initialized:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_db)
        self._initialized = True
        logger.info(f"SignalStore initialized: {self.db_path}")

    def _init_db(self) -> None:
        """Synchronous database initialization."""
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Create tables
        self._conn.executescript("""
            -- Signals table
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT NOT NULL,
                source_api TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                confidence REAL NOT NULL,
                raw_data TEXT NOT NULL,
                company_name TEXT,
                detected_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                pushed_to_notion INTEGER DEFAULT 0,
                notion_page_id TEXT
            );

            -- Indexes for fast lookup
            CREATE INDEX IF NOT EXISTS idx_signals_canonical_key
                ON signals(canonical_key);
            CREATE INDEX IF NOT EXISTS idx_signals_type
                ON signals(signal_type);
            CREATE INDEX IF NOT EXISTS idx_signals_detected_at
                ON signals(detected_at);
            CREATE INDEX IF NOT EXISTS idx_signals_pushed
                ON signals(pushed_to_notion);

            -- Suppression cache (companies already in Notion)
            CREATE TABLE IF NOT EXISTS suppression_cache (
                canonical_key TEXT PRIMARY KEY,
                notion_page_id TEXT NOT NULL,
                notion_status TEXT NOT NULL,
                company_name TEXT,
                synced_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            -- Index for expiration cleanup
            CREATE INDEX IF NOT EXISTS idx_suppression_expires
                ON suppression_cache(expires_at);
        """)
        self._conn.commit()

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._conn.close)
            self._conn = None
            self._initialized = False

    async def __aenter__(self) -> "SignalStore":
        await self.initialize()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # -------------------------------------------------------------------------
    # Signal Operations
    # -------------------------------------------------------------------------

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

        Returns:
            Signal ID
        """
        if not self._initialized:
            await self.initialize()

        detected_at = detected_at or datetime.now(timezone.utc)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._save_signal_sync,
            signal_type,
            source_api,
            canonical_key,
            confidence,
            raw_data,
            company_name,
            detected_at,
        )

    def _save_signal_sync(
        self,
        signal_type: str,
        source_api: str,
        canonical_key: str,
        confidence: float,
        raw_data: Dict[str, Any],
        company_name: Optional[str],
        detected_at: datetime,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO signals
                (signal_type, source_api, canonical_key, confidence,
                 raw_data, company_name, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_type,
                source_api,
                canonical_key,
                confidence,
                json.dumps(raw_data),
                company_name,
                detected_at.isoformat(),
            )
        )
        self._conn.commit()
        return cursor.lastrowid

    async def is_duplicate(self, canonical_key: str) -> bool:
        """Check if a signal with this canonical key already exists."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._is_duplicate_sync,
            canonical_key,
        )

    def _is_duplicate_sync(self, canonical_key: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM signals WHERE canonical_key = ? LIMIT 1",
            (canonical_key,)
        )
        return cursor.fetchone() is not None

    async def get_signals_by_canonical_key(
        self,
        canonical_key: str
    ) -> List[StoredSignal]:
        """Get all signals for a canonical key."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._get_signals_by_key_sync,
            canonical_key,
        )

    def _get_signals_by_key_sync(self, canonical_key: str) -> List[StoredSignal]:
        cursor = self._conn.execute(
            """
            SELECT * FROM signals
            WHERE canonical_key = ?
            ORDER BY detected_at DESC
            """,
            (canonical_key,)
        )
        return [self._row_to_signal(row) for row in cursor.fetchall()]

    async def get_pending_signals(self, limit: int = 100) -> List[StoredSignal]:
        """Get signals not yet pushed to Notion."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._get_pending_sync,
            limit,
        )

    def _get_pending_sync(self, limit: int) -> List[StoredSignal]:
        cursor = self._conn.execute(
            """
            SELECT * FROM signals
            WHERE pushed_to_notion = 0
            ORDER BY confidence DESC, detected_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [self._row_to_signal(row) for row in cursor.fetchall()]

    async def mark_pushed(
        self,
        signal_id: int,
        notion_page_id: str
    ) -> None:
        """Mark a signal as pushed to Notion."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._mark_pushed_sync,
            signal_id,
            notion_page_id,
        )

    def _mark_pushed_sync(self, signal_id: int, notion_page_id: str) -> None:
        self._conn.execute(
            """
            UPDATE signals
            SET pushed_to_notion = 1, notion_page_id = ?
            WHERE id = ?
            """,
            (notion_page_id, signal_id)
        )
        self._conn.commit()

    def _row_to_signal(self, row: sqlite3.Row) -> StoredSignal:
        """Convert database row to StoredSignal."""
        return StoredSignal(
            id=row["id"],
            signal_type=row["signal_type"],
            source_api=row["source_api"],
            canonical_key=row["canonical_key"],
            confidence=row["confidence"],
            raw_data=json.loads(row["raw_data"]),
            company_name=row["company_name"],
            detected_at=datetime.fromisoformat(row["detected_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            pushed_to_notion=bool(row["pushed_to_notion"]),
            notion_page_id=row["notion_page_id"],
        )

    # -------------------------------------------------------------------------
    # Suppression Cache Operations
    # -------------------------------------------------------------------------

    async def check_suppression(
        self,
        canonical_key: str
    ) -> Optional[SuppressionEntry]:
        """Check if a canonical key is in the suppression cache."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._check_suppression_sync,
            canonical_key,
        )

    def _check_suppression_sync(self, canonical_key: str) -> Optional[SuppressionEntry]:
        cursor = self._conn.execute(
            """
            SELECT * FROM suppression_cache
            WHERE canonical_key = ?
            """,
            (canonical_key,)
        )
        row = cursor.fetchone()
        if not row:
            return None

        entry = SuppressionEntry(
            canonical_key=row["canonical_key"],
            notion_page_id=row["notion_page_id"],
            notion_status=row["notion_status"],
            synced_at=datetime.fromisoformat(row["synced_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            company_name=row["company_name"],
        )

        # Return None if expired
        if entry.is_expired:
            return None

        return entry

    async def add_suppression(
        self,
        canonical_key: str,
        notion_page_id: str,
        notion_status: str,
        company_name: Optional[str] = None,
        ttl_days: int = 7,
    ) -> None:
        """Add an entry to the suppression cache."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._add_suppression_sync,
            canonical_key,
            notion_page_id,
            notion_status,
            company_name,
            ttl_days,
        )

    def _add_suppression_sync(
        self,
        canonical_key: str,
        notion_page_id: str,
        notion_status: str,
        company_name: Optional[str],
        ttl_days: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=ttl_days)

        self._conn.execute(
            """
            INSERT OR REPLACE INTO suppression_cache
                (canonical_key, notion_page_id, notion_status,
                 company_name, synced_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_key,
                notion_page_id,
                notion_status,
                company_name,
                now.isoformat(),
                expires_at.isoformat(),
            )
        )
        self._conn.commit()

    async def clear_expired_suppressions(self) -> int:
        """Remove expired suppression entries. Returns count removed."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._clear_expired_sync,
        )

    def _clear_expired_sync(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM suppression_cache WHERE expires_at < ?",
            (now,)
        )
        self._conn.commit()
        return cursor.rowcount

    async def get_suppression_stats(self) -> Dict[str, Any]:
        """Get statistics about the suppression cache."""
        if not self._initialized:
            await self.initialize()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_stats_sync)

    def _get_stats_sync(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()

        # Total entries
        total = self._conn.execute(
            "SELECT COUNT(*) FROM suppression_cache"
        ).fetchone()[0]

        # Active entries
        active = self._conn.execute(
            "SELECT COUNT(*) FROM suppression_cache WHERE expires_at >= ?",
            (now,)
        ).fetchone()[0]

        # By status
        by_status = {}
        cursor = self._conn.execute(
            """
            SELECT notion_status, COUNT(*) as count
            FROM suppression_cache
            WHERE expires_at >= ?
            GROUP BY notion_status
            """,
            (now,)
        )
        for row in cursor.fetchall():
            by_status[row[0]] = row[1]

        return {
            "total_entries": total,
            "active_entries": active,
            "expired_entries": total - active,
            "by_status": by_status,
        }


# Convenience context manager
@asynccontextmanager
async def signal_store(db_path: str = "signals.db") -> AsyncIterator[SignalStore]:
    """
    Context manager for SignalStore.

    Usage:
        async with signal_store("signals.db") as store:
            await store.save_signal(...)
    """
    store = SignalStore(db_path=db_path)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()
