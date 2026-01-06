"""
Notion Pusher for Discovery Engine

Batch processor that:
1. Fetches pending signals from SignalStore
2. Groups signals by canonical_key (multi-source aggregation)
3. Runs each through VerificationGate to get PushDecision
4. Pushes qualifying signals to Notion (confidence >= 0.4)
5. Marks signals as pushed/rejected in store
6. Handles rate limiting and error recovery

Usage:
    pusher = NotionPusher(
        signal_store=store,
        notion_connector=connector,
        verification_gate=gate
    )

    results = await pusher.process_batch(limit=50)
    print(f"Pushed: {results['pushed']}, Rejected: {results['rejected']}")
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from storage.signal_store import SignalStore, StoredSignal
from connectors.notion_connector_v2 import NotionConnector, ProspectPayload, InvestmentStage
from verification.verification_gate_v2 import VerificationGate, Signal, PushDecision, VerificationStatus

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class AggregatedProspect:
    """Signals aggregated by canonical key"""
    canonical_key: str
    company_name: str
    signals: List[StoredSignal]

    # Aggregated data
    signal_types: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    earliest_detected: Optional[datetime] = None
    latest_detected: Optional[datetime] = None

    # Combined raw data from all signals
    aggregated_data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Aggregate signal metadata"""
        if not self.signals:
            return

        # Collect unique signal types and sources
        self.signal_types = list(set(s.signal_type for s in self.signals))
        self.sources = list(set(s.source_api for s in self.signals))

        # Find earliest and latest detection times
        detected_times = [s.detected_at for s in self.signals]
        self.earliest_detected = min(detected_times)
        self.latest_detected = max(detected_times)

        # Merge raw_data from all signals (latest wins on conflict)
        for sig in sorted(self.signals, key=lambda s: s.detected_at):
            self.aggregated_data.update(sig.raw_data)

    @property
    def is_multi_source(self) -> bool:
        """Does this prospect have signals from multiple sources?"""
        return len(self.sources) >= 2

    @property
    def signal_count(self) -> int:
        """Total number of signals"""
        return len(self.signals)


@dataclass
class PushResult:
    """Result of processing a single prospect"""
    canonical_key: str
    company_name: str
    decision: PushDecision
    confidence: float

    # Notion details (if pushed)
    notion_page_id: Optional[str] = None
    notion_status: Optional[str] = None

    # Processing details
    signals_processed: int = 0
    sources_count: int = 0
    error: Optional[str] = None
    pushed: bool = False

    # Audit trail
    push_reason: str = ""
    verification_status: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BatchResult:
    """Result of processing a batch"""
    total_processed: int = 0
    pushed: int = 0
    rejected: int = 0
    held: int = 0
    errors: int = 0

    # Details
    results: List[PushResult] = field(default_factory=list)
    error_messages: List[str] = field(default_factory=list)

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        """Time taken to process batch"""
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    def summary(self) -> str:
        """Human-readable summary"""
        return (
            f"Batch Results:\n"
            f"  Processed: {self.total_processed}\n"
            f"  Pushed to Notion: {self.pushed}\n"
            f"  Rejected: {self.rejected}\n"
            f"  Held (low confidence): {self.held}\n"
            f"  Errors: {self.errors}\n"
            f"  Duration: {self.duration_seconds:.1f}s"
        )


# =============================================================================
# NOTION PUSHER
# =============================================================================

