"""
Notion Pusher - Batch processor for pushing signals to Notion CRM.

Implements confidence-based routing:
- HIGH confidence (0.7+) + multi-source → "Source" status (auto-push)
- MEDIUM confidence (0.4-0.7) → "Tracking" status (needs review)
- LOW confidence (<0.4) → Hold for batch review (don't push)
- Hard kill signals → Reject entirely

Usage:
    from workflows.notion_pusher import NotionPusher

    pusher = NotionPusher(
        notion_connector=connector,
        signal_store=store,
        verification_gate=gate,
    )

    stats = await pusher.process_batch(limit=50, dry_run=True)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from connectors.notion_connector_v2 import NotionConnector, ProspectPayload, InvestmentStage
from storage.signal_store import SignalStore, StoredSignal
from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    PushDecision,
    VerificationResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# STATS
# =============================================================================

@dataclass
class PushStats:
    """Statistics from a push batch."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    dry_run: bool = True

    # Input
    signals_retrieved: int = 0
    entities_evaluated: int = 0

    # Verification decisions
    auto_push: int = 0
    needs_review: int = 0
    held: int = 0
    rejected: int = 0

    # Notion actions
    prospects_created: int = 0
    prospects_updated: int = 0
    prospects_skipped: int = 0

    # Errors
    errors: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "dry_run": self.dry_run,
            "signals_retrieved": self.signals_retrieved,
            "entities_evaluated": self.entities_evaluated,
            "auto_push": self.auto_push,
            "needs_review": self.needs_review,
            "held": self.held,
            "rejected": self.rejected,
            "prospects_created": self.prospects_created,
            "prospects_updated": self.prospects_updated,
            "prospects_skipped": self.prospects_skipped,
            "errors": self.errors[:10],
            "error_count": len(self.errors),
        }


# =============================================================================
# PUSHER
# =============================================================================

