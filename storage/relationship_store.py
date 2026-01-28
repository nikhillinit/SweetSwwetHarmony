"""
RelationshipStore - Private relationship graph storage.

Stores domain-level relationship strength from email interaction history.
CRITICAL: Uses separate private_graph.db (NOT signals.db) for privacy isolation.

Key features:
- Email hashing for privacy (no plaintext emails stored)
- Domain-level aggregation (not individual email addresses)
- Deterministic strength scoring
- Relationship recency tracking

Usage:
    store = RelationshipStore("private_graph.db")
    await store.initialize()

    await store.upsert_domain_edge(
        me_email="user@example.com",
        target_domain="investor.com",
        intro_count=3,
        reply_count=2,
        total_messages=10,
        last_contact_at=datetime.now(timezone.utc),
    )

    strength = await store.get_domain_strength("user@example.com", "investor.com")
    print(f"Relationship strength: {strength.strength_score:.2f}")
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DomainStrength:
    """Relationship strength for a domain."""
    target_domain: str
    intro_count: int
    reply_count: int
    total_messages: int
    reply_rate: float
    recency_score: float
    strength_score: float
    last_contact_at: datetime
    first_contact_at: datetime


# =============================================================================
# RELATIONSHIP STORE
# =============================================================================

class RelationshipStore:
    """
    Private relationship graph storage.

    Stores domain-level relationship strength aggregated from email interactions.
    Uses separate private_graph.db to isolate from signals.db.
    """

    DEFAULT_DB_PATH = "private_graph.db"

    # Schema version
    SCHEMA_VERSION = 1

    # Strength formula weights
    INTRO_WEIGHT = 0.50
    REPLY_WEIGHT = 0.35
    RECENCY_WEIGHT = 0.15

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        """
        Initialize RelationshipStore.

        Args:
            db_path: Path to private graph database (default: private_graph.db)
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Initialize database connection and create schema.
        """
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row

        await self._create_schema()
        logger.info(f"RelationshipStore initialized: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self):
        """
        Context manager for database transactions.

        Usage:
            async with store.transaction() as conn:
                await conn.execute(...)
                await conn.commit()
        """
        async with self._lock:
            if not self._db:
                raise RuntimeError("Store not initialized. Call initialize() first.")
            yield self._db

    async def _create_schema(self) -> None:
        """Create database schema."""
        async with self.transaction() as conn:
            # Domain relationships table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS domain_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    me_email_hash TEXT NOT NULL,
                    target_domain TEXT NOT NULL,
                    intro_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    total_messages INTEGER NOT NULL DEFAULT 0,
                    last_contact_at TEXT,
                    first_contact_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(me_email_hash, target_domain)
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rel_email_domain
                ON domain_relationships(me_email_hash, target_domain)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rel_last_contact
                ON domain_relationships(last_contact_at)
            """)

            # Schema version tracking
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """)

            # Insert schema version
            await conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (self.SCHEMA_VERSION, datetime.now(timezone.utc).isoformat())
            )

            await conn.commit()

    @staticmethod
    def _hash_email(email: str) -> str:
        """
        Hash email for privacy.

        Args:
            email: Email address

        Returns:
            SHA256 hash of email (lowercase)
        """
        return hashlib.sha256(email.lower().encode()).hexdigest()

    async def upsert_domain_edge(
        self,
        me_email: str,
        target_domain: str,
        intro_count: int,
        reply_count: int,
        total_messages: int,
        last_contact_at: datetime,
        first_contact_at: Optional[datetime] = None,
    ) -> None:
        """
        Upsert domain relationship edge.

        Args:
            me_email: User's email (will be hashed)
            target_domain: Target domain
            intro_count: Number of intro emails sent
            reply_count: Number of replies received
            total_messages: Total messages exchanged
            last_contact_at: Timestamp of last contact
            first_contact_at: Timestamp of first contact (optional)
        """
        me_email_hash = self._hash_email(me_email)
        now = datetime.now(timezone.utc)

        async with self.transaction() as conn:
            # Check if edge exists
            cursor = await conn.execute(
                "SELECT id, first_contact_at FROM domain_relationships WHERE me_email_hash = ? AND target_domain = ?",
                (me_email_hash, target_domain)
            )
            row = await cursor.fetchone()

            if row:
                # Update existing edge
                existing_first_contact = row[1]
                await conn.execute(
                    """
                    UPDATE domain_relationships
                    SET intro_count = ?,
                        reply_count = ?,
                        total_messages = ?,
                        last_contact_at = ?,
                        updated_at = ?
                    WHERE me_email_hash = ? AND target_domain = ?
                    """,
                    (
                        intro_count,
                        reply_count,
                        total_messages,
                        last_contact_at.isoformat(),
                        now.isoformat(),
                        me_email_hash,
                        target_domain,
                    )
                )
            else:
                # Insert new edge
                first_contact = first_contact_at or last_contact_at
                await conn.execute(
                    """
                    INSERT INTO domain_relationships (
                        me_email_hash, target_domain,
                        intro_count, reply_count, total_messages,
                        last_contact_at, first_contact_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        me_email_hash,
                        target_domain,
                        intro_count,
                        reply_count,
                        total_messages,
                        last_contact_at.isoformat(),
                        first_contact.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    )
                )

            await conn.commit()

        logger.debug(f"Upserted domain edge: {target_domain} (intros={intro_count}, replies={reply_count})")

    async def get_domain_strength(
        self,
        me_email: str,
        target_domain: str,
    ) -> Optional[DomainStrength]:
        """
        Get relationship strength for a domain.

        Args:
            me_email: User's email (will be hashed)
            target_domain: Target domain

        Returns:
            DomainStrength or None if not found
        """
        me_email_hash = self._hash_email(me_email)

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT intro_count, reply_count, total_messages,
                       last_contact_at, first_contact_at
                FROM domain_relationships
                WHERE me_email_hash = ? AND target_domain = ?
                """,
                (me_email_hash, target_domain)
            )
            row = await cursor.fetchone()

        if not row:
            return None

        intro_count = row[0]
        reply_count = row[1]
        total_messages = row[2]
        last_contact_at = datetime.fromisoformat(row[3])
        first_contact_at = datetime.fromisoformat(row[4])

        # Calculate components
        reply_rate = reply_count / total_messages if total_messages > 0 else 0.0
        recency_score = self._calculate_recency_score(last_contact_at)
        strength_score = self._calculate_strength_score(
            intro_count, reply_rate, recency_score
        )

        return DomainStrength(
            target_domain=target_domain,
            intro_count=intro_count,
            reply_count=reply_count,
            total_messages=total_messages,
            reply_rate=reply_rate,
            recency_score=recency_score,
            strength_score=strength_score,
            last_contact_at=last_contact_at,
            first_contact_at=first_contact_at,
        )

    @staticmethod
    def _sigmoid(x: float, k: float = 0.5) -> float:
        """Sigmoid function for smooth saturation."""
        return 1.0 / (1.0 + math.exp(-k * x))

    @staticmethod
    def _clamp01(value: float) -> float:
        """Clamp value to [0, 1]."""
        return max(0.0, min(1.0, value))

    def _calculate_recency_score(self, last_contact_at: datetime) -> float:
        """
        Calculate recency score.

        Args:
            last_contact_at: Timestamp of last contact

        Returns:
            Recency score (0.0-1.0)
        """
        now = datetime.now(timezone.utc)
        days_since = (now - last_contact_at).days

        # 1.0 for recent (0 days), 0.0 for stale (365+ days)
        recency_score = 1.0 - min(days_since, 365) / 365
        return self._clamp01(recency_score)

    def _calculate_strength_score(
        self,
        intro_count: int,
        reply_rate: float,
        recency_score: float,
    ) -> float:
        """
        Calculate deterministic relationship strength score.

        Formula:
            strength = clamp01(
                0.50 * sigmoid(intro_count) +
                0.35 * reply_rate +
                0.15 * recency_score
            )

        Args:
            intro_count: Number of intro emails
            reply_rate: Reply rate (0.0-1.0)
            recency_score: Recency score (0.0-1.0)

        Returns:
            Strength score (0.0-1.0)
        """
        intro_score = self._sigmoid(intro_count, k=0.3)  # Gentle saturation
        weighted_score = (
            self.INTRO_WEIGHT * intro_score +
            self.REPLY_WEIGHT * reply_rate +
            self.RECENCY_WEIGHT * recency_score
        )

        return self._clamp01(weighted_score)

    async def get_all_relationships(
        self,
        me_email: str,
        min_strength: float = 0.0,
    ) -> list[DomainStrength]:
        """
        Get all relationships for a user.

        Args:
            me_email: User's email
            min_strength: Minimum strength threshold

        Returns:
            List of DomainStrength objects
        """
        me_email_hash = self._hash_email(me_email)

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT target_domain, intro_count, reply_count, total_messages,
                       last_contact_at, first_contact_at
                FROM domain_relationships
                WHERE me_email_hash = ?
                ORDER BY last_contact_at DESC
                """,
                (me_email_hash,)
            )
            rows = await cursor.fetchall()

        relationships = []
        for row in rows:
            target_domain = row[0]
            intro_count = row[1]
            reply_count = row[2]
            total_messages = row[3]
            last_contact_at = datetime.fromisoformat(row[4])
            first_contact_at = datetime.fromisoformat(row[5])

            reply_rate = reply_count / total_messages if total_messages > 0 else 0.0
            recency_score = self._calculate_recency_score(last_contact_at)
            strength_score = self._calculate_strength_score(
                intro_count, reply_rate, recency_score
            )

            if strength_score >= min_strength:
                relationships.append(DomainStrength(
                    target_domain=target_domain,
                    intro_count=intro_count,
                    reply_count=reply_count,
                    total_messages=total_messages,
                    reply_rate=reply_rate,
                    recency_score=recency_score,
                    strength_score=strength_score,
                    last_contact_at=last_contact_at,
                    first_contact_at=first_contact_at,
                ))

        return relationships
