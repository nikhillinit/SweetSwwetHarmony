"""
Notion outbox worker for draining queued writes.

Supports event-type routing:
- notion_push: Original Notion upsert flow
- profile_update_requested: Re-profile a monitored URL
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, Optional, TYPE_CHECKING

from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    InvestmentStage,
)
from storage.signal_store import SignalStore
from monitoring.events import EventType, parse_event_payload, ProfileUpdateRequestedPayload

if TYPE_CHECKING:
    from profilers.url_profiler import URLProfiler
    from storage.claim_store import ClaimStore

logger = logging.getLogger(__name__)

CLAIMED_EVENT_TYPES = (
    EventType.NOTION_PUSH.value,
    EventType.PROFILE_UPDATE_REQUESTED.value,
)


class NotionOutboxWorker:
    """Drain queued writes with event-type routing."""

    def __init__(
        self,
        signal_store: SignalStore,
        notion_connector: Optional[NotionConnector] = None,
        profiler: Optional["URLProfiler"] = None,
        claim_store: Optional["ClaimStore"] = None,
        backoff_base_seconds: float = 5.0,
        backoff_max_seconds: float = 300.0,
        warm_intro_notion_mode: str = "off",
    ) -> None:
        self.store = signal_store
        self.notion = notion_connector
        self._profiler = profiler
        self._claim_store = claim_store
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.warm_intro_notion_mode = warm_intro_notion_mode

    async def drain(self, limit: int = 50) -> Dict[str, int]:
        """
        Drain pending outbox entries with event-type routing.

        Routes events to appropriate handlers based on event_type field.
        Uses the atomic claim/finalize contract so overlapping drains cannot
        process the same row.
        """
        stats = {
            "processed": 0,
            "sent": 0,
            "failed": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "profile_updates": 0,
        }

        entries = []
        remaining = max(0, limit)
        for event_type in CLAIMED_EVENT_TYPES:
            if remaining <= 0:
                break
            claimed = await self.store.claim_due_outbox(
                event_type=event_type,
                limit=remaining,
            )
            entries.extend(claimed)
            remaining -= len(claimed)

        if not entries:
            return stats

        for entry in entries:
            stats["processed"] += 1
            outbox_id = entry["id"]
            payload = entry["payload"]

            try:
                # Check for event_type routing
                event_type = (
                    payload.get("event_type")
                    or entry.get("event_type")
                    or EventType.NOTION_PUSH.value
                )

                if event_type == EventType.PROFILE_UPDATE_REQUESTED.value:
                    # Handle profile update request
                    await self._handle_profile_update(payload)
                    await self.store.finalize_outbox(outbox_id, success=True)
                    stats["sent"] += 1
                    stats["profile_updates"] += 1

                elif event_type == EventType.NOTION_PUSH.value or event_type is None:
                    # Legacy Notion push flow
                    if self.notion is None:
                        raise RuntimeError("NotionConnector not configured")

                    prospect_payload = self._build_prospect_payload(payload.get("prospect", {}))

                    # Phase A: warm intro mode branching
                    await self._apply_warm_intro_mode(prospect_payload, outbox_id)

                    result = await self.notion.upsert_prospect(prospect_payload)

                    result_status = result.get("status")
                    if result_status in stats:
                        stats[result_status] += 1

                    notion_page_id = result.get("page_id")
                    metadata = payload.get("metadata") or {}

                    for signal_id in payload.get("signal_ids", []):
                        await self.store.mark_pushed(
                            signal_id=signal_id,
                            notion_page_id=notion_page_id,
                            metadata=metadata,
                        )

                    await self.store.finalize_outbox(outbox_id, success=True)
                    stats["sent"] += 1

                else:
                    logger.warning(f"Unknown event_type: {event_type}, skipping")
                    stats["skipped"] += 1
                    await self.store.finalize_outbox(outbox_id, success=True)

            except Exception as exc:
                stats["failed"] += 1
                backoff_seconds = self._compute_backoff(entry.get("attempts", 0))
                await self.store.finalize_outbox(
                    outbox_id,
                    success=False,
                    error=str(exc),
                    backoff_seconds=backoff_seconds,
                )
                logger.warning(f"Outbox entry {outbox_id} failed: {exc}")

        return stats

    async def _handle_profile_update(self, payload: Dict[str, Any]) -> None:
        """
        Handle a profile_update_requested event.

        Re-profiles the URL and updates claims in ClaimStore.
        """
        # Parse the typed payload
        event = ProfileUpdateRequestedPayload(**payload)

        logger.info(f"Processing profile update for {event.canonical_key} (trigger: {event.trigger})")

        # Get or create profiler
        if self._profiler is None:
            from profilers.url_profiler import URLProfiler
            self._profiler = URLProfiler(signal_store=self.store)

        # Get or create claim store
        if self._claim_store is None:
            from storage.claim_store import ClaimStore
            self._claim_store = ClaimStore(self.store)

        # Run the profiler
        url = event.url or f"https://{event.canonical_key.replace('domain:', '')}"

        try:
            profile = await self._profiler.profile(url, force_refresh=True)

            logger.info(
                f"Profile update complete for {event.canonical_key}: "
                f"{len(profile.claims)} claims extracted"
            )

        except Exception as e:
            logger.error(f"Profile update failed for {event.canonical_key}: {e}")
            raise

    async def _apply_warm_intro_mode(
        self, payload: ProspectPayload, outbox_id: int
    ) -> None:
        """Apply warm_intro_notion_mode to indicators before Notion push.

        Modes:
        - "off": strip indicators (default)
        - "shadow": log to shadow_log, then strip
        - "live": retain indicators in payload
        """
        if not payload.warm_intro_indicators:
            return

        mode = self.warm_intro_notion_mode

        if mode == "live":
            logger.info(
                "warm_intro.live outbox_id=%d indicators=%d",
                outbox_id, len(payload.warm_intro_indicators),
            )
            return  # Keep indicators in payload

        if mode == "shadow":
            # Log to shadow_log for later analysis
            try:
                computed_value = [
                    ind.model_dump(mode="json")
                    for ind in payload.warm_intro_indicators
                ]
                await self.store.log_shadow_computation(
                    feature_name="warm_intro_indicators",
                    canonical_key=payload.canonical_key,
                    computed_value=computed_value,
                )
                logger.info(
                    "warm_intro.shadow outbox_id=%d indicators=%d logged",
                    outbox_id, len(payload.warm_intro_indicators),
                )
            except Exception as e:
                logger.warning(
                    "warm_intro.shadow logging failed for outbox_id=%d: %s",
                    outbox_id, e,
                )

        # Both "off" and "shadow" strip indicators before push
        payload.warm_intro_indicators = []

    def _build_prospect_payload(self, data: Dict[str, Any]) -> ProspectPayload:
        # Ensure required fields have defaults for backward compat with legacy outbox rows
        defaults: Dict[str, Any] = {
            "discovery_id": "",
            "company_name": "",
            "stage": InvestmentStage.PRE_SEED.value,
        }
        validated_data = {**defaults, **data}
        return ProspectPayload.model_validate(validated_data)

    def _compute_backoff(self, attempts: int) -> float:
        attempt = max(1, attempts + 1)
        base = self.backoff_base_seconds * (2 ** (attempt - 1))
        base = min(base, self.backoff_max_seconds)
        return base + random.uniform(0, 0.25)
