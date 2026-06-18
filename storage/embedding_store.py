"""
EmbeddingStore - SQLite storage for company embeddings.

Provides persistent storage for:
- Company embeddings (768-dim vectors)
- Profile FTS index for keyword search
- Staleness detection via source_text_hash

Sprint 4: Similar Companies feature.

Usage:
    async with EmbeddingStore("embeddings.db") as store:
        await store.save_embedding("domain:acme.ai", embedding, "hash123")
        embedding = await store.get_embedding("domain:acme.ai")
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import aiosqlite
import numpy as np

from utils.embedding_generator import serialize_embedding, deserialize_embedding
from utils.db_path_helper import resolve_db_path_env

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA
# =============================================================================

# Same schema as Migration 8 in signal_store.py, but standalone for EmbeddingStore
EMBEDDING_SCHEMA = """
-- Embedding cache table
CREATE TABLE IF NOT EXISTS company_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL,
    embedding_kind TEXT NOT NULL DEFAULT 'profile_v1',

    -- Embedding data
    embedding BLOB NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-004',
    embedding_version TEXT NOT NULL DEFAULT 'v1',

    -- Staleness detection
    source_text_hash TEXT NOT NULL,
    source_text_preview TEXT,

    -- Timestamps
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,

    -- Ensure one embedding per company/kind/model/version
    UNIQUE (canonical_key, embedding_kind, embedding_model, embedding_version)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_key ON company_embeddings(canonical_key);
CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON company_embeddings(source_text_hash);
CREATE INDEX IF NOT EXISTS idx_embeddings_kind ON company_embeddings(embedding_kind);

