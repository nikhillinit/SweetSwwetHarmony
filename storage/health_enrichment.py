"""
Health Enrichment Storage Layer for Digital Health Intelligence.

Provides persistent SQLite storage for health enrichment data from:
- ClinicalTrials.gov (clinical trial records)
- OpenFDA (FDA clearances and approvals)
- PubMed (scientific publications)

Tables:
  - health_clinical_trials: Clinical trial records linked to entities
  - health_fda_clearances: FDA device clearances and approvals
  - health_publications: Scientific publications from PubMed

Usage:
    store = HealthEnrichmentStore()
    await store.initialize()

    # Save a clinical trial
    trial = ClinicalTrial(
        entity_id="entity-123",
        nct_id="NCT12345678",
        title="Phase 2 Study of Drug X",
        phase="Phase 2",
        status="Recruiting",
        conditions=["Diabetes", "Obesity"]
    )
    trial_id = await store.save_clinical_trial(trial)

    # Get trials for an entity
    trials = await store.get_trials_for_entity("entity-123")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

import aiosqlite

from utils.db_path_helper import resolve_db_path_env

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class ClinicalTrial:
    """
    Clinical trial record from ClinicalTrials.gov.

    Represents a clinical trial linked to a health entity.
    """

    entity_id: str
    nct_id: str
    title: str
    phase: Optional[str] = None
    status: Optional[str] = None
    enrollment: Optional[int] = None
    conditions: List[str] = field(default_factory=list)
    start_date: Optional[date] = None
    completion_date: Optional[date] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class FDAClearance:
    """
    FDA clearance/approval record from OpenFDA.

    Represents an FDA device clearance or approval linked to a health entity.
    """

    entity_id: str
    application_number: str
    device_name: str
    device_class: Optional[str] = None  # I, II, III
    clearance_type: Optional[str] = None  # 510k, PMA, de_novo
    decision: Optional[str] = None
    decision_date: Optional[date] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class Publication:
    """
    Scientific publication record from PubMed.

    Represents a publication linked to a health entity.
    """

    entity_id: str
    pmid: str
    title: str
    authors: Optional[str] = None
    journal: Optional[str] = None
    pub_date: Optional[date] = None
    citation_count: Optional[int] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


# =============================================================================
# HEALTH ENRICHMENT STORE
# =============================================================================


class HealthEnrichmentStore:
    """
    Async SQLite storage for health enrichment data.

    Features:
    - Async access via aiosqlite
    - Tables for clinical trials, FDA clearances, and publications
    - Indexed by entity_id for efficient lookups
    """

    def __init__(self, db_path: str | None = None):
        """
        Initialize health enrichment store.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for testing.
        """
        self.db_path = resolve_db_path_env(db_path)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """
        Initialize database connection and create tables.
        Should be called once at startup.
        """
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables(self._db)
        await self._db.commit()
        logger.info(f"HealthEnrichmentStore initialized at {self.db_path}")

    async def _create_tables(self, db: aiosqlite.Connection) -> None:
        """
        Create database tables and indexes.

        Args:
            db: Database connection.
        """
        # Clinical trials table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS health_clinical_trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                nct_id TEXT NOT NULL,
                title TEXT NOT NULL,
                phase TEXT,
                status TEXT,
                enrollment INTEGER,
                conditions TEXT,
                start_date TEXT,
                completion_date TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_trials_entity
            ON health_clinical_trials(entity_id)
        """)

        # FDA clearances table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS health_fda_clearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                application_number TEXT NOT NULL,
                device_name TEXT NOT NULL,
                device_class TEXT,
                clearance_type TEXT,
                decision TEXT,
                decision_date TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_fda_entity
            ON health_fda_clearances(entity_id)
        """)

        # Publications table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS health_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                pmid TEXT NOT NULL,
                title TEXT NOT NULL,
                authors TEXT,
                journal TEXT,
                pub_date TEXT,
                citation_count INTEGER,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_pubs_entity
            ON health_publications(entity_id)
        """)

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # =========================================================================
    # CLINICAL TRIAL OPERATIONS
    # =========================================================================

    async def save_clinical_trial(self, trial: ClinicalTrial) -> int:
        """
        Save a clinical trial record.

        Args:
            trial: ClinicalTrial to save.

        Returns:
            Database ID of the saved trial.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        conditions_json = json.dumps(trial.conditions) if trial.conditions else "[]"

        cursor = await self._db.execute(
            """
            INSERT INTO health_clinical_trials (
                entity_id, nct_id, title, phase, status, enrollment,
                conditions, start_date, completion_date, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trial.entity_id,
                trial.nct_id,
                trial.title,
                trial.phase,
                trial.status,
                trial.enrollment,
                conditions_json,
                trial.start_date.isoformat() if trial.start_date else None,
                trial.completion_date.isoformat() if trial.completion_date else None,
                trial.fetched_at.isoformat() if trial.fetched_at else None,
            ),
        )
        await self._db.commit()

        logger.debug(f"Saved clinical trial {trial.nct_id} for entity {trial.entity_id}")
        return cursor.lastrowid

    async def get_trials_for_entity(self, entity_id: str) -> List[ClinicalTrial]:
        """
        Get all clinical trials for an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of ClinicalTrial records.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, nct_id, title, phase, status, enrollment,
                   conditions, start_date, completion_date, fetched_at
            FROM health_clinical_trials
            WHERE entity_id = ?
            ORDER BY start_date DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_trial(row) for row in rows]

    def _row_to_trial(self, row) -> ClinicalTrial:
        """Convert database row to ClinicalTrial."""
        conditions = json.loads(row[7]) if row[7] else []

        return ClinicalTrial(
            id=row[0],
            entity_id=row[1],
            nct_id=row[2],
            title=row[3],
            phase=row[4],
            status=row[5],
            enrollment=row[6],
            conditions=conditions,
            start_date=date.fromisoformat(row[8]) if row[8] else None,
            completion_date=date.fromisoformat(row[9]) if row[9] else None,
            fetched_at=datetime.fromisoformat(row[10]) if row[10] else None,
        )

    # =========================================================================
    # FDA CLEARANCE OPERATIONS
    # =========================================================================

    async def save_fda_clearance(self, clearance: FDAClearance) -> int:
        """
        Save an FDA clearance record.

        Args:
            clearance: FDAClearance to save.

        Returns:
            Database ID of the saved clearance.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            INSERT INTO health_fda_clearances (
                entity_id, application_number, device_name, device_class,
                clearance_type, decision, decision_date, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clearance.entity_id,
                clearance.application_number,
                clearance.device_name,
                clearance.device_class,
                clearance.clearance_type,
                clearance.decision,
                clearance.decision_date.isoformat() if clearance.decision_date else None,
                clearance.fetched_at.isoformat() if clearance.fetched_at else None,
            ),
        )
        await self._db.commit()

        logger.debug(
            f"Saved FDA clearance {clearance.application_number} for entity {clearance.entity_id}"
        )
        return cursor.lastrowid

    async def get_fda_clearances_for_entity(self, entity_id: str) -> List[FDAClearance]:
        """
        Get all FDA clearances for an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of FDAClearance records.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, application_number, device_name, device_class,
                   clearance_type, decision, decision_date, fetched_at
            FROM health_fda_clearances
            WHERE entity_id = ?
            ORDER BY decision_date DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_clearance(row) for row in rows]

    def _row_to_clearance(self, row) -> FDAClearance:
        """Convert database row to FDAClearance."""
        return FDAClearance(
            id=row[0],
            entity_id=row[1],
            application_number=row[2],
            device_name=row[3],
            device_class=row[4],
            clearance_type=row[5],
            decision=row[6],
            decision_date=date.fromisoformat(row[7]) if row[7] else None,
            fetched_at=datetime.fromisoformat(row[8]) if row[8] else None,
        )

    # =========================================================================
    # PUBLICATION OPERATIONS
    # =========================================================================

    async def save_publication(self, pub: Publication) -> int:
        """
        Save a publication record.

        Args:
            pub: Publication to save.

        Returns:
            Database ID of the saved publication.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            INSERT INTO health_publications (
                entity_id, pmid, title, authors, journal,
                pub_date, citation_count, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pub.entity_id,
                pub.pmid,
                pub.title,
                pub.authors,
                pub.journal,
                pub.pub_date.isoformat() if pub.pub_date else None,
                pub.citation_count,
                pub.fetched_at.isoformat() if pub.fetched_at else None,
            ),
        )
        await self._db.commit()

        logger.debug(f"Saved publication {pub.pmid} for entity {pub.entity_id}")
        return cursor.lastrowid

    async def get_publications_for_entity(self, entity_id: str) -> List[Publication]:
        """
        Get all publications for an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            List of Publication records.
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, pmid, title, authors, journal,
                   pub_date, citation_count, fetched_at
            FROM health_publications
            WHERE entity_id = ?
            ORDER BY pub_date DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_publication(row) for row in rows]

    def _row_to_publication(self, row) -> Publication:
        """Convert database row to Publication."""
        return Publication(
            id=row[0],
            entity_id=row[1],
            pmid=row[2],
            title=row[3],
            authors=row[4],
            journal=row[5],
            pub_date=date.fromisoformat(row[6]) if row[6] else None,
            citation_count=row[7],
            fetched_at=datetime.fromisoformat(row[8]) if row[8] else None,
        )
