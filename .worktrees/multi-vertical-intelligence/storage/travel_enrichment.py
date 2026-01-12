"""
Travel Enrichment Storage Layer.

Provides persistent SQLite storage for travel enrichment data from:
- Yelp Fusion (reviews, ratings)
- Google Places (ratings, details)
- Travel Certifications (Forbes, AAA, Michelin)

Tables:
  - travel_yelp_reviews: Yelp business data linked to entities
  - travel_google_places: Google Places data linked to entities
  - travel_certifications: Certification records linked to entities
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import aiosqlite

from enrichment.yelp_fusion import YelpBusiness
from enrichment.google_places import GooglePlace
from enrichment.travel_certifications import TravelCertification, CertificationSource

logger = logging.getLogger(__name__)


@dataclass
class YelpReview:
    """Yelp review record from storage."""
    id: int
    entity_id: str
    yelp_id: str
    name: str
    rating: float
    review_count: int
    price: Optional[str]
    categories: List[str]
    url: str
    fetched_at: Optional[datetime]


@dataclass
class GooglePlaceRecord:
    """Google Place record from storage."""
    id: int
    entity_id: str
    place_id: str
    name: str
    rating: float
    user_ratings_total: int
    price_level: Optional[int]
    types: List[str]
    website: Optional[str]
    fetched_at: Optional[datetime]


@dataclass
class TravelCertificationRecord:
    """Travel certification record from storage."""
    id: int
    entity_id: str
    source: str
    rating: str
    year: int
    property_name: str
    fetched_at: Optional[datetime]


class TravelEnrichmentStore:
    """
    Async SQLite storage for travel enrichment data.
    """

    def __init__(self, db_path: str = "signals.db"):
        """
        Initialize travel enrichment store.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for testing.
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._db.commit()
        logger.info(f"TravelEnrichmentStore initialized at {self.db_path}")

    async def _create_tables(self) -> None:
        """Create database tables and indexes."""
        # Yelp reviews table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS travel_yelp_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                yelp_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rating REAL,
                review_count INTEGER,
                price TEXT,
                categories TEXT,
                url TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_yelp_entity
            ON travel_yelp_reviews(entity_id)
        """)

        # Google Places table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS travel_google_places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                place_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rating REAL,
                user_ratings_total INTEGER,
                price_level INTEGER,
                types TEXT,
                website TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_google_entity
            ON travel_google_places(entity_id)
        """)

        # Certifications table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS travel_certifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                source TEXT NOT NULL,
                rating TEXT NOT NULL,
                year INTEGER,
                property_name TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_cert_entity
            ON travel_certifications(entity_id)
        """)

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # Yelp operations
    async def save_yelp_review(self, business: YelpBusiness) -> int:
        """Save a Yelp business record."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        categories_json = json.dumps(business.categories)

        cursor = await self._db.execute(
            """
            INSERT INTO travel_yelp_reviews (
                entity_id, yelp_id, name, rating, review_count,
                price, categories, url, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                business.entity_id,
                business.yelp_id,
                business.name,
                business.rating,
                business.review_count,
                business.price,
                categories_json,
                business.url,
                business.fetched_at.isoformat() if business.fetched_at else None,
            ),
        )
        await self._db.commit()
        logger.debug(f"Saved Yelp review {business.yelp_id} for entity {business.entity_id}")
        return cursor.lastrowid

    async def get_yelp_reviews_for_entity(self, entity_id: str) -> List[YelpReview]:
        """Get all Yelp reviews for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, yelp_id, name, rating, review_count,
                   price, categories, url, fetched_at
            FROM travel_yelp_reviews
            WHERE entity_id = ?
            ORDER BY rating DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [
            YelpReview(
                id=row[0],
                entity_id=row[1],
                yelp_id=row[2],
                name=row[3],
                rating=row[4],
                review_count=row[5],
                price=row[6],
                categories=json.loads(row[7]) if row[7] else [],
                url=row[8],
                fetched_at=datetime.fromisoformat(row[9]) if row[9] else None,
            )
            for row in rows
        ]

    # Google Places operations
    async def save_google_place(self, place: GooglePlace) -> int:
        """Save a Google Place record."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        types_json = json.dumps(place.types)

        cursor = await self._db.execute(
            """
            INSERT INTO travel_google_places (
                entity_id, place_id, name, rating, user_ratings_total,
                price_level, types, website, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                place.entity_id,
                place.place_id,
                place.name,
                place.rating,
                place.user_ratings_total,
                place.price_level,
                types_json,
                place.website,
                place.fetched_at.isoformat() if place.fetched_at else None,
            ),
        )
        await self._db.commit()
        logger.debug(f"Saved Google place {place.place_id} for entity {place.entity_id}")
        return cursor.lastrowid

    async def get_google_places_for_entity(self, entity_id: str) -> List[GooglePlaceRecord]:
        """Get all Google Places for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, place_id, name, rating, user_ratings_total,
                   price_level, types, website, fetched_at
            FROM travel_google_places
            WHERE entity_id = ?
            ORDER BY rating DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [
            GooglePlaceRecord(
                id=row[0],
                entity_id=row[1],
                place_id=row[2],
                name=row[3],
                rating=row[4],
                user_ratings_total=row[5],
                price_level=row[6],
                types=json.loads(row[7]) if row[7] else [],
                website=row[8],
                fetched_at=datetime.fromisoformat(row[9]) if row[9] else None,
            )
            for row in rows
        ]

    # Certification operations
    async def save_certification(self, cert: TravelCertification) -> int:
        """Save a certification record."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            INSERT INTO travel_certifications (
                entity_id, source, rating, year, property_name, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cert.entity_id,
                cert.source.value,
                cert.rating,
                cert.year,
                cert.property_name,
                cert.fetched_at.isoformat() if cert.fetched_at else None,
            ),
        )
        await self._db.commit()
        logger.debug(f"Saved certification {cert.source.value} for entity {cert.entity_id}")
        return cursor.lastrowid

    async def get_certifications_for_entity(self, entity_id: str) -> List[TravelCertificationRecord]:
        """Get all certifications for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, source, rating, year, property_name, fetched_at
            FROM travel_certifications
            WHERE entity_id = ?
            ORDER BY year DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [
            TravelCertificationRecord(
                id=row[0],
                entity_id=row[1],
                source=row[2],
                rating=row[3],
                year=row[4],
                property_name=row[5],
                fetched_at=datetime.fromisoformat(row[6]) if row[6] else None,
            )
            for row in rows
        ]
