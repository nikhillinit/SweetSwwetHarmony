"""
Profile Update Worker for Monitoring Subsystem

Consumes profile_update_requested events from the outbox and triggers
URLProfiler to re-profile changed websites.

Per Spec v2.4 Section 9.6: Uses atomic claim (BEGIN IMMEDIATE) in short
transaction, processes work outside transaction, then finalizes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from storage.claim_store import ClaimStore
    from profilers.url_profiler import URLProfiler

from monitoring.events import EventType, ProfileUpdateRequestedPayload

logger = logging.getLogger(__name__)


class ProfileUpdateWorker:
    """
    Worker that processes profile_update_requested events.

    Claims events from the outbox, calls URLProfiler.profile() to re-profile
    the changed website, and updates ClaimStore with functional_profile.

    Usage:
        worker = ProfileUpdateWorker(signal_store, claim_store, profiler)
        processed = await worker.process_batch(limit=10)
    """

    def __init__(
        self,
        signal_store: "SignalStore",
        claim_store: Optional["ClaimStore"] = None,
        profiler: Optional["URLProfiler"] = None,
        stale_ttl_minutes: int = 30,
    ):
        """
        Initialize worker.

        Args:
            signal_store: SignalStore for outbox operations
            claim_store: ClaimStore for updating functional_profile
            profiler: URLProfiler for re-profiling
            stale_ttl_minutes: TTL for stale processing entries
        """
        self.signal_store = signal_store
        self.claim_store = claim_store
        self.profiler = profiler
        self.stale_ttl_minutes = stale_ttl_minutes

    async def process_batch(
        self,
        limit: int = 10,
    ) -> Dict[str, int]:
        """
        Process a batch of profile update requests.

        Args:
            limit: Maximum events to process

        Returns:
            Dict with processing stats: {claimed, succeeded, failed}
        """
        stats = {"claimed": 0, "succeeded": 0, "failed": 0}

        # Claim events (atomic, short transaction)
        events = await self.signal_store.claim_due_outbox(
            event_type=EventType.PROFILE_UPDATE_REQUESTED.value,
            limit=limit,
            stale_processing_ttl_minutes=self.stale_ttl_minutes,
        )

        if not events:
            logger.debug("No profile update events to process")
            return stats

        stats["claimed"] = len(events)
        logger.info(f"Claimed {len(events)} profile update events")

        # Process each event (outside transaction)
        for event in events:
            try:
                await self._process_event(event)
                await self.signal_store.finalize_outbox(
                    outbox_id=event["id"],
                    success=True,
                )
                stats["succeeded"] += 1
            except Exception as e:
                logger.error(f"Failed to process event {event['id']}: {e}")
                await self.signal_store.finalize_outbox(
                    outbox_id=event["id"],
                    success=False,
                    error=str(e)[:500],
                    backoff_seconds=60.0,  # 1 minute backoff on failure
                )
                stats["failed"] += 1

        return stats

    async def _process_event(self, event: Dict[str, Any]) -> None:
        """
        Process a single profile update event.

        Args:
            event: Outbox event dict
        """
        payload = ProfileUpdateRequestedPayload(**event["payload"])

        logger.info(
            f"Processing profile update: watch={payload.watch_id}, "
            f"canonical_key={payload.canonical_key}, trigger={payload.trigger}"
        )

        # Skip if no profiler configured
        if not self.profiler:
            logger.warning("No URLProfiler configured, skipping re-profile")
            return

        # Re-profile the URL
        url = payload.url
        if not url:
            logger.warning(f"No URL in payload for watch {payload.watch_id}")
            return

        try:
            profile = await self.profiler.profile(url, force_refresh=True)

            # Update ClaimStore if available
            if self.claim_store and profile.claims:
                await self._update_claims(payload.canonical_key, profile)

            logger.info(
                f"Re-profiled {url}: {len(profile.claims or [])} claims extracted"
            )

        except Exception as e:
            logger.error(f"Failed to profile {url}: {e}")
            raise

    async def _update_claims(
        self,
        canonical_key: str,
        profile: Any,  # CompanyProfile
    ) -> None:
        """
        Update ClaimStore with extracted claims.

        Args:
            canonical_key: Company canonical key
            profile: CompanyProfile from URLProfiler
        """
        if not self.claim_store:
            return

        # Store functional_profile predicate
        try:
            import json
            profile_summary = {
                "canonical_key": profile.canonical_key,
                "source_urls": profile.source_urls,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "claim_count": len(profile.claims or []),
            }

            await self.claim_store.upsert_claim(
                entity_key=canonical_key,
                predicate="functional_profile",
                value=json.dumps(profile_summary),
                confidence=0.8,
                source="monitoring_profile_update",
            )

            # Store individual claims
            for claim in profile.claims or []:
                if hasattr(claim, 'predicate') and hasattr(claim, 'value'):
                    await self.claim_store.upsert_claim(
                        entity_key=canonical_key,
                        predicate=claim.predicate,
                        value=claim.value,
                        confidence=getattr(claim, 'confidence', 0.7),
                        source="monitoring_profile_update",
                    )

        except Exception as e:
            logger.error(f"Failed to update claims for {canonical_key}: {e}")
            # Don't re-raise - claims update is best-effort

    async def run_continuous(
        self,
        batch_size: int = 10,
        poll_interval_seconds: float = 10.0,
        max_iterations: Optional[int] = None,
    ) -> None:
        """
        Run worker continuously, polling for events.

        Args:
            batch_size: Events per batch
            poll_interval_seconds: Time between polls
            max_iterations: Maximum iterations (None = infinite)
        """
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            try:
                stats = await self.process_batch(limit=batch_size)

                if stats["claimed"] > 0:
                    logger.info(
                        f"Batch complete: {stats['succeeded']} succeeded, "
                        f"{stats['failed']} failed"
                    )

            except Exception as e:
                logger.error(f"Error in worker iteration: {e}")

            await asyncio.sleep(poll_interval_seconds)
            iteration += 1


async def run_profile_update_worker(
    signal_store: "SignalStore",
    claim_store: Optional["ClaimStore"] = None,
    profiler: Optional["URLProfiler"] = None,
    batch_size: int = 10,
    poll_interval: float = 10.0,
) -> None:
    """
    Run the profile update worker (convenience function).

    Args:
        signal_store: SignalStore instance
        claim_store: Optional ClaimStore
        profiler: Optional URLProfiler
        batch_size: Events per batch
        poll_interval: Time between polls
    """
    worker = ProfileUpdateWorker(signal_store, claim_store, profiler)
    await worker.run_continuous(
        batch_size=batch_size,
        poll_interval_seconds=poll_interval,
    )
