"""
SourceAssetStore: Raw snapshot storage for the Two-Entity Model.

This module stores raw assets from various sources (GitHub repos, Product Hunt
launches, etc.) before they're converted to signals. It enables:

1. Change detection: Compare current snapshot to previous
2. Entity resolution: Link multiple assets to the same company
3. Audit trail: Full history of what we've seen

The Two-Entity Model:
- SourceAsset: Raw data from a source (this store)
- Lead: Company entity in CRM (Notion)

Assets can be linked to Leads through the asset_to_lead mapping table.
"""
import aiosqlite
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


@dataclass
class SourceAsset:
    """
    Raw asset from a source.

    This represents a single observation of an entity from a source API.
    Multiple SourceAssets can exist for the same external_id (snapshots over time).
    """
    source_type: str  # github_repo, product_hunt, sec_filing, greenhouse_job, etc.
    external_id: str  # Stable identifier from source (repo full_name, PH id, etc.)
    raw_payload: Dict[str, Any]  # Full API response (or relevant subset)
    fetched_at: datetime  # When we fetched this snapshot
    id: Optional[int] = None  # Database ID (set after save)
    change_detected: bool = False  # True if this differs from previous snapshot
    created_at: Optional[datetime] = None  # When record was created


class SourceAssetStore:
    """
    SQLite-based storage for source assets.

    Stores raw snapshots from various sources, enabling:
    - Change detection via snapshot comparison
    - Historical analysis
    - Entity resolution

    Usage:
        store = SourceAssetStore("assets.db")
        await store.initialize()

        asset = SourceAsset(
            source_type="github_repo",
            external_id="owner/repo",
            raw_payload={"description": "Cool project"},
            fetched_at=datetime.utcnow()
        )
        asset_id = await store.save_asset(asset)

        previous = await store.get_previous_snapshot("github_repo", "owner/repo")
    """

    def __init__(self, db_path: str):
        """
        Initialize SourceAssetStore.

        Args:
            db_path: Path to SQLite database. Use ":memory:" for in-memory.
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS source_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                change_detected BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Index for efficient lookups
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_assets_lookup
            ON source_assets(source_type, external_id, fetched_at DESC)
        """)

        # Index for finding assets with changes
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_assets_changes
            ON source_assets(change_detected, created_at DESC)
        """)

        await self._db.commit()
        logger.info(f"SourceAssetStore initialized at {self.db_path}")

    async def save_asset(self, asset: SourceAsset) -> int:
        """
        Save a source asset.

        Args:
            asset: SourceAsset to save.

        Returns:
            Database ID of the saved asset.
        """
        cursor = await self._db.execute(
            """INSERT INTO source_assets
               (source_type, external_id, raw_payload, fetched_at, change_detected)
               VALUES (?, ?, ?, ?, ?)""",
            (
                asset.source_type,
                asset.external_id,
                json.dumps(asset.raw_payload),
                asset.fetched_at.isoformat(),
                asset.change_detected,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_asset(self, asset_id: int) -> Optional[SourceAsset]:
        """
        Retrieve an asset by ID.

        Args:
            asset_id: Database ID.

        Returns:
            SourceAsset if found, None otherwise.
        """
        cursor = await self._db.execute(
            "SELECT * FROM source_assets WHERE id = ?", (asset_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_asset(row)

    async def get_previous_snapshot(
        self,
        source_type: str,
        external_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the second-most-recent snapshot for comparison.

        This is used by TriggerGate to compare old vs new.

        Args:
            source_type: Type of source (github_repo, etc.)
            external_id: Source-specific identifier.

        Returns:
            Raw payload of previous snapshot, or None if no previous.
        """
        cursor = await self._db.execute(
            """SELECT raw_payload FROM source_assets
               WHERE source_type = ? AND external_id = ?
               ORDER BY fetched_at DESC
               LIMIT 1 OFFSET 1""",
            (source_type, external_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return json.loads(row[0])

    async def get_latest_snapshot(
        self,
        source_type: str,
        external_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent snapshot.

        Args:
            source_type: Type of source.
            external_id: Source-specific identifier.

        Returns:
            Raw payload of latest snapshot, or None if none exists.
        """
        cursor = await self._db.execute(
            """SELECT raw_payload FROM source_assets
               WHERE source_type = ? AND external_id = ?
               ORDER BY fetched_at DESC
               LIMIT 1""",
            (source_type, external_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return json.loads(row[0])

    async def get_assets_with_changes(
        self,
        limit: int = 100,
        source_type: Optional[str] = None,
    ) -> List[SourceAsset]:
        """
        Get assets that have changes detected.

        Args:
            limit: Maximum number of assets to return.
            source_type: Optional filter by source type.

        Returns:
            List of SourceAssets with change_detected=True.
        """
        if source_type:
            cursor = await self._db.execute(
                """SELECT * FROM source_assets
                   WHERE change_detected = TRUE AND source_type = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (source_type, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM source_assets
                   WHERE change_detected = TRUE
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            )

        rows = await cursor.fetchall()
        return [self._row_to_asset(row) for row in rows]

    async def count_by_source_type(self) -> Dict[str, int]:
        """
        Count assets by source type.

        Returns:
            Dict mapping source_type to count.
        """
        cursor = await self._db.execute(
            """SELECT source_type, COUNT(*) as count
               FROM source_assets
               GROUP BY source_type"""
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_snapshot_count(
        self,
        source_type: str,
        external_id: str,
    ) -> int:
        """
        Count how many snapshots exist for an entity.

        Args:
            source_type: Type of source.
            external_id: Source-specific identifier.

        Returns:
            Number of snapshots.
        """
        cursor = await self._db.execute(
            """SELECT COUNT(*) FROM source_assets
               WHERE source_type = ? AND external_id = ?""",
            (source_type, external_id),
        )
        row = await cursor.fetchone()
        return row[0]

    def _row_to_asset(self, row) -> SourceAsset:
        """Convert database row to SourceAsset."""
        return SourceAsset(
            id=row[0],
            source_type=row[1],
            external_id=row[2],
            raw_payload=json.loads(row[3]),
            fetched_at=datetime.fromisoformat(row[4]),
            change_detected=bool(row[5]),
            created_at=datetime.fromisoformat(row[6]) if row[6] else None,
        )

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
