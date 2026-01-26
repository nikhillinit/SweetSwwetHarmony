"""
Watch State Machine for Monitoring Subsystem

Lightweight class for updating watch state and logging to watch_events.
All watch state changes go through this class for audit trail.

Per Spec v2.4 Section 9.3: The state machine records transitions;
it does NOT contain gating logic (that stays in gating.py).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from monitoring.monitor_store import MonitorStore
    from monitoring.models import Watch
    from monitoring.failure_classifier import FailureCategory

logger = logging.getLogger(__name__)


class WatchStateMachine:
    """
    Manages watch state transitions with audit logging.

    All state changes to watches go through this class, which ensures:
    1. Watch table is updated atomically
    2. watch_events audit log is appended
    3. Consistent event types for monitoring

    Event types:
    - fetch_started: Check began
    - fetch_success_unchanged: Fetch succeeded, content unchanged
    - fetch_success_changed: Fetch succeeded, content changed
    - fetch_failed: Fetch failed
    - snapshot_recorded: New snapshot saved
    - diff_calculated: Diff computed
    - alert_created: Alert created
    - profile_update_enqueued: Profile update queued
    - deactivated: Watch deactivated

    Usage:
        sm = WatchStateMachine(monitor_store, watch)
        await sm.record_fetch_started()

        if success:
            if content_changed:
                await sm.record_fetch_success_changed()
                await sm.record_snapshot_recorded(snapshot_id)
            else:
                await sm.record_fetch_success_unchanged()
        else:
            await sm.record_fetch_failed(category, error)
    """

    def __init__(
        self,
        store: "MonitorStore",
        watch: "Watch",
    ):
        """
        Initialize state machine.

        Args:
            store: MonitorStore for database operations
            watch: The watch being managed
        """
        self.store = store
        self.watch = watch
        self._pending_events: list = []

    async def record_fetch_started(self) -> None:
        """Record that a fetch check has started."""
        await self._append_event("fetch_started", {})

    async def record_fetch_success_unchanged(self) -> None:
        """Record a successful fetch with no content change."""
        now = datetime.now(timezone.utc)

        # Update watch: reset failures, update last_checked_at
        await self._update_watch({
            "last_checked_at": now.isoformat(),
            "consecutive_failures": 0,
            "backoff_until": None,
            "last_failure_category": None,
            "last_failure_error": None,
            "updated_at": now.isoformat(),
        })

        await self._append_event("fetch_success_unchanged", {})

    async def record_fetch_success_changed(
        self,
        content_hash: str,
        hasher_version: str,
    ) -> None:
        """
        Record a successful fetch with content change.

        Note: Does NOT update last_snapshot_id - that happens in record_snapshot_recorded.

        Args:
            content_hash: New content hash
            hasher_version: Hasher version used
        """
        now = datetime.now(timezone.utc)

        # Update watch: reset failures, update last_checked_at
        await self._update_watch({
            "last_checked_at": now.isoformat(),
            "consecutive_failures": 0,
            "backoff_until": None,
            "last_failure_category": None,
            "last_failure_error": None,
            "updated_at": now.isoformat(),
        })

        await self._append_event("fetch_success_changed", {
            "content_hash": content_hash,
            "hasher_version": hasher_version,
        })

    async def record_fetch_failed(
        self,
        category: "FailureCategory",
        error_message: str,
        backoff_until: datetime,
        should_deactivate: bool = False,
        deactivation_reason: Optional[str] = None,
    ) -> None:
        """
        Record a failed fetch with appropriate backoff.

        Args:
            category: Failure category
            error_message: Error description
            backoff_until: When to retry
            should_deactivate: Whether to deactivate the watch
            deactivation_reason: Reason for deactivation
        """
        now = datetime.now(timezone.utc)

        update_data = {
            "last_checked_at": now.isoformat(),
            "consecutive_failures": self.watch.consecutive_failures + 1,
            "backoff_until": backoff_until.isoformat(),
            "last_failure_category": category.value if hasattr(category, 'value') else str(category),
            "last_failure_error": error_message[:500],  # Truncate long errors
            "updated_at": now.isoformat(),
        }

        if should_deactivate:
            update_data["active"] = 0
            update_data["deactivated_reason"] = deactivation_reason

        await self._update_watch(update_data)

        await self._append_event("fetch_failed", {
            "category": category.value if hasattr(category, 'value') else str(category),
            "error": error_message[:500],
            "consecutive_failures": self.watch.consecutive_failures + 1,
            "backoff_until": backoff_until.isoformat(),
        })

        if should_deactivate:
            await self._append_event("deactivated", {
                "reason": deactivation_reason,
                "consecutive_failures": self.watch.consecutive_failures + 1,
            })

    async def record_snapshot_recorded(
        self,
        snapshot_id: int,
        is_maintenance_diff: bool = False,
    ) -> None:
        """
        Record that a snapshot was saved.

        Updates last_snapshot_id on the watch.

        Args:
            snapshot_id: ID of the saved snapshot
            is_maintenance_diff: True if this was a hasher_version change
        """
        now = datetime.now(timezone.utc)

        await self._update_watch({
            "last_snapshot_id": snapshot_id,
            "updated_at": now.isoformat(),
        })

        await self._append_event("snapshot_recorded", {
            "snapshot_id": snapshot_id,
            "is_maintenance_diff": is_maintenance_diff,
        })

    async def record_diff_calculated(
        self,
        diff_id: int,
        severity_score: float,
        instant_trigger: Optional[str] = None,
    ) -> None:
        """
        Record that a diff was calculated.

        Args:
            diff_id: ID of the diff
            severity_score: Calculated severity
            instant_trigger: Name of instant trigger if applicable
        """
        event_data = {
            "diff_id": diff_id,
            "severity_score": severity_score,
        }
        if instant_trigger:
            event_data["instant_trigger"] = instant_trigger

        await self._append_event("diff_calculated", event_data)

    async def record_alert_created(
        self,
        alert_id: int,
        reason: str,
        severity_score: float,
    ) -> None:
        """
        Record that an alert was created.

        Args:
            alert_id: ID of the alert
            reason: Alert reason
            severity_score: Severity that triggered the alert
        """
        await self._append_event("alert_created", {
            "alert_id": alert_id,
            "reason": reason,
            "severity_score": severity_score,
        })

    async def record_profile_update_enqueued(
        self,
        outbox_id: int,
        trigger: str,
    ) -> None:
        """
        Record that a profile update was enqueued.

        Args:
            outbox_id: ID of the outbox entry
            trigger: What triggered the update
        """
        await self._append_event("profile_update_enqueued", {
            "outbox_id": outbox_id,
            "trigger": trigger,
        })

    async def record_cooldown_updated(
        self,
        cooldown_until: Optional[datetime],
        reason: str,
    ) -> None:
        """
        Record that cooldown state was updated.

        Args:
            cooldown_until: New cooldown expiry (or None if cleared)
            reason: Why cooldown changed
        """
        now = datetime.now(timezone.utc)

        await self._update_watch({
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "updated_at": now.isoformat(),
        })

        await self._append_event("cooldown_updated", {
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "reason": reason,
        })

    async def record_deactivated(
        self,
        reason: str,
    ) -> None:
        """
        Record that the watch was deactivated.

        Args:
            reason: Why the watch was deactivated
        """
        now = datetime.now(timezone.utc)

        await self._update_watch({
            "active": 0,
            "deactivated_reason": reason,
            "updated_at": now.isoformat(),
        })

        await self._append_event("deactivated", {
            "reason": reason,
        })

    async def _update_watch(self, updates: Dict[str, Any]) -> None:
        """
        Update watch in database.

        Args:
            updates: Dict of column -> value updates
        """
        if not self.store._db:
            raise RuntimeError("Database not initialized")

        # Build SET clause
        set_parts = []
        values = []
        for key, value in updates.items():
            set_parts.append(f"{key} = ?")
            values.append(value)

        values.append(self.watch.id)

        await self.store._db.execute(
            f"UPDATE watches SET {', '.join(set_parts)} WHERE id = ?",
            tuple(values)
        )
        await self.store._db.commit()

        # Update local watch object
        for key, value in updates.items():
            if hasattr(self.watch, key):
                setattr(self.watch, key, value)

    async def _append_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> None:
        """
        Append event to watch_events table.

        Args:
            event_type: Type of event
            event_data: Event payload
        """
        if not self.store._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self.store._db.execute(
            """
            INSERT INTO watch_events (watch_id, occurred_at, event_type, event_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                self.watch.id,
                now,
                event_type,
                json.dumps(event_data) if event_data else None,
            )
        )
        await self.store._db.commit()

        logger.debug(f"Watch {self.watch.id} event: {event_type} - {event_data}")
