"""
Notion Poller

Polls Notion Inbox for user decisions and syncs to local database.

Workflow:
1. Query Notion for recently modified pages (status != New)
2. Extract decision (Approved/Rejected) and rejection reason
3. Insert into user_actions table
4. Update signal status

Runs periodically (e.g., every 5 minutes via cron or scheduler).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .inbox_connector import NotionInboxConnector, NotionPage

if TYPE_CHECKING:
    from ..storage.consumer_store import ConsumerStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SyncedAction:
    """Record of a synced user action."""
    signal_id: int
    notion_page_id: str
    action: str  # 'approve' or 'reject'
    rejection_reason: Optional[str] = None
    rejection_notes: Optional[str] = None
    thesis_score: Optional[float] = None


# =============================================================================
# NOTION POLLER
# =============================================================================

class NotionPoller:
    """
    Polls Notion for user decisions and syncs to local database.

    Captures:
    - Status changes (New → Approved/Rejected)
    - Rejection reasons
    - Reviewer notes

    Usage:
        poller = NotionPoller(store)

        # Poll and sync
        count = await poller.poll_and_sync(since_minutes=10)
        print(f"Synced {count} decisions")

        # Run continuously
        await poller.run_forever(interval_minutes=5)
    """

    def __init__(
        self,
        store: ConsumerStore,
        connector: Optional[NotionInboxConnector] = None,
    ):
        """
        Initialize poller.

        Args:
            store: ConsumerStore for recording actions
            connector: NotionInboxConnector (created if not provided)
        """
        self.store = store
        self._connector = connector

    @property
    def connector(self) -> NotionInboxConnector:
        """Lazy-load connector."""
        if self._connector is None:
            self._connector = NotionInboxConnector()
        return self._connector

    async def poll_and_sync(self, since_minutes: int = 10) -> int:
        """
        Poll Notion for recently updated pages and sync decisions.

        Args:
            since_minutes: Only process pages modified in last N minutes

        Returns:
            Number of actions synced
        """
        # Calculate cutoff time
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

        # Query Notion for non-New pages
        try:
            pages = await self.connector.query_recently_modified(
                exclude_status="New",
                limit=100,
            )
        except Exception as e:
            logger.error(f"Failed to query Notion: {e}")
            return 0

        synced = 0

        for page in pages:
            # Skip if not recently modified
            if page.last_edited_time and page.last_edited_time < cutoff:
                continue

            # Skip if no signal ID
            if not page.signal_id:
                continue

            # Process decision
            action = await self._process_page_decision(page)
            if action:
                synced += 1

        if synced > 0:
            logger.info(f"Synced {synced} decisions from Notion")

        return synced

    async def _process_page_decision(self, page: NotionPage) -> Optional[SyncedAction]:
        """
        Process a single page's decision.

        Returns:
            SyncedAction if synced, None if skipped
        """
        # Map status to action
        action_type = None
        if page.status == "Approved":
            action_type = "approve"
        elif page.status == "Rejected":
            action_type = "reject"
        else:
            # Not a final decision (Reviewing, etc.)
            return None

        # Check if already synced
        if await self._is_already_synced(page.id, action_type):
            return None

        # Record user action
        await self._record_action(
            signal_id=page.signal_id,
            notion_page_id=page.id,
            action=action_type,
            rejection_reason=page.rejection_reason,
            rejection_notes=page.notes,
            thesis_score=page.thesis_score,
        )

        # Update signal status
        new_status = "approved" if action_type == "approve" else "rejected"
        await self.store.update_signal_status(page.signal_id, new_status)

        logger.debug(f"Synced {action_type} for signal {page.signal_id}")

        return SyncedAction(
            signal_id=page.signal_id,
            notion_page_id=page.id,
            action=action_type,
            rejection_reason=page.rejection_reason,
            rejection_notes=page.notes,
            thesis_score=page.thesis_score,
        )

    async def _is_already_synced(self, notion_page_id: str, action: str) -> bool:
        """Check if this action has already been synced."""
        # Query user_actions table
        if not self.store._db:
            return False

        cursor = await self.store._db.execute(
            "SELECT id FROM user_actions WHERE notion_page_id = ? AND action = ?",
            (notion_page_id, action)
        )
        row = await cursor.fetchone()
        return row is not None

    async def _record_action(
        self,
        signal_id: int,
        notion_page_id: str,
        action: str,
        rejection_reason: Optional[str],
        rejection_notes: Optional[str],
        thesis_score: Optional[float],
    ) -> None:
        """Record user action in database."""
        if not self.store._db:
            raise RuntimeError("Store not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self.store._db.execute(
            """
            INSERT INTO user_actions (
                signal_id, notion_page_id, action,
                rejection_reason, rejection_notes, thesis_score_at_action,
                synced_from_notion_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, notion_page_id, action,
                rejection_reason, rejection_notes, thesis_score,
                now, now
            )
        )
        await self.store._db.commit()

    async def run_forever(
        self,
        interval_minutes: int = 5,
        since_minutes: int = 10,
    ) -> None:
        """
        Run poller continuously.

        Args:
            interval_minutes: Time between polls
            since_minutes: Window for modified pages
        """
        import asyncio

        logger.info(f"Starting Notion poller (every {interval_minutes} min)")

        while True:
            try:
                count = await self.poll_and_sync(since_minutes=since_minutes)
                if count > 0:
                    logger.info(f"Poll complete: synced {count} decisions")
            except Exception as e:
                logger.error(f"Poll error: {e}")

            await asyncio.sleep(interval_minutes * 60)

    async def get_action_stats(self) -> Dict[str, int]:
        """Get counts of user actions by type."""
        if not self.store._db:
            return {}

        cursor = await self.store._db.execute(
            "SELECT action, COUNT(*) FROM user_actions GROUP BY action"
        )
        rows = await cursor.fetchall()
        return {action: count for action, count in rows}

    async def get_rejection_reasons(self) -> Dict[str, int]:
        """Get counts of rejection reasons."""
        if not self.store._db:
            return {}

        cursor = await self.store._db.execute(
            """
            SELECT rejection_reason, COUNT(*)
            FROM user_actions
            WHERE action = 'reject' AND rejection_reason IS NOT NULL
            GROUP BY rejection_reason
            """
        )
        rows = await cursor.fetchall()
        return {reason: count for reason, count in rows}
