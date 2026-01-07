"""
EntityResolutionStore: Maps SourceAssets to Leads (companies).

This module implements the Two-Entity Model's resolution layer:
- SourceAsset: Raw data from a source (GitHub repo, PH launch, etc.)
- Lead: Company entity in CRM (Notion page)

Resolution strategies:
- DOMAIN_MATCH: Asset homepage → Lead website
- ORG_MATCH: Asset GitHub org → Lead GitHub org
- NAME_SIMILARITY: Fuzzy match on company name
- HEURISTIC: Algorithmic guess (lower confidence)
- MANUAL: Human-in-the-loop override (highest confidence)

The asset_to_lead table links assets to leads with confidence scores,
enabling multi-asset companies (e.g., 3 repos → 1 company).
"""
import aiosqlite
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class ResolutionMethod(str, Enum):
    """How the asset was resolved to a lead."""
    DOMAIN_MATCH = "domain_match"      # Homepage URL matched lead website
    ORG_MATCH = "org_match"            # GitHub org matched lead's org
    NAME_SIMILARITY = "name_similarity" # Fuzzy name match
    HEURISTIC = "heuristic"            # Algorithmic guess
    MANUAL = "manual"                  # Human override (highest priority)


@dataclass
class AssetToLead:
    """
    Link between a SourceAsset and a Lead.

    This represents the resolution of a raw asset to a company entity.
    Multiple assets can link to the same lead (multi-signal company).
    """
    asset_id: int  # ID from source_assets table
    asset_source_type: str  # github_repo, product_hunt, etc.
    asset_external_id: str  # External identifier from source
    lead_canonical_key: str  # Canonical key of the lead (e.g., domain:acme.com)
    confidence: float  # 0.0-1.0 confidence in this resolution
    resolved_by: ResolutionMethod  # How this was resolved
    id: Optional[int] = None  # Database ID (set after save)
    resolved_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Optional[Dict[str, Any]] = None  # Additional resolution info