-- FTS index for company profiles
CREATE VIRTUAL TABLE IF NOT EXISTS company_profiles_fts USING fts5(
    canonical_key UNINDEXED,
    company_name,
    searchable_text,
    category,
    business_model,
    tokenize='porter unicode61'
);
"""


# =============================================================================
# EMBEDDING STORE
# =============================================================================

class EmbeddingStore:
    """
    Async SQLite storage for company embeddings.

    Features:
    - Save/retrieve embeddings with numpy serialization
    - Batch retrieval for efficient similarity computation
    - Staleness detection via source_text_hash
    - FTS5 profile search for candidate retrieval
    """

    DEFAULT_EMBEDDING_KIND = "profile_v1"
    DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
    DEFAULT_EMBEDDING_VERSION = "v1"

    def __init__(
        self,
        db_path: str | Path | None = None,
        embedding_kind: str = DEFAULT_EMBEDDING_KIND,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    ):
        """
        Initialize embedding store.

        Args:
            db_path: Path to SQLite database file
            embedding_kind: Type of embedding (default: profile_v1)
            embedding_model: Model used for embedding
            embedding_version: Version for cache invalidation
        """
        self.db_path = Path(resolve_db_path_env(db_path))
        self.embedding_kind = embedding_kind
        self.embedding_model = embedding_model
        self.embedding_version = embedding_version
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """
        Initialize database connection and create schema.
        Should be called once at startup.
        """
        if self._initialized:
            return

        # Create parent directories if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to database
        self._db = await aiosqlite.connect(str(self.db_path))

        # Enable foreign keys
        await self._db.execute("PRAGMA foreign_keys = ON")

        # Create tables
        await self._create_schema()

        self._initialized = True

    async def _create_schema(self) -> None:
        """Create embedding tables if they don't exist."""
        # Split schema into individual statements
        statements = [s.strip() for s in EMBEDDING_SCHEMA.split(";") if s.strip()]

        for statement in statements:
            if statement:
                try:
                    await self._db.execute(statement)
                except Exception as e:
                    # Ignore errors for CREATE IF NOT EXISTS
                    if "already exists" not in str(e).lower():
                        logger.debug(f"Schema statement note: {e}")

        await self._db.commit()

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            self._initialized = False

    async def __aenter__(self) -> "EmbeddingStore":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    # =========================================================================
    # EMBEDDING OPERATIONS
    # =========================================================================

    async def save_embedding(
        self,
        canonical_key: str,
        embedding: np.ndarray,
        source_text_hash: str,
        source_text_preview: Optional[str] = None,
        embedding_kind: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_version: Optional[str] = None,
    ) -> int:
        """
        Save an embedding to the database.

        Uses upsert: if key exists with same kind/model/version, updates it.

        Args:
            canonical_key: Company canonical key (e.g., "domain:acme.ai")
            embedding: numpy array (768-dim float32)
            source_text_hash: SHA256 hash of source text
            source_text_preview: First N chars of source text (for debugging)
            embedding_kind: Override default embedding kind
            embedding_model: Override default model name
            embedding_version: Override default version

        Returns:
            Row ID of the saved embedding
        """
        kind = embedding_kind or self.embedding_kind
        model = embedding_model or self.embedding_model
        version = embedding_version or self.embedding_version

        # Serialize embedding to bytes
        embedding_bytes = serialize_embedding(embedding)

        now = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            # Upsert via INSERT OR REPLACE
            cursor = await self._db.execute(
                """
                INSERT INTO company_embeddings (
                    canonical_key, embedding_kind, embedding, embedding_model,
                    embedding_version, source_text_hash, source_text_preview,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (canonical_key, embedding_kind, embedding_model, embedding_version)
                DO UPDATE SET
                    embedding = excluded.embedding,
                    source_text_hash = excluded.source_text_hash,
                    source_text_preview = excluded.source_text_preview,
                    updated_at = excluded.updated_at
                """,
                (
                    canonical_key,
                    kind,
                    embedding_bytes,
                    model,
                    version,
                    source_text_hash,
                    source_text_preview,
                    now,
                    now,
                ),
            )
            await self._db.commit()
            return cursor.lastrowid

    async def get_embedding(
        self,
        canonical_key: str,
        embedding_kind: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_version: Optional[str] = None,
    ) -> Optional[np.ndarray]:
        """
        Get an embedding by canonical key.

        Args:
            canonical_key: Company canonical key
            embedding_kind: Override default embedding kind
            embedding_model: Override default model name
            embedding_version: Override default version

        Returns:
            numpy array if found, None otherwise
        """
        kind = embedding_kind or self.embedding_kind
        model = embedding_model or self.embedding_model
        version = embedding_version or self.embedding_version

        cursor = await self._db.execute(
            """
            SELECT embedding FROM company_embeddings
            WHERE canonical_key = ?
              AND embedding_kind = ?
              AND embedding_model = ?
              AND embedding_version = ?
            """,
            (canonical_key, kind, model, version),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return deserialize_embedding(row[0])

    async def get_embeddings_batch(
        self,
        canonical_keys: List[str],
        embedding_kind: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_version: Optional[str] = None,
    ) -> Dict[str, np.ndarray]:
        """
        Get embeddings for multiple keys in a single query.

        Args:
            canonical_keys: List of canonical keys
            embedding_kind: Override default embedding kind
            embedding_model: Override default model name
            embedding_version: Override default version

        Returns:
            Dict mapping canonical_key to embedding (only for found keys)
        """
        if not canonical_keys:
            return {}

        kind = embedding_kind or self.embedding_kind
        model = embedding_model or self.embedding_model
        version = embedding_version or self.embedding_version

        # Build query with placeholders
        placeholders = ",".join("?" * len(canonical_keys))
        query = f"""
            SELECT canonical_key, embedding FROM company_embeddings
            WHERE canonical_key IN ({placeholders})
              AND embedding_kind = ?
              AND embedding_model = ?
              AND embedding_version = ?
        """

        cursor = await self._db.execute(
            query,
            (*canonical_keys, kind, model, version),
        )
        rows = await cursor.fetchall()

        return {
            row[0]: deserialize_embedding(row[1])
            for row in rows
        }

    async def get_stale_keys(
        self,
        current_hashes: Dict[str, str],
        embedding_kind: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_version: Optional[str] = None,
    ) -> List[str]:
        """
        Find keys that need re-embedding (hash changed or not present).

        Args:
            current_hashes: Dict mapping canonical_key to current source_text_hash
            embedding_kind: Override default embedding kind
            embedding_model: Override default model name
            embedding_version: Override default version

        Returns:
            List of canonical_keys that are stale or missing
        """
        if not current_hashes:
            return []

        kind = embedding_kind or self.embedding_kind
        model = embedding_model or self.embedding_model
        version = embedding_version or self.embedding_version

        # Get stored hashes for all keys
        keys = list(current_hashes.keys())
        placeholders = ",".join("?" * len(keys))

        cursor = await self._db.execute(
            f"""
            SELECT canonical_key, source_text_hash FROM company_embeddings
            WHERE canonical_key IN ({placeholders})
              AND embedding_kind = ?
              AND embedding_model = ?
              AND embedding_version = ?
            """,
            (*keys, kind, model, version),
        )
        rows = await cursor.fetchall()

        stored_hashes = {row[0]: row[1] for row in rows}

        # Find stale keys
        stale = []
        for key, current_hash in current_hashes.items():
            stored_hash = stored_hashes.get(key)
            if stored_hash is None or stored_hash != current_hash:
                stale.append(key)

        return stale

    # =========================================================================
    # FTS OPERATIONS
    # =========================================================================

    async def index_profile(
        self,
        canonical_key: str,
        company_name: str,
        searchable_text: str,
        category: Optional[str] = None,
        business_model: Optional[str] = None,
    ) -> None:
        """
        Index a company profile for FTS search.

        Uses upsert: if key exists, updates it.

        Args:
            canonical_key: Company canonical key
            company_name: Company name
            searchable_text: Combined profile text for keyword matching
            category: Company category
            business_model: Business model
        """
        async with self._lock:
            # Delete existing entry if present (FTS5 doesn't support UPDATE)
            await self._db.execute(
                "DELETE FROM company_profiles_fts WHERE canonical_key = ?",
                (canonical_key,),
            )

            # Insert new entry
            await self._db.execute(
                """
                INSERT INTO company_profiles_fts (
                    canonical_key, company_name, searchable_text, category, business_model
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (canonical_key, company_name, searchable_text, category or "", business_model or ""),
            )
            await self._db.commit()

    async def search_profiles(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 300,
    ) -> List[Dict[str, Any]]:
        """
        Search profiles using FTS5.

        Args:
            query: Search query (keywords)
            category: Optional category filter
            limit: Maximum results

        Returns:
            List of dicts with canonical_key, company_name, category, business_model
        """
        if not query.strip():
            return []

        # Sanitize query for FTS5
        safe_query = self._sanitize_fts_query(query)
        if not safe_query:
            return []

        # Build query
        if category:
            cursor = await self._db.execute(
                """
                SELECT canonical_key, company_name, category, business_model,
                       bm25(company_profiles_fts) as rank
                FROM company_profiles_fts
                WHERE company_profiles_fts MATCH ?
                  AND category = ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, category, limit),
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT canonical_key, company_name, category, business_model,
                       bm25(company_profiles_fts) as rank
                FROM company_profiles_fts
                WHERE company_profiles_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, limit),
            )

        rows = await cursor.fetchall()

        return [
            {
                "canonical_key": row[0],
                "company_name": row[1],
                "category": row[2],
                "business_model": row[3],
                "rank": row[4],
            }
            for row in rows
        ]

    def _sanitize_fts_query(self, query: str) -> str:
        """
        Sanitize a query string for FTS5.

        Args:
            query: Raw query string

        Returns:
            FTS5-safe query string
        """
        # Remove special FTS5 characters (including periods which cause syntax errors)
        safe = re.sub(r'[":*\-+()^.]', " ", query)

        # Normalize whitespace
        safe = " ".join(safe.split())

        # Add prefix matching
        if safe:
            safe = f"{safe}*"

        return safe

    async def get_all_profiles(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Get all indexed profiles (without FTS search).

        Args:
            limit: Maximum profiles to return

        Returns:
            List of all profile dicts
        """
        cursor = await self._db.execute(
            """
            SELECT canonical_key, company_name, searchable_text, category, business_model
            FROM company_profiles_fts
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

        return [
            {
                "canonical_key": row[0],
                "company_name": row[1],
                "searchable_text": row[2],
                "category": row[3],
                "business_model": row[4],
            }
            for row in rows
        ]

    # =========================================================================
    # STATS
    # =========================================================================

    async def get_stats(self) -> Dict[str, int]:
        """
        Get embedding store statistics.

        Returns:
            Dict with total_embeddings, total_profiles counts
        """
        cursor = await self._db.execute("SELECT COUNT(*) FROM company_embeddings")
        row = await cursor.fetchone()
        total_embeddings = row[0] if row else 0

        cursor = await self._db.execute("SELECT COUNT(*) FROM company_profiles_fts")
        row = await cursor.fetchone()
        total_profiles = row[0] if row else 0

        return {
            "total_embeddings": total_embeddings,
            "total_profiles": total_profiles,
        }
