"""
Company Action Handler

Business logic for inbox actions: Track, Pass, Pipeline.
Coordinates between company_state, company_actions, and Notion outbox.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Result of an action execution."""
    success: bool
    canonical_key: str
    action: str
    message: str
    new_status: Optional[str] = None


class CompanyActionHandler:
    """
    Handles all company inbox actions.

    Actions:
    - track: Move from inbox to tracking
    - pass: Move from inbox to passed (with reason)
    - pipeline: Queue for Notion push
    - snooze: Hide until date
    - unsnooze: Restore to inbox
    """

    def __init__(self, store: SignalStore):
        self.store = store

    async def track(
        self,
        canonical_key: str,
        actor: Optional[str] = None,
    ) -> ActionResult:
        """
        Move company from inbox to tracking.

        Args:
            canonical_key: The company's canonical key
            actor: Who performed the action

        Returns:
            ActionResult with success status
        """
        try:
            # Update state
            await self.store.upsert_company_state(
                canonical_key=canonical_key,
                status="tracking",
                owner=actor,
            )

            # Log action
            await self.store.log_company_action(
                canonical_key=canonical_key,
                action="track",
                actor=actor,
            )

            logger.info(f"Company {canonical_key} moved to tracking by {actor}")

            return ActionResult(
                success=True,
                canonical_key=canonical_key,
                action="track",
                message="Company moved to tracking",
                new_status="tracking",
            )
        except Exception as e:
            logger.error(f"Failed to track {canonical_key}: {e}")
            return ActionResult(
                success=False,
                canonical_key=canonical_key,
                action="track",
                message=f"Failed to track: {e}",
            )

    async def pass_company(
        self,
        canonical_key: str,
        reason: str,
        actor: Optional[str] = None,
    ) -> ActionResult:
        """
        Pass on a company (move to passed status).

        Args:
            canonical_key: The company's canonical key
            reason: Why the company was passed
            actor: Who performed the action

        Returns:
            ActionResult with success status
        """
        try:
            # Update state
            await self.store.upsert_company_state(
                canonical_key=canonical_key,
                status="passed",
                owner=actor,
                pass_reason=reason,
            )

            # Log action with reason
            await self.store.log_company_action(
                canonical_key=canonical_key,
                action="pass",
                actor=actor,
                metadata={"reason": reason},
            )

            # Suppress future signals for this company
            # (Mark any pending signals as rejected)
            await self._suppress_signals(canonical_key, reason)

            logger.info(f"Company {canonical_key} passed by {actor}: {reason}")

            return ActionResult(
                success=True,
                canonical_key=canonical_key,
                action="pass",
                message=f"Company passed: {reason}",
                new_status="passed",
            )
        except Exception as e:
            logger.error(f"Failed to pass {canonical_key}: {e}")
            return ActionResult(
                success=False,
                canonical_key=canonical_key,
                action="pass",
                message=f"Failed to pass: {e}",
            )

    async def add_to_pipeline(
        self,
        canonical_key: str,
        actor: Optional[str] = None,
    ) -> ActionResult:
        """
        Queue company for Notion pipeline push.

        Args:
            canonical_key: The company's canonical key
            actor: Who performed the action

        Returns:
            ActionResult with success status
        """
        try:
            # Update state
            await self.store.upsert_company_state(
                canonical_key=canonical_key,
                status="pipeline_requested",
                owner=actor,
            )

            # Log action
            await self.store.log_company_action(
                canonical_key=canonical_key,
                action="pipeline",
                actor=actor,
            )

            # Get company data for outbox payload
            company = await self.store.get_company_by_key(canonical_key)
            if not company:
                return ActionResult(
                    success=False,
                    canonical_key=canonical_key,
                    action="pipeline",
                    message="Company not found",
                )

            # Queue for Notion outbox
            payload = self._build_notion_payload(company, actor)
            await self._enqueue_notion_push(canonical_key, payload, actor)

            logger.info(f"Company {canonical_key} queued for pipeline by {actor}")

            return ActionResult(
                success=True,
                canonical_key=canonical_key,
                action="pipeline",
                message="Company queued for Notion pipeline",
                new_status="pipeline_requested",
            )
        except Exception as e:
            logger.error(f"Failed to add {canonical_key} to pipeline: {e}")
            return ActionResult(
                success=False,
                canonical_key=canonical_key,
                action="pipeline",
                message=f"Failed to add to pipeline: {e}",
            )

    async def snooze(
        self,
        canonical_key: str,
        until: datetime,
        actor: Optional[str] = None,
    ) -> ActionResult:
        """
        Snooze a company until a specific date.

        Args:
            canonical_key: The company's canonical key
            until: When to unsnooze
            actor: Who performed the action

        Returns:
            ActionResult with success status
        """
        try:
            await self.store.upsert_company_state(
                canonical_key=canonical_key,
                status="inbox",  # Keep in inbox but snoozed
                snoozed_until=until,
            )

            await self.store.log_company_action(
                canonical_key=canonical_key,
                action="snooze",
                actor=actor,
                metadata={"until": until.isoformat()},
            )

            logger.info(f"Company {canonical_key} snoozed until {until} by {actor}")

            return ActionResult(
                success=True,
                canonical_key=canonical_key,
                action="snooze",
                message=f"Company snoozed until {until.date()}",
            )
        except Exception as e:
            logger.error(f"Failed to snooze {canonical_key}: {e}")
            return ActionResult(
                success=False,
                canonical_key=canonical_key,
                action="snooze",
                message=f"Failed to snooze: {e}",
            )

    async def unsnooze(
        self,
        canonical_key: str,
        actor: Optional[str] = None,
    ) -> ActionResult:
        """
        Unsnooze a company (restore to inbox).

        Args:
            canonical_key: The company's canonical key
            actor: Who performed the action

        Returns:
            ActionResult with success status
        """
        try:
            await self.store.upsert_company_state(
                canonical_key=canonical_key,
                status="inbox",
                snoozed_until=None,
            )

            await self.store.log_company_action(
                canonical_key=canonical_key,
                action="unsnooze",
                actor=actor,
            )

            logger.info(f"Company {canonical_key} unsnoozed by {actor}")

            return ActionResult(
                success=True,
                canonical_key=canonical_key,
                action="unsnooze",
                message="Company restored to inbox",
                new_status="inbox",
            )
        except Exception as e:
            logger.error(f"Failed to unsnooze {canonical_key}: {e}")
            return ActionResult(
                success=False,
                canonical_key=canonical_key,
                action="unsnooze",
                message=f"Failed to unsnooze: {e}",
            )

    async def _suppress_signals(self, canonical_key: str, reason: str) -> None:
        """Suppress (reject) all pending signals for a passed company."""
        # This would mark pending signals as rejected
        # For now, the suppression_cache handles this at pipeline level
        pass

    def _build_notion_payload(
        self,
        company: Dict[str, Any],
        actor: Optional[str],
    ) -> Dict[str, Any]:
        """Build payload for Notion outbox."""
        return {
            "canonical_key": company.get("canonical_key"),
            "company_name": company.get("company_name"),
            "website": company.get("website"),
            "description": company.get("one_liner"),
            "confidence": company.get("max_confidence"),
            "source": company.get("sources"),
            "verticals": [company.get("vertical")] if company.get("vertical") else [],
            "created_by": actor,
        }

    async def _enqueue_notion_push(
        self,
        canonical_key: str,
        payload: Dict[str, Any],
        actor: Optional[str],
    ) -> None:
        """Add to Notion outbox for async processing."""
        # Generate idempotency key
        idempotency_key = f"pipeline:{canonical_key}:{datetime.now(timezone.utc).isoformat()}"

        now = datetime.now(timezone.utc).isoformat()

        # Use the existing outbox infrastructure
        if not self.store._db:
            raise RuntimeError("Database not initialized")

        await self.store._db.execute(
            """
            INSERT INTO notion_outbox (idempotency_key, payload_json, status, attempts, created_at, updated_at, created_by)
            VALUES (?, ?, 'pending', 0, ?, ?, ?)
            """,
            (idempotency_key, json.dumps(payload), now, now, actor),
        )
        await self.store._db.commit()