class EntityResolutionStore:
    """
    SQLite-based storage for asset-to-lead mappings.

    Enables:
    - Multi-asset companies (GitHub + PH + job posting → 1 company)
    - Manual overrides for corrections
    - Confidence-based filtering
    - Resolution audit trail

    Usage:
        store = EntityResolutionStore("resolution.db")
        await store.initialize()

        link = AssetToLead(
            asset_id=1,
            asset_source_type="github_repo",
            asset_external_id="startup/app",
            lead_canonical_key="domain:startup.com",
            confidence=0.95,
            resolved_by=ResolutionMethod.DOMAIN_MATCH,
        )
        await store.create_link(link)

        lead_key = await store.get_lead_for_asset("github_repo", "startup/app")
    """

    def __init__(self, db_path: str):
        """
        Initialize EntityResolutionStore.

        Args:
            db_path: Path to SQLite database. Use ":memory:" for in-memory.
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        # Main linking table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS asset_to_lead (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                asset_source_type TEXT NOT NULL,
                asset_external_id TEXT NOT NULL,
                lead_canonical_key TEXT NOT NULL,
                confidence REAL NOT NULL,
                resolved_by TEXT NOT NULL,
                resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Index for looking up lead by asset
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_asset_to_lead_asset
            ON asset_to_lead(asset_source_type, asset_external_id, resolved_by DESC)
        """)

        # Index for looking up assets by lead
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_asset_to_lead_lead
            ON asset_to_lead(lead_canonical_key)
        """)

        # Asset registry (tracks which assets exist for unresolved queries)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS asset_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_asset_registry_lookup
            ON asset_registry(source_type, external_id)
        """)

        await self._db.commit()
        logger.info(f"EntityResolutionStore initialized at {self.db_path}")

    async def create_link(self, link: AssetToLead) -> int:
        """
        Create or update a link between asset and lead.

        If a link already exists for this asset with a lower-priority method,
        the new link replaces it. Manual links always take precedence.

        Args:
            link: AssetToLead to create.

        Returns:
            Database ID of the link.
        """
        import json

        # Check if a link already exists
        existing = await self._get_existing_link(
            link.asset_source_type,
            link.asset_external_id,
        )

        if existing:
            # Manual always wins, otherwise higher confidence wins
            should_replace = (
                link.resolved_by == ResolutionMethod.MANUAL
                or (
                    existing["resolved_by"] != ResolutionMethod.MANUAL.value
                    and link.confidence > existing["confidence"]
                )
            )

            if should_replace:
                await self._db.execute(
                    "DELETE FROM asset_to_lead WHERE id = ?",
                    (existing["id"],),
                )
            else:
                # Keep existing link
                return existing["id"]

        cursor = await self._db.execute(
            """INSERT INTO asset_to_lead
               (asset_id, asset_source_type, asset_external_id,
                lead_canonical_key, confidence, resolved_by, resolved_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                link.asset_id,
                link.asset_source_type,
                link.asset_external_id,
                link.lead_canonical_key,
                link.confidence,
                link.resolved_by.value,
                link.resolved_at.isoformat(),
                json.dumps(link.metadata) if link.metadata else None,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def _get_existing_link(
        self,
        source_type: str,
        external_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get existing link for an asset."""
        cursor = await self._db.execute(
            """SELECT id, confidence, resolved_by FROM asset_to_lead
               WHERE asset_source_type = ? AND asset_external_id = ?
               ORDER BY
                   CASE WHEN resolved_by = 'manual' THEN 0 ELSE 1 END,
                   confidence DESC
               LIMIT 1""",
            (source_type, external_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {"id": row[0], "confidence": row[1], "resolved_by": row[2]}

    async def get_link(self, link_id: int) -> Optional[AssetToLead]:
        """
        Retrieve a link by ID.

        Args:
            link_id: Database ID.

        Returns:
            AssetToLead if found, None otherwise.
        """
        cursor = await self._db.execute(
            "SELECT * FROM asset_to_lead WHERE id = ?",
            (link_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_link(row)

    async def get_lead_for_asset(
        self,
        source_type: str,
        external_id: str,
        min_confidence: float = 0.0,
    ) -> Optional[str]:
        """
        Get the lead canonical key for an asset.

        Prioritizes manual resolutions, then highest confidence.

        Args:
            source_type: Type of source (github_repo, etc.)
            external_id: Source-specific identifier.
            min_confidence: Minimum confidence threshold.

        Returns:
            Lead canonical key if resolved, None otherwise.
        """
        cursor = await self._db.execute(
            """SELECT lead_canonical_key FROM asset_to_lead
               WHERE asset_source_type = ?
                 AND asset_external_id = ?
                 AND confidence >= ?
               ORDER BY
                   CASE WHEN resolved_by = 'manual' THEN 0 ELSE 1 END,
                   confidence DESC
               LIMIT 1""",
            (source_type, external_id, min_confidence),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return row[0]

    async def get_assets_for_lead(
        self,
        lead_canonical_key: str,
    ) -> List[AssetToLead]:
        """
        Get all assets linked to a lead.

        Args:
            lead_canonical_key: Canonical key of the lead.

        Returns:
            List of AssetToLead links.
        """
        cursor = await self._db.execute(
            """SELECT * FROM asset_to_lead
               WHERE lead_canonical_key = ?
               ORDER BY confidence DESC""",
            (lead_canonical_key,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_link(row) for row in rows]

    async def register_asset(
        self,
        asset_id: int,
        source_type: str,
        external_id: str,
    ) -> None:
        """
        Register an asset in the registry.

        This is used to track which assets exist for unresolved queries.

        Args:
            asset_id: ID from source_assets table.
            source_type: Type of source.
            external_id: Source-specific identifier.
        """
        await self._db.execute(
            """INSERT OR REPLACE INTO asset_registry
               (asset_id, source_type, external_id)
               VALUES (?, ?, ?)""",
            (asset_id, source_type, external_id),
        )
        await self._db.commit()

    async def get_unresolved_assets(
        self,
        limit: int = 100,
        source_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get assets that haven't been resolved to leads.

        Args:
            limit: Maximum number of assets to return.
            source_type: Optional filter by source type.

        Returns:
            List of unresolved asset info dicts.
        """
        if source_type:
            cursor = await self._db.execute(
                """SELECT ar.asset_id, ar.source_type, ar.external_id, ar.registered_at
                   FROM asset_registry ar
                   LEFT JOIN asset_to_lead atl
                       ON ar.source_type = atl.asset_source_type
                       AND ar.external_id = atl.asset_external_id
                   WHERE atl.id IS NULL AND ar.source_type = ?
                   ORDER BY ar.registered_at DESC
                   LIMIT ?""",
                (source_type, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT ar.asset_id, ar.source_type, ar.external_id, ar.registered_at
                   FROM asset_registry ar
                   LEFT JOIN asset_to_lead atl
                       ON ar.source_type = atl.asset_source_type
                       AND ar.external_id = atl.asset_external_id
                   WHERE atl.id IS NULL
                   ORDER BY ar.registered_at DESC
                   LIMIT ?""",
                (limit,),
            )

        rows = await cursor.fetchall()
        return [
            {
                "asset_id": row[0],
                "source_type": row[1],
                "external_id": row[2],
                "registered_at": row[3],
            }
            for row in rows
        ]

    async def count_by_resolution_method(self) -> Dict[ResolutionMethod, int]:
        """
        Count links by resolution method.

        Returns:
            Dict mapping ResolutionMethod to count.
        """
        cursor = await self._db.execute(
            """SELECT resolved_by, COUNT(*) as count
               FROM asset_to_lead
               GROUP BY resolved_by"""
        )
        rows = await cursor.fetchall()
        return {ResolutionMethod(row[0]): row[1] for row in rows}

    async def delete_link(self, link_id: int) -> None:
        """
        Delete a link.

        Args:
            link_id: Database ID of the link to delete.
        """
        await self._db.execute(
            "DELETE FROM asset_to_lead WHERE id = ?",
            (link_id,),
        )
        await self._db.commit()

    def _row_to_link(self, row) -> AssetToLead:
        """Convert database row to AssetToLead."""
        import json

        return AssetToLead(
            id=row[0],
            asset_id=row[1],
            asset_source_type=row[2],
            asset_external_id=row[3],
            lead_canonical_key=row[4],
            confidence=row[5],
            resolved_by=ResolutionMethod(row[6]),
            resolved_at=datetime.fromisoformat(row[7]) if row[7] else datetime.utcnow(),
            metadata=json.loads(row[8]) if row[8] else None,
        )

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
