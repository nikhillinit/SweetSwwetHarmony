"""
Notion outbox worker for draining queued writes.

Supports event-type routing:
- notion_push: Original Notion upsert flow
- profile_update_requested: Re-profile a monitored URL
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

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
    ) -> None:
        self.store = signal_store
        self.notion = notion_connector
        self._profiler = profiler
        self._claim_store = claim_store
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds

    async def drain(self, limit: int = 50) -> Dict[str, int]:
        """
        Drain pending outbox entries with event-type routing.

        Routes events to appropriate handlers based on event_type field.
        Falls back to legacy Notion push if no event_type specified.
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

        entries = await self.store.get_pending_outbox(limit=limit)
        if not entries:
            return stats

        for entry in entries:
            stats["processed"] += 1
            outbox_id = entry["id"]
            payload = entry["payload"]

            try:
                # Check for event_type routing
                event_type = payload.get("event_type")

                if event_type == EventType.PROFILE_UPDATE_REQUESTED.value:
                    # Handle profile update request
                    await self._handle_profile_update(payload)
                    await self.store.mark_outbox_sent(outbox_id)
                    stats["sent"] += 1
                    stats["profile_updates"] += 1

                elif event_type == EventType.NOTION_PUSH.value or event_type is None:
                    # Legacy Notion push flow
                    if self.notion is None:
                        raise RuntimeError("NotionConnector not configured")

                    prospect_payload = self._build_prospect_payload(payload.get("prospect", {}))
                    result = await self.notion.upsert_prospect(prospect_payload)

                    await self.store.mark_outbox_sent(outbox_id)
                    stats["sent"] += 1

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

                else:
                    logger.warning(f"Unknown event_type: {event_type}, skipping")
                    stats["skipped"] += 1
                    await self.store.mark_outbox_sent(outbox_id)

            except Exception as exc:
                stats["failed"] += 1
                backoff_seconds = self._compute_backoff(entry.get("attempts", 0))
                await self.store.mark_outbox_failed(outbox_id, str(exc), backoff_seconds)
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

    def _build_prospect_payload(self, data: Dict[str, Any]) -> ProspectPayload:
        stage_value = data.get("stage")
        try:
            stage = InvestmentStage(stage_value) if stage_value else InvestmentStage.PRE_SEED
        except ValueError:
            stage = InvestmentStage.PRE_SEED

        return ProspectPayload(
            discovery_id=data.get("discovery_id", ""),
            company_name=data.get("company_name", ""),
            canonical_key=data.get("canonical_key", ""),
            stage=stage,
            status=data.get("status"),
            website=data.get("website", ""),
            canonical_key_candidates=data.get("canonical_key_candidates") or [],
            confidence_score=float(data.get("confidence_score", 0.0)),
            signal_types=data.get("signal_types") or [],
            why_now=data.get("why_now", ""),
            short_description=data.get("short_description", ""),
            sector=data.get("sector"),
            proposed_sector=data.get("proposed_sector"),
            taxonomy_status=data.get("taxonomy_status"),
            founder_name=data.get("founder_name", ""),
            founder_linkedin=data.get("founder_linkedin", ""),
            location=data.get("location", ""),
            target_raise=data.get("target_raise", ""),
            external_refs=data.get("external_refs") or {},
            watchlists_matched=data.get("watchlists_matched") or [],
            investor_matches=data.get("investor_matches") or [],
        )

    def _compute_backoff(self, attempts: int) -> float:
        attempt = max(1, attempts + 1)
        base = self.backoff_base_seconds * (2 ** (attempt - 1))
        base = min(base, self.backoff_max_seconds)
        return base + random.uniform(0, 0.25)
