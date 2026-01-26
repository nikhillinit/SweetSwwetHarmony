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
        }

        # 1. Prune old diffs
        stats["diffs_pruned"] = await self._prune_old_diffs()

        # 2. Prune excess snapshots per watch
        stats["snapshots_pruned"] = await self._prune_excess_snapshots()

        # 3. Prune old acknowledged alerts
        stats["alerts_pruned"] = await self._prune_old_alerts()

        logger.info(
            f"Retention complete: "
            f"{stats['snapshots_pruned']} snapshots, "
            f"{stats['diffs_pruned']} diffs, "
            f"{stats['alerts_pruned']} alerts"
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
        """Delete excess snapshots beyond max_snapshots_per_watch."""
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

            # Delete oldest snapshots for this watch
            cursor = await self._db.execute(
                """
                DELETE FROM snapshots
                WHERE id IN (
                    SELECT id FROM snapshots
                    WHERE watch_id = ?
                    ORDER BY fetched_at ASC
                    LIMIT ?
                )
                """,
                (watch_id, to_delete)
            )
            total_pruned += cursor.rowcount

        await self._db.commit()

        if total_pruned > 0:
            logger.info(f"Pruned {total_pruned} excess snapshots")

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
