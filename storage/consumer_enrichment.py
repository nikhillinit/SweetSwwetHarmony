"""
Consumer Enrichment Storage Layer for Consumer Intelligence.

Provides persistent SQLite storage for consumer enrichment data from:
- Brand sentiment analysis
- Community/marketplace metrics
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class BrandSentimentRecord:
    """Brand sentiment storage record."""
    entity_id: str
    brand_name: str
    overall_sentiment: float
    mention_count: int
    positive_ratio: float
    negative_ratio: Optional[float] = None
    fetched_at: Optional[datetime] = None


@dataclass
class CommunityMetricsRecord:
    """Community metrics storage record."""
    entity_id: str
    platform_name: str
    total_users: int
    active_users: int
    growth_rate: float
    engagement_rate: Optional[float] = None
    fetched_at: Optional[datetime] = None


class ConsumerEnrichmentStore:
    """Storage for consumer enrichment data."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database tables."""
        self._db = await aiosqlite.connect(self.db_path)
        logger.debug(f"ConsumerEnrichmentStore connected to {self.db_path}")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS consumer_brand_sentiment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                brand_name TEXT NOT NULL,
                overall_sentiment REAL,
                mention_count INTEGER,
                positive_ratio REAL,
                negative_ratio REAL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS consumer_community_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                platform_name TEXT NOT NULL,
                total_users INTEGER,
                active_users INTEGER,
                growth_rate REAL,
                engagement_rate REAL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._db.commit()
        logger.debug("Consumer enrichment tables initialized")

    async def save_brand_sentiment(self, record: BrandSentimentRecord) -> None:
        """Save brand sentiment record."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        await self._db.execute(
            """INSERT INTO consumer_brand_sentiment
               (entity_id, brand_name, overall_sentiment, mention_count,
                positive_ratio, negative_ratio)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (record.entity_id, record.brand_name, record.overall_sentiment,
             record.mention_count, record.positive_ratio, record.negative_ratio)
        )
        await self._db.commit()
        logger.debug(f"Saved brand sentiment for {record.entity_id}")

    async def get_brand_sentiment_for_entity(
        self,
        entity_id: str
    ) -> List[BrandSentimentRecord]:
        """Get brand sentiment records for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT * FROM consumer_brand_sentiment WHERE entity_id = ?",
            (entity_id,)
        )
        rows = await cursor.fetchall()

        return [
            BrandSentimentRecord(
                entity_id=row[1],
                brand_name=row[2],
                overall_sentiment=row[3],
                mention_count=row[4],
                positive_ratio=row[5],
                negative_ratio=row[6]
            )
            for row in rows
        ]

    async def save_community_metrics(self, record: CommunityMetricsRecord) -> None:
        """Save community metrics record."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        await self._db.execute(
            """INSERT INTO consumer_community_metrics
               (entity_id, platform_name, total_users, active_users,
                growth_rate, engagement_rate)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (record.entity_id, record.platform_name, record.total_users,
             record.active_users, record.growth_rate, record.engagement_rate)
        )
        await self._db.commit()
        logger.debug(f"Saved community metrics for {record.entity_id}")

    async def get_community_metrics_for_entity(
        self,
        entity_id: str
    ) -> List[CommunityMetricsRecord]:
        """Get community metrics for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT * FROM consumer_community_metrics WHERE entity_id = ?",
            (entity_id,)
        )
        rows = await cursor.fetchall()

        return [
            CommunityMetricsRecord(
                entity_id=row[1],
                platform_name=row[2],
                total_users=row[3],
                active_users=row[4],
                growth_rate=row[5],
                engagement_rate=row[6]
            )
            for row in rows
        ]

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.debug("ConsumerEnrichmentStore connection closed")