class NotionPusher:
    """
    Batch processor for pushing verified signals to Notion.

    Features:
    - Multi-source signal aggregation
    - Confidence-based routing (HIGH → Source, MEDIUM → Tracking)
    - Retry logic with exponential backoff
    - Rate limiting for Notion API
    - Comprehensive error handling
    - Audit trail for all decisions
    """

    # Decision thresholds (from CLAUDE.md)
    HIGH_CONFIDENCE_THRESHOLD = 0.7    # → Status: "Source"
    MEDIUM_CONFIDENCE_THRESHOLD = 0.4  # → Status: "Tracking"

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 2.0  # seconds

    def __init__(
        self,
        signal_store: SignalStore,
        notion_connector: NotionConnector,
        verification_gate: Optional[VerificationGate] = None,
        dry_run: bool = False,
    ):
        """
        Initialize NotionPusher.

        Args:
            signal_store: SignalStore instance for reading/updating signals
            notion_connector: NotionConnector instance for Notion API
            verification_gate: Optional VerificationGate (creates default if None)
            dry_run: If True, don't actually push to Notion or update store
        """
        self.store = signal_store
        self.notion = notion_connector
        self.gate = verification_gate or VerificationGate(
            strict_mode=False,
            auto_push_status="Source",
            needs_review_status="Tracking"
        )
        self.dry_run = dry_run

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    async def process_batch(
        self,
        limit: Optional[int] = None,
        signal_type: Optional[str] = None,
    ) -> BatchResult:
        """
        Process a batch of pending signals.

        Args:
            limit: Maximum number of signals to fetch (None = all)
            signal_type: Filter by signal type (None = all types)

        Returns:
            BatchResult with summary and details
        """
        result = BatchResult()

        try:
            # 1. Fetch pending signals
            logger.info(f"Fetching pending signals (limit={limit}, type={signal_type})...")
            pending = await self.store.get_pending_signals(
                limit=limit,
                signal_type=signal_type
            )

            if not pending:
                logger.info("No pending signals to process")
                result.completed_at = datetime.now(timezone.utc)
                return result

            logger.info(f"Found {len(pending)} pending signals")

            # 2. Group by canonical key
            prospects = self._group_by_canonical_key(pending)
            logger.info(f"Grouped into {len(prospects)} unique prospects")

            # 3. Process each prospect
            for prospect in prospects:
                try:
                    push_result = await self._process_prospect(prospect)
                    result.results.append(push_result)
                    result.total_processed += 1

                    # Update counters
                    if push_result.pushed:
                        result.pushed += 1
                    elif push_result.decision == PushDecision.REJECT:
                        result.rejected += 1
                    elif push_result.decision == PushDecision.HOLD:
                        result.held += 1

                    if push_result.error:
                        result.errors += 1
                        result.error_messages.append(
                            f"{prospect.canonical_key}: {push_result.error}"
                        )

                except Exception as e:
                    logger.error(f"Error processing prospect {prospect.canonical_key}: {e}")
                    result.errors += 1
                    result.error_messages.append(f"{prospect.canonical_key}: {str(e)}")

        except Exception as e:
            logger.error(f"Batch processing error: {e}")
            result.error_messages.append(f"Batch error: {str(e)}")

        finally:
            result.completed_at = datetime.now(timezone.utc)

        logger.info(f"\n{result.summary()}")
        return result

    async def process_single_prospect(
        self,
        canonical_key: str
    ) -> PushResult:
        """
        Process all signals for a single prospect by canonical key.

        Useful for reprocessing or debugging specific prospects.
        """
        # Get all signals for this prospect
        signals = await self.store.get_signals_for_company(canonical_key)

        if not signals:
            return PushResult(
                canonical_key=canonical_key,
                company_name="Unknown",
                decision=PushDecision.REJECT,
                confidence=0.0,
                error="No signals found for canonical key"
            )

        # Group into prospect
        prospect = AggregatedProspect(
            canonical_key=canonical_key,
            company_name=signals[0].company_name or "Unknown",
            signals=signals
        )

        return await self._process_prospect(prospect)

    # =========================================================================
    # SIGNAL AGGREGATION
    # =========================================================================

    def _group_by_canonical_key(
        self,
        signals: List[StoredSignal]
    ) -> List[AggregatedProspect]:
        """
        Group signals by canonical key.

        Multiple signals for same canonical_key = multi-source verification.
        """
        grouped: Dict[str, List[StoredSignal]] = defaultdict(list)

        for signal in signals:
            grouped[signal.canonical_key].append(signal)

        prospects = []
        for canonical_key, sigs in grouped.items():
            # Use company name from most confident signal
            company_name = max(sigs, key=lambda s: s.confidence).company_name or "Unknown"

            prospect = AggregatedProspect(
                canonical_key=canonical_key,
                company_name=company_name,
                signals=sigs
            )
            prospects.append(prospect)

        return prospects

    # =========================================================================
    # PROSPECT PROCESSING
    # =========================================================================

    async def _process_prospect(
        self,
        prospect: AggregatedProspect
    ) -> PushResult:
        """
        Process a single aggregated prospect.

        Steps:
        1. Convert to VerificationGate signals
        2. Run through verification gate
        3. Make push decision based on confidence
        4. Push to Notion if qualifying
        5. Update signal processing status
        """
        logger.info(f"Processing: {prospect.company_name} ({prospect.canonical_key})")
        logger.info(f"  Signals: {prospect.signal_count} from {len(prospect.sources)} sources")

        # Convert to verification signals
        verification_signals = self._convert_to_verification_signals(prospect)

        # Run through verification gate
        verification_result = self.gate.evaluate(verification_signals)

        logger.info(f"  Verification: {verification_result.decision.value} "
                   f"(confidence: {verification_result.confidence_score:.2f})")

        # Make push decision
        push_result = PushResult(
            canonical_key=prospect.canonical_key,
            company_name=prospect.company_name,
            decision=verification_result.decision,
            confidence=verification_result.confidence_score,
            signals_processed=prospect.signal_count,
            sources_count=len(prospect.sources),
            push_reason=verification_result.reason,
            verification_status=verification_result.verification_status.value
        )

        # Handle decision
        try:
            if verification_result.decision == PushDecision.REJECT:
                # Reject (hard kill or insufficient evidence)
                await self._mark_signals_rejected(
                    prospect.signals,
                    reason=verification_result.reason,
                    metadata=verification_result.confidence_breakdown
                )

            elif verification_result.decision == PushDecision.HOLD:
                # Hold (low confidence - wait for more signals)
                logger.info(f"  Holding {prospect.company_name} (low confidence)")
                # Don't mark as rejected - keep pending for future batches

            elif verification_result.decision in [PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW]:
                # Push to Notion
                push_result = await self._push_to_notion(
                    prospect,
                    verification_result,
                    push_result
                )

        except Exception as e:
            logger.error(f"Error processing {prospect.canonical_key}: {e}")
            push_result.error = str(e)

        return push_result

    def _convert_to_verification_signals(
        self,
        prospect: AggregatedProspect
    ) -> List[Signal]:
        """Convert StoredSignals to VerificationGate Signals"""
        verification_signals = []

        for stored_signal in prospect.signals:
            signal = Signal(
                id=str(stored_signal.id),
                signal_type=stored_signal.signal_type,
                confidence=stored_signal.confidence,
                source_api=stored_signal.source_api,
                detected_at=stored_signal.detected_at,
                raw_data=stored_signal.raw_data,
                verified_by_sources=[stored_signal.source_api]
            )
            verification_signals.append(signal)

        return verification_signals

    # =========================================================================
    # NOTION PUSH
    # =========================================================================

    async def _push_to_notion(
        self,
        prospect: AggregatedProspect,
        verification_result,
        push_result: PushResult
    ) -> PushResult:
        """
        Push prospect to Notion with retry logic.

        Maps verification decision to Notion status:
        - AUTO_PUSH → "Source" (high confidence)
        - NEEDS_REVIEW → "Tracking" (medium confidence)
        """
        # Build ProspectPayload
        payload = self._build_prospect_payload(prospect, verification_result)

        logger.info(f"  Pushing to Notion: {payload.company_name} → {payload.status}")

        # Push with retry
        if not self.dry_run:
            notion_result = await self._push_with_retry(payload)

            if notion_result:
                push_result.pushed = True
                push_result.notion_page_id = notion_result.get("page_id")
                push_result.notion_status = payload.status

                # Mark signals as pushed
                await self._mark_signals_pushed(
                    prospect.signals,
                    notion_page_id=notion_result["page_id"],
                    metadata={
                        "confidence": verification_result.confidence_score,
                        "status": payload.status,
                        "decision": verification_result.decision.value,
                        "verification_status": verification_result.verification_status.value
                    }
                )

                logger.info(f"  ✓ Pushed to Notion: {notion_result['page_id']} "
                           f"({notion_result['status']})")
        else:
            logger.info(f"  [DRY RUN] Would push: {payload.company_name} → {payload.status}")
            push_result.pushed = True  # For dry run stats

        return push_result

    def _build_prospect_payload(
        self,
        prospect: AggregatedProspect,
        verification_result
    ) -> ProspectPayload:
        """Build ProspectPayload from aggregated prospect"""

        # Extract data from aggregated signals
        data = prospect.aggregated_data

        # Determine investment stage (default to Pre-Seed)
        stage = InvestmentStage.PRE_SEED
        if "stage" in data:
            stage_str = data["stage"]
            try:
                stage = InvestmentStage(stage_str)
            except ValueError:
                logger.warning(f"Unknown stage '{stage_str}', defaulting to Pre-Seed")

        # Build payload
        payload = ProspectPayload(
            discovery_id=f"disc-{prospect.canonical_key}",
            company_name=prospect.company_name,
            canonical_key=prospect.canonical_key,
            stage=stage,
            status=verification_result.suggested_status,

            # Identity
            website=data.get("website", ""),

            # Discovery fields
            confidence_score=verification_result.confidence_score,
            signal_types=prospect.signal_types,
            why_now=self._generate_why_now(prospect, verification_result),

            # Optional enrichment
            short_description=data.get("description", ""),
            founder_name=data.get("founder_name", ""),
            founder_linkedin=data.get("founder_linkedin", ""),
            location=data.get("location", ""),
            target_raise=data.get("target_raise", ""),

            # External refs for canonical key
            external_refs=data.get("external_refs", {})
        )

        return payload

    def _generate_why_now(
        self,
        prospect: AggregatedProspect,
        verification_result
    ) -> str:
        """Generate 'Why Now' summary for Notion"""
        signal_types = ", ".join(prospect.signal_types)
        sources = len(prospect.sources)
        confidence = verification_result.confidence_score

        return (
            f"Detected via {signal_types} from {sources} source(s). "
            f"Confidence: {confidence:.0%}. "
            f"Latest signal: {prospect.latest_detected.strftime('%Y-%m-%d')}."
        )

    async def _push_with_retry(
        self,
        payload: ProspectPayload,
        max_retries: int = MAX_RETRIES
    ) -> Optional[Dict[str, Any]]:
        """
        Push to Notion with exponential backoff retry.

        Returns Notion result or None on failure.
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                result = await self.notion.upsert_prospect(payload)
                return result

            except Exception as e:
                last_error = e

                if attempt < max_retries - 1:
                    delay = self.RETRY_DELAY_BASE ** (attempt + 1)
                    logger.warning(
                        f"Push failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Push failed after {max_retries} attempts: {e}"
                    )

        return None

    # =========================================================================
    # SIGNAL STATUS UPDATES
    # =========================================================================

    async def _mark_signals_pushed(
        self,
        signals: List[StoredSignal],
        notion_page_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Mark all signals as pushed to Notion"""
        if self.dry_run:
            return

        for signal in signals:
            try:
                await self.store.mark_pushed(
                    signal_id=signal.id,
                    notion_page_id=notion_page_id,
                    metadata=metadata
                )
            except Exception as e:
                logger.error(f"Error marking signal {signal.id} as pushed: {e}")

    async def _mark_signals_rejected(
        self,
        signals: List[StoredSignal],
        reason: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Mark all signals as rejected"""
        if self.dry_run:
            return

        for signal in signals:
            try:
                await self.store.mark_rejected(
                    signal_id=signal.id,
                    reason=reason,
                    metadata=metadata
                )
            except Exception as e:
                logger.error(f"Error marking signal {signal.id} as rejected: {e}")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def run_batch_push(
    db_path: str = "signals.db",
    limit: Optional[int] = None,
    dry_run: bool = False
) -> BatchResult:
    """
    Convenience function to run a batch push from environment config.

    Args:
        db_path: Path to signal database
        limit: Max signals to process
        dry_run: If True, don't actually push

    Returns:
        BatchResult
    """
    from storage.signal_store import SignalStore
    from connectors.notion_connector_v2 import create_connector_from_env
    from verification.verification_gate_v2 import VerificationGate

    # Initialize components
    store = SignalStore(db_path)
    await store.initialize()

    try:
        notion = create_connector_from_env()
        gate = VerificationGate(strict_mode=False)

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=notion,
            verification_gate=gate,
            dry_run=dry_run
        )

        result = await pusher.process_batch(limit=limit)
        return result

    finally:
        await store.close()


# =============================================================================
# CLI
# =============================================================================

async def main():
    """CLI entry point"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Push verified signals to Notion")
    parser.add_argument("--db", default="signals.db", help="Signal database path")
    parser.add_argument("--limit", type=int, help="Max signals to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually push")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Run batch
    result = await run_batch_push(
        db_path=args.db,
        limit=args.limit,
        dry_run=args.dry_run
    )

    # Print summary
    print("\n" + "=" * 60)
    print(result.summary())
    print("=" * 60)

    if result.error_messages:
        print("\nErrors:")
        for error in result.error_messages:
            print(f"  - {error}")

    # Exit with error code if failures
    sys.exit(1 if result.errors > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
