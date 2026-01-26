"""
Monitor Store - Database operations for Monitoring Subsystem

Provides transaction-safe CRUD operations for watches, snapshots, diffs, and alerts.
Uses the same connection as SignalStore for consistency.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

from monitoring.models import (
    Watch,
    Snapshot,
    Diff,
    SeverityComponents,
    MonitoringAlert,
    CanonicalKeyAlias,
    MonitoringConfig,
)

logger = logging.getLogger(__name__)


class MonitorStore:
    """
    Database operations for the monitoring subsystem.

    Uses SignalStore's connection for transaction consistency.
    """

    def __init__(self, signal_store: "SignalStore"):
        """
        Initialize MonitorStore.

        Args:
            signal_store: SignalStore instance for database access
        """
        self._signal_store = signal_store

    @property
    def _db(self):
        """Get database connection from SignalStore."""
        return self._signal_store._db

    # =========================================================================
    # WATCHES
    # =========================================================================

    async def create_watch(
        self,
        canonical_key: str,
        url: str,
        watch_type: str = "website",
        interval_seconds: int = 86400,
    ) -> Watch:
        """
        Create a new watch.

        Args:
            canonical_key: Canonical key for the company
            url: URL to monitor
            watch_type: Type of watch (website, portfolio, linkedin_about)
            interval_seconds: Check interval in seconds

        Returns:
            Created Watch with ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO watches (canonical_key, url, watch_type, interval_seconds, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key, watch_type, url) DO UPDATE SET
                interval_seconds = excluded.interval_seconds,
                active = 1
            RETURNING id, canonical_key, url, watch_type, interval_seconds,
                      last_checked_at, last_snapshot_id, consecutive_failures,
                      backoff_until, cooldown_until, active, created_at
            """,
            (canonical_key, url, watch_type, interval_seconds, now)
        )
        row = await cursor.fetchone()
        await self._db.commit()

        return self._row_to_watch(row)

    async def get_watch(self, watch_id: int) -> Optional[Watch]:
        """Get a watch by ID."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, url, watch_type, interval_seconds,
                   last_checked_at, last_snapshot_id, consecutive_failures,
                   backoff_until, cooldown_until, consecutive_low_sev_hits,
                   last_low_sev_at, active, created_at
            FROM watches
            WHERE id = ?
            """,
            (watch_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_watch(row)

    async def get_watch_by_url(
        self,
        canonical_key: str,
        url: str,
        watch_type: str = "website",
    ) -> Optional[Watch]:
        """Get a watch by canonical key, URL, and type."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, url, watch_type, interval_seconds,
                   last_checked_at, last_snapshot_id, consecutive_failures,
                   backoff_until, cooldown_until, consecutive_low_sev_hits,
                   last_low_sev_at, active, created_at
            FROM watches
            WHERE canonical_key = ? AND url = ? AND watch_type = ?
            """,
            (canonical_key, url, watch_type)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_watch(row)

    async def get_due_watches(self, limit: int = 100) -> List[Watch]:
        """
        Get watches that are due for checking.

        Respects:
        - active flag
        - backoff_until
        - interval_seconds since last_checked_at
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, url, watch_type, interval_seconds,
                   last_checked_at, last_snapshot_id, consecutive_failures,
                   backoff_until, cooldown_until, consecutive_low_sev_hits,
                   last_low_sev_at, active, created_at
            FROM watches
            WHERE active = 1
              AND (backoff_until IS NULL OR backoff_until <= ?)
              AND (cooldown_until IS NULL OR cooldown_until <= ?)
              AND (
                  last_checked_at IS NULL
                  OR datetime(last_checked_at, '+' || interval_seconds || ' seconds') <= ?
              )
            ORDER BY last_checked_at ASC NULLS FIRST
            LIMIT ?
            """,
            (now, now, now, limit)
        )
        rows = await cursor.fetchall()
        return [self._row_to_watch(row) for row in rows]

    async def get_watches_for_canonical_key(
        self,
        canonical_key: str,
        active_only: bool = True,
    ) -> List[Watch]:
        """Get all watches for a canonical key."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT id, canonical_key, url, watch_type, interval_seconds,
                   last_checked_at, last_snapshot_id, consecutive_failures,
                   backoff_until, cooldown_until, consecutive_low_sev_hits,
                   last_low_sev_at, active, created_at
            FROM watches
            WHERE canonical_key = ?
        """
        params: tuple = (canonical_key,)

        if active_only:
            query += " AND active = 1"

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_watch(row) for row in rows]

    async def update_watch_checked(self, watch_id: int) -> None:
        """Update watch after a successful check (no content change)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            UPDATE watches
            SET last_checked_at = ?,
                consecutive_failures = 0,
                backoff_until = NULL
            WHERE id = ?
            """,
            (now, watch_id)
        )
        await self._db.commit()

    async def update_watch_failed(
        self,
        watch_id: int,
        backoff_seconds: float = 3600.0,
        failure_category: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Update watch after a failed check with backoff.

        Args:
            watch_id: Watch ID
            backoff_seconds: Seconds until next retry
            failure_category: Failure category (v2.4: transient, client_error, etc.)
            error_message: Error message for logging
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        backoff_until = (now + timedelta(seconds=backoff_seconds)).isoformat()

        await self._db.execute(
            """
            UPDATE watches
            SET last_checked_at = ?,
                consecutive_failures = consecutive_failures + 1,
                backoff_until = ?,
                last_failure_category = ?,
                last_failure_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now.isoformat(), backoff_until, failure_category, error_message, now.isoformat(), watch_id)
        )
        await self._db.commit()

    async def deactivate_watch(self, watch_id: int) -> None:
        """Deactivate a watch."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute(
            "UPDATE watches SET active = 0 WHERE id = ?",
            (watch_id,)
        )
        await self._db.commit()

    def _row_to_watch(self, row: tuple) -> Watch:
        """Convert database row to Watch object."""
        return Watch(
            id=row[0],
            canonical_key=row[1],
            url=row[2],
            watch_type=row[3],
            interval_seconds=row[4],
            last_checked_at=self._parse_datetime(row[5]),
            last_snapshot_id=row[6],
            consecutive_failures=row[7] or 0,
            backoff_until=self._parse_datetime(row[8]),
            cooldown_until=self._parse_datetime(row[9]),
            consecutive_low_sev_hits=row[10] if len(row) > 10 else 0,
            last_low_sev_at=self._parse_datetime(row[11]) if len(row) > 11 else None,
            active=bool(row[12]) if len(row) > 12 else True,
            created_at=self._parse_datetime(row[13]) if len(row) > 13 else datetime.now(timezone.utc),
        )

    # =========================================================================
    # SNAPSHOTS
    # =========================================================================

    async def save_snapshot_and_update_watch(
        self,
        watch: Watch,
        snapshot: Snapshot,
        diff: Optional[Diff] = None,
    ) -> tuple[int, Optional[int]]:
        """
        Atomically save snapshot, diff, and update watch state.

        Uses a transaction to ensure all-or-nothing semantics.

        Args:
            watch: The watch being updated
            snapshot: New snapshot to save
            diff: Optional diff to save

        Returns:
            Tuple of (snapshot_id, diff_id or None)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        async with self._signal_store.transaction() as conn:
            # 1. Insert snapshot
            cursor = await conn.execute(
                """
                INSERT INTO snapshots (
                    watch_id, fetched_at, status_code, requested_url, final_url,
                    final_host, page_state, content_hash, hasher_version, text_length,
                    text_content_preview, embedding_key, error, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    watch.id,
                    snapshot.fetched_at.isoformat(),
                    snapshot.status_code,
                    snapshot.requested_url,
                    snapshot.final_url,
                    snapshot.final_host,
                    snapshot.page_state,
                    snapshot.content_hash,
                    snapshot.hasher_version,
                    snapshot.text_length,
                    snapshot.text_content_preview,
                    snapshot.embedding_key,
                    snapshot.error,
                    json.dumps(snapshot.metadata) if snapshot.metadata else None,
                )
            )
            snapshot_id = cursor.lastrowid

            # 2. Insert diff (if any)
            diff_id = None
            if diff:
                cursor = await conn.execute(
                    """
                    INSERT INTO diffs (
                        watch_id, old_snapshot_id, new_snapshot_id, created_at,
                        severity_score, severity_components_json, semantic_drift,
                        has_redirect, has_state_change, has_text_change, diff_summary_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        watch.id,
                        diff.old_snapshot_id,
                        snapshot_id,
                        diff.created_at.isoformat(),
                        diff.severity_score,
                        json.dumps(diff.severity_components.to_dict()) if diff.severity_components else None,
                        diff.semantic_drift,
                        int(diff.has_redirect),
                        int(diff.has_state_change),
                        int(diff.has_text_change),
                        json.dumps(diff.diff_summary) if diff.diff_summary else None,
                    )
                )
                diff_id = cursor.lastrowid

            # 3. Update watch state
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                UPDATE watches
                SET last_checked_at = ?,
                    last_snapshot_id = ?,
                    consecutive_failures = 0,
                    backoff_until = NULL
                WHERE id = ?
                """,
                (now, snapshot_id, watch.id)
            )

        logger.info(f"Saved snapshot {snapshot_id} for watch {watch.id}")
        return snapshot_id, diff_id

    async def get_snapshot(self, snapshot_id: int) -> Optional[Snapshot]:
        """Get a snapshot by ID."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, fetched_at, status_code, requested_url,
                   final_url, final_host, page_state, content_hash, hasher_version,
                   text_length, text_content_preview, embedding_key, error, metadata_json
            FROM snapshots
            WHERE id = ?
            """,
            (snapshot_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_snapshot(row)

    async def get_latest_snapshot(self, watch_id: int) -> Optional[Snapshot]:
        """Get the most recent snapshot for a watch."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, fetched_at, status_code, requested_url,
                   final_url, final_host, page_state, content_hash, hasher_version,
                   text_length, text_content_preview, embedding_key, error, metadata_json
            FROM snapshots
            WHERE watch_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (watch_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_snapshot(row)

    async def get_snapshots_for_watch(
        self,
        watch_id: int,
        limit: int = 10,
    ) -> List[Snapshot]:
        """Get recent snapshots for a watch."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, fetched_at, status_code, requested_url,
                   final_url, final_host, page_state, content_hash, hasher_version,
                   text_length, text_content_preview, embedding_key, error, metadata_json
            FROM snapshots
            WHERE watch_id = ?
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (watch_id, limit)
        )
        rows = await cursor.fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    def _row_to_snapshot(self, row: tuple) -> Snapshot:
        """Convert database row to Snapshot object."""
        return Snapshot(
            id=row[0],
            watch_id=row[1],
            fetched_at=self._parse_datetime(row[2]) or datetime.now(timezone.utc),
            status_code=row[3],
            requested_url=row[4],
            final_url=row[5],
            final_host=row[6],
            page_state=row[7],
            content_hash=row[8] or "",
            hasher_version=row[9] or "v1",
            text_length=row[10] or 0,
            text_content_preview=row[11],
            embedding_key=row[12],
            error=row[13],
            metadata=json.loads(row[14]) if row[14] else None,
        )

    async def find_recent_snapshot_by_hash(
        self,
        watch_id: int,
        content_hash: str,
        hasher_version: str,
        window_minutes: int = 5,
    ) -> Optional[Snapshot]:
        """
        Find a recent snapshot with matching hash (for recent-hash guard).

        This implements the crash-recovery fallback from Spec v2.4 Section 10.2.
        Only used when last_snapshot_id doesn't match - prevents duplicates
        after partial failures.

        Args:
            watch_id: Watch ID
            content_hash: Content hash to match
            hasher_version: Hasher version to match
            window_minutes: Time window to search (default 5 minutes)

        Returns:
            Matching Snapshot or None
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        window_start = (
            datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        ).isoformat()

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, fetched_at, status_code, requested_url,
                   final_url, final_host, page_state, content_hash, hasher_version,
                   text_length, text_content_preview, embedding_key, error, metadata_json
            FROM snapshots
            WHERE watch_id = ?
              AND content_hash = ?
              AND hasher_version = ?
              AND fetched_at >= ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (watch_id, content_hash, hasher_version, window_start)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_snapshot(row)

    async def check_hash_unchanged(
        self,
        watch: "Watch",
        content_hash: str,
        hasher_version: str,
    ) -> tuple[bool, Optional[Snapshot]]:
        """
        Two-step recent-hash guard per Spec v2.4 Section 10.2.

        Step 1: Check if last_snapshot_id points to matching hash
        Step 2: Fall back to time-window query (crash recovery)

        Args:
            watch: The watch being checked
            content_hash: New content hash
            hasher_version: Hasher version used

        Returns:
            Tuple of (is_unchanged, existing_snapshot)
            - (True, snapshot) if content unchanged (reuse this snapshot)
            - (False, None) if content changed (create new snapshot)
        """
        # Step 1: Check last_snapshot_id
        if watch.last_snapshot_id:
            last_snapshot = await self.get_snapshot(watch.last_snapshot_id)
            if last_snapshot:
                if (last_snapshot.content_hash == content_hash and
                    last_snapshot.hasher_version == hasher_version):
                    # Content unchanged - normal path
                    return (True, last_snapshot)

        # Step 2: Time-window fallback (crash recovery)
        # This catches the edge case where last_snapshot_id is stale
        recent = await self.find_recent_snapshot_by_hash(
            watch.id, content_hash, hasher_version
        )
        if recent and recent.id != watch.last_snapshot_id:
            # Found matching recent snapshot - reuse to prevent duplicate
            return (True, recent)

        # Content changed - create new snapshot
        return (False, None)

    # =========================================================================
    # DIFFS
    # =========================================================================

    async def get_diff(self, diff_id: int) -> Optional[Diff]:
        """Get a diff by ID."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, old_snapshot_id, new_snapshot_id, created_at,
                   severity_score, severity_components_json, semantic_drift,
                   has_redirect, has_state_change, has_text_change, diff_summary_json
            FROM diffs
            WHERE id = ?
            """,
            (diff_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_diff(row)

    async def get_recent_diffs(
        self,
        limit: int = 50,
        min_severity: float = 0.0,
    ) -> List[Diff]:
        """Get recent diffs across all watches."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, old_snapshot_id, new_snapshot_id, created_at,
                   severity_score, severity_components_json, semantic_drift,
                   has_redirect, has_state_change, has_text_change, diff_summary_json
            FROM diffs
            WHERE severity_score >= ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (min_severity, limit)
        )
        rows = await cursor.fetchall()
        return [self._row_to_diff(row) for row in rows]

    async def get_diffs_for_watch(
        self,
        watch_id: int,
        limit: int = 10,
    ) -> List[Diff]:
        """Get recent diffs for a specific watch."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, old_snapshot_id, new_snapshot_id, created_at,
                   severity_score, severity_components_json, semantic_drift,
                   has_redirect, has_state_change, has_text_change, diff_summary_json
            FROM diffs
            WHERE watch_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (watch_id, limit)
        )
        rows = await cursor.fetchall()
        return [self._row_to_diff(row) for row in rows]

    def _row_to_diff(self, row: tuple) -> Diff:
        """Convert database row to Diff object."""
        components = None
        if row[6]:
            comp_data = json.loads(row[6])
            components = SeverityComponents(**comp_data)

        return Diff(
            id=row[0],
            watch_id=row[1],
            old_snapshot_id=row[2],
            new_snapshot_id=row[3],
            created_at=self._parse_datetime(row[4]) or datetime.now(timezone.utc),
            severity_score=row[5] or 0.0,
            severity_components=components,
            semantic_drift=row[7],
            has_redirect=bool(row[8]),
            has_state_change=bool(row[9]),
            has_text_change=bool(row[10]),
            diff_summary=json.loads(row[11]) if row[11] else None,
        )

    # =========================================================================
    # ALERTS
    # =========================================================================

    async def create_monitoring_alert(
        self,
        watch_id: int,
        diff_id: Optional[int],
        alert_reason: str,
        severity_score: float,
        payload: Optional[Dict[str, Any]] = None,
    ) -> MonitoringAlert:
        """Create a new monitoring alert."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO monitoring_alerts (
                watch_id, diff_id, alert_reason, severity_score, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                watch_id,
                diff_id,
                alert_reason,
                severity_score,
                now,
                json.dumps(payload) if payload else None,
            )
        )
        row = await cursor.fetchone()
        await self._db.commit()

        alert = MonitoringAlert(
            id=row[0],
            watch_id=watch_id,
            diff_id=diff_id,
            alert_reason=alert_reason,
            severity_score=severity_score,
            created_at=self._parse_datetime(now) or datetime.now(timezone.utc),
            payload=payload,
        )
        logger.info(f"Created monitoring alert {alert.id}: {alert_reason}")
        return alert

    async def get_unacked_alerts(self, limit: int = 50) -> List[MonitoringAlert]:
        """Get unacknowledged alerts, most recent first."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, watch_id, diff_id, alert_reason, severity_score,
                   acknowledged, acknowledged_by, acknowledged_at, created_at, payload_json
            FROM monitoring_alerts
            WHERE acknowledged = 0
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def acknowledge_alert(
        self,
        alert_id: int,
        acknowledged_by: str = "system",
    ) -> None:
        """Acknowledge an alert."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            UPDATE monitoring_alerts
            SET acknowledged = 1,
                acknowledged_by = ?,
                acknowledged_at = ?
            WHERE id = ?
            """,
            (acknowledged_by, now, alert_id)
        )
        await self._db.commit()
        logger.info(f"Acknowledged alert {alert_id} by {acknowledged_by}")

    def _row_to_alert(self, row: tuple) -> MonitoringAlert:
        """Convert database row to MonitoringAlert object."""
        return MonitoringAlert(
            id=row[0],
            watch_id=row[1],
            diff_id=row[2],
            alert_reason=row[3],
            severity_score=row[4] or 0.0,
            acknowledged=bool(row[5]),
            acknowledged_by=row[6],
            acknowledged_at=self._parse_datetime(row[7]),
            created_at=self._parse_datetime(row[8]) or datetime.now(timezone.utc),
            payload=json.loads(row[9]) if row[9] else None,
        )

    # =========================================================================
    # ALIASES
    # =========================================================================

    async def create_alias(
        self,
        old_key: str,
        new_key: str,
        reason: str = "redirect",
    ) -> CanonicalKeyAlias:
        """Create a canonical key alias."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO canonical_key_aliases (old_key, new_key, reason, detected_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(old_key, new_key) DO UPDATE SET
                reason = excluded.reason,
                detected_at = excluded.detected_at
            RETURNING id
            """,
            (old_key, new_key, reason, now)
        )
        row = await cursor.fetchone()
        await self._db.commit()

        return CanonicalKeyAlias(
            id=row[0],
            old_key=old_key,
            new_key=new_key,
            reason=reason,
            detected_at=self._parse_datetime(now) or datetime.now(timezone.utc),
        )

    async def get_aliases_for_key(self, canonical_key: str) -> List[CanonicalKeyAlias]:
        """Get all aliases where this key is either old or new."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, old_key, new_key, reason, detected_at
            FROM canonical_key_aliases
            WHERE old_key = ? OR new_key = ?
            ORDER BY detected_at DESC
            """,
            (canonical_key, canonical_key)
        )
        rows = await cursor.fetchall()
        return [
            CanonicalKeyAlias(
                id=row[0],
                old_key=row[1],
                new_key=row[2],
                reason=row[3],
                detected_at=self._parse_datetime(row[4]) or datetime.now(timezone.utc),
            )
            for row in rows
        ]

    # =========================================================================
    # CONFIG
    # =========================================================================

    async def get_config(self) -> MonitoringConfig:
        """Get the monitoring configuration."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT config_json FROM monitoring_config WHERE id = 1"
        )
        row = await cursor.fetchone()

        if row and row[0]:
            return MonitoringConfig.from_json(row[0])

        # Return default config if not found
        return MonitoringConfig()

    async def update_config(self, config: MonitoringConfig) -> None:
        """Update the monitoring configuration."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute(
            """
            INSERT INTO monitoring_config (id, config_json)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET config_json = excluded.config_json
            """,
            (config.to_json(),)
        )
        await self._db.commit()

    # =========================================================================
    # METRICS / RUNS
    # =========================================================================

    async def start_monitoring_run(self, run_id: str) -> None:
        """Record the start of a monitoring run."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            INSERT INTO monitoring_runs (run_id, started_at, created_at)
            VALUES (?, ?, ?)
            """,
            (run_id, now, now)
        )
        await self._db.commit()

    async def complete_monitoring_run(
        self,
        run_id: str,
        watches_checked: int = 0,
        snapshots_taken: int = 0,
        diffs_computed: int = 0,
        high_severity_events: int = 0,
        profile_updates_triggered: int = 0,
        errors: Optional[List[str]] = None,
    ) -> None:
        """Record the completion of a monitoring run."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)

        # Get start time to compute duration
        cursor = await self._db.execute(
            "SELECT started_at FROM monitoring_runs WHERE run_id = ?",
            (run_id,)
        )
        row = await cursor.fetchone()
        duration = None
        if row and row[0]:
            started_at = self._parse_datetime(row[0])
            if started_at:
                duration = (now - started_at).total_seconds()

        await self._db.execute(
            """
            UPDATE monitoring_runs
            SET completed_at = ?,
                duration_seconds = ?,
                watches_checked = ?,
                snapshots_taken = ?,
                diffs_computed = ?,
                high_severity_events = ?,
                profile_updates_triggered = ?,
                errors_json = ?
            WHERE run_id = ?
            """,
            (
                now.isoformat(),
                duration,
                watches_checked,
                snapshots_taken,
                diffs_computed,
                high_severity_events,
                profile_updates_triggered,
                json.dumps(errors) if errors else None,
                run_id,
            )
        )
        await self._db.commit()

    async def get_recent_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent monitoring runs."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT run_id, started_at, completed_at, duration_seconds,
                   watches_checked, snapshots_taken, diffs_computed,
                   high_severity_events, profile_updates_triggered, errors_json
            FROM monitoring_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "run_id": row[0],
                "started_at": row[1],
                "completed_at": row[2],
                "duration_seconds": row[3],
                "watches_checked": row[4],
                "snapshots_taken": row[5],
                "diffs_computed": row[6],
                "high_severity_events": row[7],
                "profile_updates_triggered": row[8],
                "errors": json.loads(row[9]) if row[9] else None,
            }
            for row in rows
        ]

    # =========================================================================
    # DEBOUNCE / COOLDOWN STATE
    # =========================================================================

    async def update_debounce_state(
        self,
        watch_id: int,
        severity_score: float,
        config: MonitoringConfig,
    ) -> bool:
        """
        Update debounce state and return whether to trigger action.

        Args:
            watch_id: Watch ID
            severity_score: Current severity score
            config: Monitoring config

        Returns:
            True if action should be triggered (debounce threshold met)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        window_start = (now - timedelta(hours=config.debounce_window_hours)).isoformat()

        # Get current watch state
        watch = await self.get_watch(watch_id)
        if not watch:
            return False

        # Check if this is a low-severity hit
        if severity_score < config.profile_threshold:
            # Update low-severity tracking
            if watch.last_low_sev_at:
                # Within window?
                if watch.last_low_sev_at.isoformat() >= window_start:
                    new_count = watch.consecutive_low_sev_hits + 1
                else:
                    new_count = 1
            else:
                new_count = 1

            await self._db.execute(
                """
                UPDATE watches
                SET consecutive_low_sev_hits = ?,
                    last_low_sev_at = ?
                WHERE id = ?
                """,
                (new_count, now.isoformat(), watch_id)
            )
            await self._db.commit()

            # Trigger if we've hit the debounce count
            return new_count >= config.debounce_count

        # High severity - always trigger
        return True

    async def set_cooldown(
        self,
        watch_id: int,
        config: MonitoringConfig,
    ) -> None:
        """Set cooldown period after a profile update."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cooldown_until = (
            datetime.now(timezone.utc) + timedelta(hours=config.cooldown_hours)
        ).isoformat()

        await self._db.execute(
            """
            UPDATE watches
            SET cooldown_until = ?,
                consecutive_low_sev_hits = 0,
                last_low_sev_at = NULL
            WHERE id = ?
            """,
            (cooldown_until, watch_id)
        )
        await self._db.commit()

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 datetime string."""
        if not value:
            return None
        try:
            # Handle both with and without timezone
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            if "+" not in value and "-" not in value[10:]:
                value += "+00:00"
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
