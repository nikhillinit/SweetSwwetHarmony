"""
Gold Set Manager

Manages the gold set for evaluation:
- CRUD operations for gold set companies and labels
- Import/export from JSON/CSV
- Annotation workflow helpers

Sprint 6: Evaluation & Calibration.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class GoldSetCompany:
    """A company in the gold set."""
    canonical_key: str
    company_name: str
    category: str  # core_sector, long_tail, ambiguous, hard_negative
    id: Optional[int] = None
    annotator_1: Optional[str] = None
    annotator_2: Optional[str] = None
    tie_breaker: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class GoldSetLabel:
    """A label for a gold set company."""
    company_id: int
    predicate: str  # problem, customer, sector, stage, geo
    label_type: str  # exact, partial, incorrect, abstain
    annotator: str
    id: Optional[int] = None
    gold_value: Optional[str] = None
    confidence: str = "high"  # high, medium, low
    notes: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class GoldSetInvestorLabel:
    """An investor relevance label for a gold set company."""
    company_id: int
    investor_id: str
    relevance: str  # relevant, partial, irrelevant
    annotator: str
    id: Optional[int] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class GoldSetStats:
    """Statistics about the gold set."""
    total_companies: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    total_labels: int = 0
    by_predicate: Dict[str, int] = field(default_factory=dict)
    by_label_type: Dict[str, int] = field(default_factory=dict)
    total_investor_labels: int = 0
    annotators: List[str] = field(default_factory=list)


# =============================================================================
# GOLD SET MANAGER
# =============================================================================

class GoldSetManager:
    """
    Manages the gold set for evaluation.

    Gold set categories:
    - core_sector: Clear thesis fit (CPG, health tech, travel)
    - long_tail: Edge cases, niche sectors
    - ambiguous: Borderline thesis fit
    - hard_negative: Clear non-fit (B2B, crypto, enterprise)
    """

    VALID_CATEGORIES = ["core_sector", "long_tail", "ambiguous", "hard_negative"]
    VALID_PREDICATES = ["problem", "customer", "sector", "stage", "geo", "business_model"]
    VALID_LABEL_TYPES = ["exact", "partial", "incorrect", "abstain"]
    VALID_RELEVANCE = ["relevant", "partial", "irrelevant"]

    def __init__(self, store: "SignalStore"):
        """
        Initialize with SignalStore instance.

        Args:
            store: SignalStore for database access
        """
        self._store = store

    # =========================================================================
    # COMPANY CRUD
    # =========================================================================

    async def add_company(
        self,
        canonical_key: str,
        company_name: str,
        category: str,
        annotator_1: Optional[str] = None,
        annotator_2: Optional[str] = None,
    ) -> int:
        """
        Add a company to the gold set.

        Args:
            canonical_key: Company's canonical key
            company_name: Company name
            category: One of VALID_CATEGORIES
            annotator_1: First annotator
            annotator_2: Second annotator

        Returns:
            The company ID
        """
        if category not in self.VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Must be one of {self.VALID_CATEGORIES}")

        if not self._store._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._store._db.execute(
            """
            INSERT INTO gold_set_companies (
                canonical_key, company_name, category, annotator_1, annotator_2, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                company_name = excluded.company_name,
                category = excluded.category,
                annotator_1 = excluded.annotator_1,
                annotator_2 = excluded.annotator_2
            """,
            (canonical_key, company_name, category, annotator_1, annotator_2, now),
        )
        await self._store._db.commit()
        return cursor.lastrowid or 0

    async def get_company(self, canonical_key: str) -> Optional[GoldSetCompany]:
        """Get a gold set company by canonical key."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._store._db.execute(
            """
            SELECT id, canonical_key, company_name, category, annotator_1, annotator_2, tie_breaker, created_at
            FROM gold_set_companies
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return GoldSetCompany(
            id=row[0],
            canonical_key=row[1],
            company_name=row[2],
            category=row[3],
            annotator_1=row[4],
            annotator_2=row[5],
            tie_breaker=row[6],
            created_at=row[7],
        )

    async def get_company_by_id(self, company_id: int) -> Optional[GoldSetCompany]:
        """Get a gold set company by ID."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._store._db.execute(
            """
            SELECT id, canonical_key, company_name, category, annotator_1, annotator_2, tie_breaker, created_at
            FROM gold_set_companies
            WHERE id = ?
            """,
            (company_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return GoldSetCompany(
            id=row[0],
            canonical_key=row[1],
            company_name=row[2],
            category=row[3],
            annotator_1=row[4],
            annotator_2=row[5],
            tie_breaker=row[6],
            created_at=row[7],
        )

    async def list_companies(
        self,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> List[GoldSetCompany]:
        """
        List gold set companies.

        Args:
            category: Filter by category
            limit: Maximum number to return

        Returns:
            List of GoldSetCompany objects
        """
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        if category:
            cursor = await self._store._db.execute(
                """
                SELECT id, canonical_key, company_name, category, annotator_1, annotator_2, tie_breaker, created_at
                FROM gold_set_companies
                WHERE category = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (category, limit),
            )
        else:
            cursor = await self._store._db.execute(
                """
                SELECT id, canonical_key, company_name, category, annotator_1, annotator_2, tie_breaker, created_at
                FROM gold_set_companies
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        rows = await cursor.fetchall()

        return [
            GoldSetCompany(
                id=row[0],
                canonical_key=row[1],
                company_name=row[2],
                category=row[3],
                annotator_1=row[4],
                annotator_2=row[5],
                tie_breaker=row[6],
                created_at=row[7],
            )
            for row in rows
        ]

    async def delete_company(self, canonical_key: str) -> bool:
        """Delete a company from the gold set (cascades to labels)."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._store._db.execute(
            "DELETE FROM gold_set_companies WHERE canonical_key = ?",
            (canonical_key,),
        )
        await self._store._db.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # LABEL CRUD
    # =========================================================================

    async def add_label(
        self,
        company_id: int,
        predicate: str,
        label_type: str,
        annotator: str,
        gold_value: Optional[str] = None,
        confidence: str = "high",
        notes: Optional[str] = None,
    ) -> int:
        """
        Add a label to a gold set company.

        Args:
            company_id: Gold set company ID
            predicate: One of VALID_PREDICATES
            label_type: One of VALID_LABEL_TYPES
            annotator: Annotator identifier
            gold_value: Ground truth value
            confidence: high, medium, or low
            notes: Optional notes

        Returns:
            The label ID
        """
        if predicate not in self.VALID_PREDICATES:
            raise ValueError(f"Invalid predicate: {predicate}. Must be one of {self.VALID_PREDICATES}")
        if label_type not in self.VALID_LABEL_TYPES:
            raise ValueError(f"Invalid label_type: {label_type}. Must be one of {self.VALID_LABEL_TYPES}")

        if not self._store._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._store._db.execute(
            """
            INSERT INTO gold_set_labels (
                company_id, predicate, label_type, gold_value, annotator, confidence, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, predicate, annotator) DO UPDATE SET
                label_type = excluded.label_type,
                gold_value = excluded.gold_value,
                confidence = excluded.confidence,
                notes = excluded.notes
            """,
            (company_id, predicate, label_type, gold_value, annotator, confidence, notes, now),
        )
        await self._store._db.commit()
        return cursor.lastrowid or 0

    async def get_labels(
        self,
        company_id: int,
        predicate: Optional[str] = None,
    ) -> List[GoldSetLabel]:
        """Get labels for a company."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        if predicate:
            cursor = await self._store._db.execute(
                """
                SELECT id, company_id, predicate, label_type, gold_value, annotator, confidence, notes, created_at
                FROM gold_set_labels
                WHERE company_id = ? AND predicate = ?
                """,
                (company_id, predicate),
            )
        else:
            cursor = await self._store._db.execute(
                """
                SELECT id, company_id, predicate, label_type, gold_value, annotator, confidence, notes, created_at
                FROM gold_set_labels
                WHERE company_id = ?
                """,
                (company_id,),
            )

        rows = await cursor.fetchall()

        return [
            GoldSetLabel(
                id=row[0],
                company_id=row[1],
                predicate=row[2],
                label_type=row[3],
                gold_value=row[4],
                annotator=row[5],
                confidence=row[6],
                notes=row[7],
                created_at=row[8],
            )
            for row in rows
        ]

    # =========================================================================
    # INVESTOR LABEL CRUD
    # =========================================================================

    async def add_investor_label(
        self,
        company_id: int,
        investor_id: str,
        relevance: str,
        annotator: str,
        notes: Optional[str] = None,
    ) -> int:
        """
        Add an investor relevance label.

        Args:
            company_id: Gold set company ID
            investor_id: Investor ID
            relevance: One of VALID_RELEVANCE
            annotator: Annotator identifier
            notes: Optional notes

        Returns:
            The label ID
        """
        if relevance not in self.VALID_RELEVANCE:
            raise ValueError(f"Invalid relevance: {relevance}. Must be one of {self.VALID_RELEVANCE}")

        if not self._store._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._store._db.execute(
            """
            INSERT INTO gold_set_investor_labels (
                company_id, investor_id, relevance, annotator, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, investor_id, annotator) DO UPDATE SET
                relevance = excluded.relevance,
                notes = excluded.notes
            """,
            (company_id, investor_id, relevance, annotator, notes, now),
        )
        await self._store._db.commit()
        return cursor.lastrowid or 0

    async def get_investor_labels(self, company_id: int) -> List[GoldSetInvestorLabel]:
        """Get investor labels for a company."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._store._db.execute(
            """
            SELECT id, company_id, investor_id, relevance, annotator, notes, created_at
            FROM gold_set_investor_labels
            WHERE company_id = ?
            """,
            (company_id,),
        )
        rows = await cursor.fetchall()

        return [
            GoldSetInvestorLabel(
                id=row[0],
                company_id=row[1],
                investor_id=row[2],
                relevance=row[3],
                annotator=row[4],
                notes=row[5],
                created_at=row[6],
            )
            for row in rows
        ]

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_stats(self) -> GoldSetStats:
        """Get statistics about the gold set."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        stats = GoldSetStats()

        # Total companies
        cursor = await self._store._db.execute("SELECT COUNT(*) FROM gold_set_companies")
        row = await cursor.fetchone()
        stats.total_companies = row[0] if row else 0

        # By category
        cursor = await self._store._db.execute(
            "SELECT category, COUNT(*) FROM gold_set_companies GROUP BY category"
        )
        rows = await cursor.fetchall()
        stats.by_category = {row[0]: row[1] for row in rows}

        # Total labels
        cursor = await self._store._db.execute("SELECT COUNT(*) FROM gold_set_labels")
        row = await cursor.fetchone()
        stats.total_labels = row[0] if row else 0

        # By predicate
        cursor = await self._store._db.execute(
            "SELECT predicate, COUNT(*) FROM gold_set_labels GROUP BY predicate"
        )
        rows = await cursor.fetchall()
        stats.by_predicate = {row[0]: row[1] for row in rows}

        # By label type
        cursor = await self._store._db.execute(
            "SELECT label_type, COUNT(*) FROM gold_set_labels GROUP BY label_type"
        )
        rows = await cursor.fetchall()
        stats.by_label_type = {row[0]: row[1] for row in rows}

        # Total investor labels
        cursor = await self._store._db.execute("SELECT COUNT(*) FROM gold_set_investor_labels")
        row = await cursor.fetchone()
        stats.total_investor_labels = row[0] if row else 0

        # Annotators
        cursor = await self._store._db.execute(
            """
            SELECT DISTINCT annotator FROM (
                SELECT annotator_1 as annotator FROM gold_set_companies WHERE annotator_1 IS NOT NULL
                UNION
                SELECT annotator_2 as annotator FROM gold_set_companies WHERE annotator_2 IS NOT NULL
                UNION
                SELECT annotator FROM gold_set_labels
                UNION
                SELECT annotator FROM gold_set_investor_labels
            )
            """
        )
        rows = await cursor.fetchall()
        stats.annotators = [row[0] for row in rows if row[0]]

        return stats

    # =========================================================================
    # IMPORT/EXPORT
    # =========================================================================

    async def export_to_json(self, path: Path) -> int:
        """
        Export gold set to JSON file.

        Args:
            path: Output file path

        Returns:
            Number of companies exported
        """
        companies = await self.list_companies(limit=10000)

        export_data = {
            "version": datetime.now(timezone.utc).strftime("%Y%m%d"),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "companies": [],
        }

        for company in companies:
            labels = await self.get_labels(company.id)
            investor_labels = await self.get_investor_labels(company.id)

            company_data = company.to_dict()
            company_data["labels"] = [l.to_dict() for l in labels]
            company_data["investor_labels"] = [l.to_dict() for l in investor_labels]
            export_data["companies"].append(company_data)

        with open(path, "w") as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"Exported {len(companies)} companies to {path}")
        return len(companies)

    async def import_from_json(self, path: Path) -> int:
        """
        Import gold set from JSON file.

        Args:
            path: Input file path

        Returns:
            Number of companies imported
        """
        with open(path) as f:
            data = json.load(f)

        count = 0
        for company_data in data.get("companies", []):
            # Add company
            company_id = await self.add_company(
                canonical_key=company_data["canonical_key"],
                company_name=company_data["company_name"],
                category=company_data["category"],
                annotator_1=company_data.get("annotator_1"),
                annotator_2=company_data.get("annotator_2"),
            )

            # Get actual company ID (may have been updated)
            company = await self.get_company(company_data["canonical_key"])
            if not company:
                continue

            # Add labels
            for label_data in company_data.get("labels", []):
                await self.add_label(
                    company_id=company.id,
                    predicate=label_data["predicate"],
                    label_type=label_data["label_type"],
                    annotator=label_data["annotator"],
                    gold_value=label_data.get("gold_value"),
                    confidence=label_data.get("confidence", "high"),
                    notes=label_data.get("notes"),
                )

            # Add investor labels
            for inv_label in company_data.get("investor_labels", []):
                await self.add_investor_label(
                    company_id=company.id,
                    investor_id=inv_label["investor_id"],
                    relevance=inv_label["relevance"],
                    annotator=inv_label["annotator"],
                    notes=inv_label.get("notes"),
                )

            count += 1

        logger.info(f"Imported {count} companies from {path}")
        return count

    async def export_to_csv(self, path: Path) -> int:
        """
        Export gold set companies to CSV (labels in separate file).

        Args:
            path: Output file path for companies

        Returns:
            Number of companies exported
        """
        companies = await self.list_companies(limit=10000)

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "canonical_key", "company_name", "category",
                "annotator_1", "annotator_2", "tie_breaker"
            ])
            for company in companies:
                writer.writerow([
                    company.canonical_key,
                    company.company_name,
                    company.category,
                    company.annotator_1 or "",
                    company.annotator_2 or "",
                    company.tie_breaker or "",
                ])

        # Export labels to separate file
        labels_path = path.with_suffix(".labels.csv")
        with open(labels_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "canonical_key", "predicate", "label_type",
                "gold_value", "annotator", "confidence", "notes"
            ])
            for company in companies:
                labels = await self.get_labels(company.id)
                for label in labels:
                    writer.writerow([
                        company.canonical_key,
                        label.predicate,
                        label.label_type,
                        label.gold_value or "",
                        label.annotator,
                        label.confidence,
                        label.notes or "",
                    ])

        logger.info(f"Exported {len(companies)} companies to {path}")
        return len(companies)

    async def import_from_csv(self, path: Path) -> int:
        """
        Import gold set companies from CSV.

        Args:
            path: Input file path

        Returns:
            Number of companies imported
        """
        count = 0

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                await self.add_company(
                    canonical_key=row["canonical_key"],
                    company_name=row["company_name"],
                    category=row["category"],
                    annotator_1=row.get("annotator_1") or None,
                    annotator_2=row.get("annotator_2") or None,
                )
                count += 1

        # Import labels if file exists
        labels_path = path.with_suffix(".labels.csv")
        if labels_path.exists():
            with open(labels_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    company = await self.get_company(row["canonical_key"])
                    if company:
                        await self.add_label(
                            company_id=company.id,
                            predicate=row["predicate"],
                            label_type=row["label_type"],
                            annotator=row["annotator"],
                            gold_value=row.get("gold_value") or None,
                            confidence=row.get("confidence", "high"),
                            notes=row.get("notes") or None,
                        )

        logger.info(f"Imported {count} companies from {path}")
        return count