class NotionPusher:
    """
    Batch processor for pushing qualified signals to Notion CRM.

    Workflow:
    1. Retrieve pending signals from SignalStore
    2. Group signals by canonical key (entity)
    3. Evaluate each entity through verification gate
    4. Route based on confidence:
       - AUTO_PUSH → Create/update in Notion with "Source" status
       - NEEDS_REVIEW → Create/update with "Tracking" status
       - HOLD → Don't push yet (wait for more signals)
       - REJECT → Don't push (hard kill or insufficient evidence)
    5. Mark processed signals as pushed
    """

    def __init__(
        self,
        notion_connector: NotionConnector,
        signal_store: SignalStore,
        verification_gate: Optional[VerificationGate] = None,
    ):
        self.notion = notion_connector
        self.store = signal_store
        self.gate = verification_gate or VerificationGate()

    async def process_batch(
        self,
        limit: int = 50,
        dry_run: bool = True,
    ) -> PushStats:
        """
        Process a batch of pending signals.

        Args:
            limit: Maximum number of signals to process
            dry_run: If True, don't actually push to Notion

        Returns:
            PushStats with processing results
        """
        stats = PushStats(dry_run=dry_run)

        try:
            # Step 1: Get pending signals
            logger.info(f"Fetching up to {limit} pending signals...")
            pending = await self.store.get_pending_signals(limit=limit)
            stats.signals_retrieved = len(pending)

            if not pending:
                logger.info("No pending signals to process")
                stats.completed_at = datetime.now(timezone.utc)
                return stats

            logger.info(f"Retrieved {len(pending)} pending signals")

            # Step 2: Group by canonical key
            by_key: Dict[str, List[StoredSignal]] = {}
            for stored_signal in pending:
                key = stored_signal.canonical_key
                if key not in by_key:
                    by_key[key] = []
                by_key[key].append(stored_signal)

            stats.entities_evaluated = len(by_key)
            logger.info(f"Grouped into {len(by_key)} entities")

            # Step 3: Process each entity
            for canonical_key, stored_signals in by_key.items():
                try:
                    await self._process_entity(
                        canonical_key=canonical_key,
                        stored_signals=stored_signals,
                        stats=stats,
                        dry_run=dry_run,
                    )
                except Exception as e:
                    error_msg = f"Error processing {canonical_key}: {e}"
                    logger.error(error_msg)
                    stats.errors.append(error_msg)

            stats.completed_at = datetime.now(timezone.utc)
            logger.info(
                f"Batch complete: {stats.prospects_created} created, "
                f"{stats.prospects_updated} updated, "
                f"{stats.held} held"
            )

        except Exception as e:
            logger.exception("Batch processing failed")
            stats.errors.append(f"Batch error: {str(e)}")
            stats.completed_at = datetime.now(timezone.utc)

        return stats

    async def _process_entity(
        self,
        canonical_key: str,
        stored_signals: List[StoredSignal],
        stats: PushStats,
        dry_run: bool,
    ) -> None:
        """Process a single entity (group of signals with same canonical key)."""

        # Convert StoredSignal to Signal for verification
        signals = [
            Signal(
                id=f"signal_{s.id}",
                signal_type=s.signal_type,
                confidence=s.confidence,
                source_api=s.source_api,
                detected_at=s.detected_at,
                raw_data=s.raw_data,
            )
            for s in stored_signals
        ]

        # Evaluate through verification gate
        verification = self.gate.evaluate(signals)

        # Track decision
        if verification.decision == PushDecision.AUTO_PUSH:
            stats.auto_push += 1
            logger.info(f"AUTO_PUSH: {canonical_key} (confidence: {verification.confidence_score:.2f})")
        elif verification.decision == PushDecision.NEEDS_REVIEW:
            stats.needs_review += 1
            logger.info(f"NEEDS_REVIEW: {canonical_key} (confidence: {verification.confidence_score:.2f})")
        elif verification.decision == PushDecision.HOLD:
            stats.held += 1
            logger.debug(f"HOLD: {canonical_key} (confidence: {verification.confidence_score:.2f})")
            return  # Don't push
        elif verification.decision == PushDecision.REJECT:
            stats.rejected += 1
            logger.debug(f"REJECT: {canonical_key} ({verification.reason})")
            return  # Don't push

        # Push to Notion if not dry run
        if not dry_run:
            result = await self._push_to_notion(
                canonical_key=canonical_key,
                signals=signals,
                verification=verification,
            )

            if result["status"] == "created":
                stats.prospects_created += 1
            elif result["status"] == "updated":
                stats.prospects_updated += 1
            else:
                stats.prospects_skipped += 1

            # Mark signals as pushed
            if result.get("page_id"):
                for stored_signal in stored_signals:
                    await self.store.mark_pushed(
                        stored_signal.id,
                        result["page_id"],
                    )

    async def _push_to_notion(
        self,
        canonical_key: str,
        signals: List[Signal],
        verification: VerificationResult,
    ) -> Dict[str, Any]:
        """Push a qualified prospect to Notion."""

        # Extract company info from signals
        company_name = "Unknown"
        company_domain = ""
        location = ""
        signal_types = list(set(s.signal_type for s in signals))

        for signal in signals:
            raw = signal.raw_data or {}

            # Get company name
            if raw.get("company_name") and (not company_name or company_name == "Unknown"):
                company_name = raw["company_name"]

            # Get domain
            if raw.get("company_domain") and not company_domain:
                company_domain = raw["company_domain"]
            elif raw.get("domain") and not company_domain:
                company_domain = raw["domain"]

            # Get location
            if raw.get("location") and not location:
                location = raw["location"]
            elif raw.get("locations") and not location:
                locations = raw["locations"]
                if isinstance(locations, list) and locations:
                    location = locations[0]

        # Determine investment stage from signals
        stage = self._infer_stage(signals)

        # Build prospect payload
        prospect = ProspectPayload(
            discovery_id=f"disc_{canonical_key.replace(':', '_').replace('.', '_')}",
            company_name=company_name,
            canonical_key=canonical_key,
            stage=stage,
            status=verification.suggested_status or "Source",
            website=f"https://{company_domain}" if company_domain else "",
            confidence_score=verification.confidence_score,
            signal_types=signal_types,
            why_now=verification.reason,
            location=location,
        )

        try:
            return await self.notion.upsert_prospect(prospect)
        except Exception as e:
            logger.error(f"Failed to push {canonical_key} to Notion: {e}")
            return {"status": "error", "reason": str(e)}

    def _infer_stage(self, signals: List[Signal]) -> InvestmentStage:
        """
        Infer investment stage from signals.

        Heuristics:
        - funding_event with amount → look at amount
        - hiring_signal with many positions → likely funded (Seed+)
        - incorporation only → Pre-Seed
        """
        for signal in signals:
            raw = signal.raw_data or {}

            # Check funding signals
            if signal.signal_type == "funding_event":
                amount = raw.get("amount", 0)
                if amount > 10_000_000:
                    return InvestmentStage.SERIES_A
                elif amount > 2_000_000:
                    return InvestmentStage.SEED_PLUS
                elif amount > 0:
                    return InvestmentStage.SEED

            # Check hiring signals
            if signal.signal_type == "hiring_signal":
                positions = raw.get("total_positions", 0)
                if positions >= 20:
                    return InvestmentStage.SEED_PLUS
                elif positions >= 5:
                    return InvestmentStage.SEED

        # Default to Pre-Seed
        return InvestmentStage.PRE_SEED


# =============================================================================
# CONVENIENCE
# =============================================================================

async def run_push_batch(
    db_path: str = "signals.db",
    limit: int = 50,
    dry_run: bool = True,
) -> PushStats:
    """
    Convenience function to run a push batch.

    Usage:
        stats = await run_push_batch(dry_run=True)
        print(stats.to_dict())
    """
    import os
    from connectors.notion_connector_v2 import NotionConnector

    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("NOTION_DATABASE_ID")

    if not api_key or not database_id:
        raise ValueError("NOTION_API_KEY and NOTION_DATABASE_ID must be set")

    notion = NotionConnector(api_key=api_key, database_id=database_id)
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        pusher = NotionPusher(
            notion_connector=notion,
            signal_store=store,
        )
        return await pusher.process_batch(limit=limit, dry_run=dry_run)
    finally:
        await store.close()
