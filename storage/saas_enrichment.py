"""
SaaS Enrichment Storage Layer for B2B SaaS Intelligence.

Provides persistent SQLite storage for SaaS enrichment data from:
- G2Crowd (product reviews and ratings)
- Capterra (product reviews and ratings)
- Tech stack detection (technologies and hosting)

Tables:
  - saas_g2_reviews: G2Crowd review data linked to entities
  - saas_capterra_reviews: Capterra review data linked to entities
  - saas_tech_stacks: Technology stack data linked to entities

Usage:
    store = SaaSEnrichmentStore("signals.db")
    await store.initialize()

    # Save a G2 review
    review = G2Review(
        entity_id="entity-123",
        product_name="Acme CRM",
        rating=4.5,
        review_count=100,
        category="CRM"
    )
    await store.save_g2_review(review)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class G2Review:
    """G2 review record linked to an entity."""

    entity_id: str
    product_name: str
    rating: float
    review_count: int
    category: str
    vendor: Optional[str] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class CapterraReview:
    """Capterra review record linked to an entity."""

    entity_id: str
    product_name: str
    overall_rating: float
    review_count: int
    category: str
    ease_of_use_rating: Optional[float] = None
    value_for_money_rating: Optional[float] = None
    vendor: Optional[str] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class TechStackRecord:
    """Tech stack record linked to an entity."""

    entity_id: str
    domain: str
    technologies: List[str]
    hosting: Optional[str] = None
    analytics: Optional[List[str]] = None
    cdn: Optional[str] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


# =============================================================================
# SAAS ENRICHMENT STORE
# =============================================================================


class SaaSEnrichmentStore:
    """
    Async SQLite storage for SaaS enrichment data.

    Features:
    - Async access via aiosqlite
    - Tables for G2, Capterra, and tech stack data
    - Indexed by entity_id for efficient lookups
    """

    def __init__(self, db_path: str = "signals.db"):
        """
        Initialize SaaS enrichment store.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for testing.
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """
        Initialize database connection and create tables.
        Should be called once at startup.
        """
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._db.commit()
        logger.info(f"SaaSEnrichmentStore initialized at {self.db_path}")

    async def _create_tables(self) -> None:
        """Create database tables and indexes."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # G2 reviews table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS saas_g2_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                rating REAL,
                review_count INTEGER,
                category TEXT,
                vendor TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_g2_entity
            ON saas_g2_reviews(entity_id)
        """)

        # Capterra reviews table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS saas_capterra_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                overall_rating REAL,
                review_count INTEGER,
                category TEXT,
                ease_of_use_rating REAL,
                value_for_money_rating REAL,
                vendor TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_capterra_entity
            ON saas_capterra_reviews(entity_id)
        """)

        # Tech stacks table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS saas_tech_stacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                technologies TEXT,
                hosting TEXT,
                analytics TEXT,
                cdn TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tech_entity
            ON saas_tech_stacks(entity_id)
        """)

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # =========================================================================
    # G2 REVIEW OPERATIONS
    # =========================================================================

    async def save_g2_review(self, review: G2Review) -> int:
        """
        Save a G2 review record.

        Args:
            review: G2Review to save.

        Returns:
            Database ID of the saved review.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            INSERT INTO saas_g2_reviews (
                entity_id, product_name, rating, review_count,
                category, vendor, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.entity_id,
                review.product_name,
                review.rating,
                review.review_count,
                review.category,
                review.vendor,
                review.fetched_at.isoformat() if review.fetched_at else None,
            ),
        )
        await self._db.commit()

        logger.debug(f"Saved G2 review for {review.entity_id}: {review.product_name}")
        return cursor.lastrowid

    async def get_g2_reviews_for_entity(self, entity_id: str) -> List[G2Review]:
        """
        Get all G2 reviews for an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of G2Review records.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, product_name, rating, review_count,
                   category, vendor, fetched_at
            FROM saas_g2_reviews
            WHERE entity_id = ?
            ORDER BY rating DESC
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_g2_review(row) for row in rows]

    def _row_to_g2_review(self, row) -> G2Review:
        """Convert database row to G2Review."""
        return G2Review(
            id=row[0],
            entity_id=row[1],
            product_name=row[2],
            rating=row[3],
            review_count=row[4],
            category=row[5],
            vendor=row[6],
            fetched_at=datetime.fromisoformat(row[7]) if row[7] else None,
        )

    # =========================================================================
    # CAPTERRA REVIEW OPERATIONS
    # =========================================================================

    async def save_capterra_review(self, review: CapterraReview) -> int:
        """
        Save a Capterra review record.

        Args:
            review: CapterraReview to save.

        Returns:
            Database ID of the saved review.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            INSERT INTO saas_capterra_reviews (
                entity_id, product_name, overall_rating, review_count,
                category, ease_of_use_rating, value_for_money_rating,
                vendor, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.entity_id,
                review.product_name,
                review.overall_rating,
                review.review_count,
                review.category,
                review.ease_of_use_rating,
                review.value_for_money_rating,
                review.vendor,
                review.fetched_at.isoformat() if review.fetched_at else None,
            ),
        )
        await self._db.commit()

        logger.debug(f"Saved Capterra review for {review.entity_id}: {review.product_name}")
        return cursor.lastrowid

    async def get_capterra_reviews_for_entity(self, entity_id: str) -> List[CapterraReview]:
        """
        Get all Capterra reviews for an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of CapterraReview records.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, product_name, overall_rating, review_count,
                   category, ease_of_use_rating, value_for_money_rating,
                   vendor, fetched_at
            FROM saas_capterra_reviews
            WHERE entity_id = ?
            ORDER BY overall_rating DESC
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_capterra_review(row) for row in rows]

    def _row_to_capterra_review(self, row) -> CapterraReview:
        """Convert database row to CapterraReview."""
        return CapterraReview(
            id=row[0],
            entity_id=row[1],
            product_name=row[2],
            overall_rating=row[3],
            review_count=row[4],
            category=row[5],
            ease_of_use_rating=row[6],
            value_for_money_rating=row[7],
            vendor=row[8],
            fetched_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )

    # =========================================================================
    # TECH STACK OPERATIONS
    # =========================================================================

    async def save_tech_stack(self, record: TechStackRecord) -> int:
        """
        Save a tech stack record.

        Args:
            record: TechStackRecord to save.

        Returns:
            Database ID of the saved record.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            INSERT INTO saas_tech_stacks (
                entity_id, domain, technologies, hosting, analytics, cdn, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.entity_id,
                record.domain,
                json.dumps(record.technologies),
                record.hosting,
                json.dumps(record.analytics) if record.analytics else None,
                record.cdn,
                record.fetched_at.isoformat() if record.fetched_at else None,
            ),
        )
        await self._db.commit()

        logger.debug(f"Saved tech stack for {record.entity_id}: {record.domain}")
        return cursor.lastrowid

    async def get_tech_stack_for_entity(self, entity_id: str) -> List[TechStackRecord]:
        """
        Get all tech stacks for an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of TechStackRecord records.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, domain, technologies, hosting, analytics, cdn, fetched_at
            FROM saas_tech_stacks
            WHERE entity_id = ?
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_tech_stack(row) for row in rows]

    def _row_to_tech_stack(self, row) -> TechStackRecord:
        """Convert database row to TechStackRecord."""
        return TechStackRecord(
            id=row[0],
            entity_id=row[1],
            domain=row[2],
            technologies=json.loads(row[3]) if row[3] else [],
            hosting=row[4],
            analytics=json.loads(row[5]) if row[5] else None,
            cdn=row[6],
            fetched_at=datetime.fromisoformat(row[7]) if row[7] else None,
        )
