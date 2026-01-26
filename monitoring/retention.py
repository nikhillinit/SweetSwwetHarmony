"""
Retention Management for Monitoring Subsystem

Handles pruning of old snapshots and diffs to prevent unbounded growth.
Configured via MonitoringConfig:
- max_snapshots_per_watch: Keep only this many snapshots per watch
- max_diff_age_days: Delete diffs older than this
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

from monitoring.models import MonitoringConfig

logger = logging.getLogger(__name__)


class RetentionManager:
    """
    Manages retention of monitoring data.

    Prunes old snapshots and diffs based on configuration.
    """

    def __init__(
        self,
        signal_store: "SignalStore",
        config: MonitoringConfig | None = None,
    ):
        """
        Initialize RetentionManager.

        Args:
            signal_store: Signal store for database access
            config: Monitoring configuration
        """
        self._signal_store = signal_store
        self._config = config or MonitoringConfig()

    @property
    def _db(self):
        """Get database connection from SignalStore."""
        return self._signal_store._db

    async def run_retention(self) -> dict[str, int]:
        """
        Run retention cleanup.

        Returns:
            Dict with counts of pruned items
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        stats = {
            "snapshots_pruned": 0,
            "diffs_pruned": 0,
            "alerts_pruned": 0,
            "orphan_embeddings_pruned": 0,
        }

        # 1. Prune old diffs (before snapshots to maintain FK integrity)
        stats["diffs_pruned"] = await self._prune_old_diffs()

        # 2. Prune excess snapshots per watch (with FK safety)
        stats["snapshots_pruned"] = await self._prune_excess_snapshots()

        # 3. Prune old acknowledged alerts
        stats["alerts_pruned"] = await self._prune_old_alerts()

        # 4. Prune orphan embeddings (snapshot_v1 kind with no matching snapshot)
        stats["orphan_embeddings_pruned"] = await self._prune_orphan_embeddings()

        logger.info(
            f"Retention complete: "
            f"{stats['snapshots_pruned']} snapshots, "
            f"{stats['diffs_pruned']} diffs, "
            f"{stats['alerts_pruned']} alerts, "
            f"{stats['orphan_embeddings_pruned']} orphan embeddings"
        )

        return stats

    async def _prune_old_diffs(self) -> int:
        """Delete diffs older than max_diff_age_days."""
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=self._config.max_diff_age_days)
        ).isoformat()

        cursor = await self._db.execute(
            """
            DELETE FROM diffs
            WHERE created_at < ?
            """,
            (cutoff,)
        )
        await self._db.commit()

        count = cursor.rowcount
        if count > 0:
            logger.info(f"Pruned {count} diffs older than {self._config.max_diff_age_days} days")

        return count

    async def _prune_excess_snapshots(self) -> int:
        """
        Delete excess snapshots beyond max_snapshots_per_watch.

        IMPORTANT: Deletes referencing diffs FIRST to maintain FK integrity.
        """
        max_keep = self._config.max_snapshots_per_watch

        # Get watches with excess snapshots
        cursor = await self._db.execute(
            """
            SELECT watch_id, COUNT(*) as cnt
            FROM snapshots
            GROUP BY watch_id
            HAVING cnt > ?
            """,
            (max_keep,)
        )
        watches_to_prune = await cursor.fetchall()

        total_pruned = 0

        for watch_id, count in watches_to_prune:
            to_delete = count - max_keep

            # Step 1: Get IDs of snapshots to delete
            cursor = await self._db.execute(
                """
                SELECT id FROM snapshots
                WHERE watch_id = ?
                ORDER BY fetched_at ASC
                LIMIT ?
                """,
                (watch_id, to_delete)
            )
            snapshot_ids_to_delete = [row[0] for row in await cursor.fetchall()]

            if not snapshot_ids_to_delete:
                continue

            # Step 2: Delete diffs that reference these snapshots (FK integrity)
            placeholders = ",".join("?" * len(snapshot_ids_to_delete))
            await self._db.execute(
                f"""
                DELETE FROM diffs
                WHERE old_snapshot_id IN ({placeholders})
                   OR new_snapshot_id IN ({placeholders})
                """,
                snapshot_ids_to_delete + snapshot_ids_to_delete
            )

            # Step 3: Delete the snapshots
            await self._db.execute(
                f"""
                DELETE FROM snapshots
                WHERE id IN ({placeholders})
                """,
                snapshot_ids_to_delete
            )
            total_pruned += len(snapshot_ids_to_delete)

        await self._db.commit()

        if total_pruned > 0:
            logger.info(f"Pruned {total_pruned} excess snapshots (with FK-safe diff deletion)")

        return total_pruned

    async def _prune_old_alerts(self, days: int = 90) -> int:
        """Delete acknowledged alerts older than N days."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        cursor = await self._db.execute(
            """
            DELETE FROM monitoring_alerts
            WHERE acknowledged = 1
              AND acknowledged_at < ?
            """,
            (cutoff,)
        )
        await self._db.commit()

        count = cursor.rowcount
        if count > 0:
            logger.info(f"Pruned {count} acknowledged alerts older than {days} days")

        return count

    async def get_storage_stats(self) -> dict[str, int]:
        """Get storage statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        stats = {}

        # Count snapshots
        cursor = await self._db.execute("SELECT COUNT(*) FROM snapshots")
        row = await cursor.fetchone()
        stats["total_snapshots"] = row[0]

        # Count diffs
        cursor = await self._db.execute("SELECT COUNT(*) FROM diffs")
        row = await cursor.fetchone()
        stats["total_diffs"] = row[0]

        # Count watches
        cursor = await self._db.execute("SELECT COUNT(*) FROM watches WHERE active = 1")
        row = await cursor.fetchone()
        stats["active_watches"] = row[0]

        # Count alerts
        cursor = await self._db.execute("SELECT COUNT(*) FROM monitoring_alerts WHERE acknowledged = 0")
        row = await cursor.fetchone()
        stats["unacked_alerts"] = row[0]

        return stats

    async def _prune_orphan_embeddings(self) -> int:
        """
        Delete orphan embeddings where the referenced snapshot no longer exists.

        Targets embeddings with embedding_kind='snapshot_v1' where the
        canonical_key (which stores the snapshot's embedding_key) points
        to a non-existent snapshot.

        Returns:
            Count of deleted embeddings
        """
        # Check if company_embeddings table exists
        cursor = await self._db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='company_embeddings'
            """
        )
        if not await cursor.fetchone():
            logger.debug("company_embeddings table does not exist, skipping orphan prune")
            return 0

        # Delete embeddings where:
        # - embedding_kind = 'snapshot_v1'
        # - canonical_key NOT IN (SELECT embedding_key FROM snapshots WHERE embedding_key IS NOT NULL)
        cursor = await self._db.execute(
            """
            DELETE FROM company_embeddings
            WHERE embedding_kind = 'snapshot_v1'
              AND canonical_key NOT IN (
                  SELECT embedding_key FROM snapshots
                  WHERE embedding_key IS NOT NULL
              )
            """
        )
        await self._db.commit()

        count = cursor.rowcount
        if count > 0:
            logger.info(f"Pruned {count} orphan snapshot embeddings")

        return count


async def run_retention(
    signal_store: "SignalStore",
    config: MonitoringConfig | None = None,
) -> dict[str, int]:
    """
    Convenience function to run retention cleanup.

    Args:
        signal_store: Signal store instance
        config: Optional config override

    Returns:
        Dict with counts of pruned items
    """
    manager = RetentionManager(signal_store, config)
    return await manager.run_retention()
