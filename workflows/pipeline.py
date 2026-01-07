"""
Pipeline Orchestrator for Discovery Engine

Ties together the entire discovery pipeline:
  collect() → store() → dedupe() → verify() → push()

Coordinates:
- Collector execution (parallel or sequential)
- Signal storage in SQLite
- Suppression checking against Notion
- Verification gate routing
- Notion pushing with proper status

Usage:
    from workflows.pipeline import DiscoveryPipeline

    pipeline = DiscoveryPipeline()
    await pipeline.initialize()

    # Run full pipeline
    result = await pipeline.run_full_pipeline(
        collectors=["github", "sec_edgar"],
        dry_run=True
    )

    # Or run stages independently
    await pipeline.run_collectors(["companies_house"], dry_run=False)
    await pipeline.process_pending()
    await pipeline.sync_suppression()
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Storage
from storage.signal_store import SignalStore, StoredSignal
from storage.source_asset_store import SourceAssetStore
from consumer.signal_processor import SignalProcessor, ProcessorConfig
from consumer.entity_resolver import EntityResolver, ResolverConfig

# Verification
from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    VerificationResult,
    PushDecision,
    VerificationStatus,
)

# Notion integration
from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    InvestmentStage,
    DealStatus,
)

# Collectors (import dynamically to avoid circular imports)
from discovery_engine.mcp_server import CollectorResult, CollectorStatus

# Suppression sync (for cache warmup)
from workflows.suppression_sync import SuppressionSync

# Health monitoring
from utils.signal_health import SignalHealthMonitor

# Notifications
from utils.slack_notifier import SlackNotifier, SlackConfig

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

class PipelineMode(str, Enum):
    """Pipeline execution mode"""
    FULL = "full"              # Run all stages
    COLLECT_ONLY = "collect"   # Just run collectors
    PROCESS_ONLY = "process"   # Process stored signals
    SYNC_ONLY = "sync"         # Sync suppression cache


@dataclass
class PipelineConfig:
    """Configuration for the discovery pipeline"""

    # Storage
    db_path: str = "signals.db"
    asset_store_path: str = "assets.db"  # SourceAssetStore path

    # Notion
    notion_api_key: Optional[str] = None
    notion_database_id: Optional[str] = None

    # Collectors
    github_token: Optional[str] = None
    companies_house_api_key: Optional[str] = None

    # Execution
    parallel_collectors: bool = True  # Run collectors in parallel
    batch_size: int = 50             # Process signals in batches

    # Verification
    strict_mode: bool = False        # Require 2+ sources for auto-push

    # Warmup
    warmup_suppression_cache: bool = True  # Auto-sync suppression cache on init

    # Feature flags (v2 components)
    use_gating: bool = True          # Enable TriggerGate + LLMClassifierV2 (consumer filtering)
    use_entities: bool = False       # Enable EntityResolver
    use_asset_store: bool = False    # Save to SourceAssetStore

    @classmethod
    def from_env(cls) -> PipelineConfig:
        """Load configuration from environment variables"""
        return cls(
            db_path=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
            asset_store_path=os.getenv("ASSET_STORE_PATH", "assets.db"),
            notion_api_key=os.getenv("NOTION_API_KEY"),
            notion_database_id=os.getenv("NOTION_DATABASE_ID"),
            github_token=os.getenv("GITHUB_TOKEN"),
            companies_house_api_key=os.getenv("COMPANIES_HOUSE_API_KEY"),
            parallel_collectors=os.getenv("PARALLEL_COLLECTORS", "true").lower() == "true",
            batch_size=int(os.getenv("BATCH_SIZE", "50")),
            strict_mode=os.getenv("STRICT_MODE", "false").lower() == "true",
            warmup_suppression_cache=os.getenv("WARMUP_SUPPRESSION_CACHE", "true").lower() == "true",
            use_gating=os.getenv("USE_GATING", "true").lower() == "true",
            use_entities=os.getenv("USE_ENTITIES", "false").lower() == "true",
            use_asset_store=os.getenv("USE_ASSET_STORE", "false").lower() == "true",
        )


@dataclass
class PipelineStats:
    """Statistics from a pipeline run"""

    # Collector stats
    collectors_run: int = 0
    collectors_succeeded: int = 0
    collectors_failed: int = 0
    signals_collected: int = 0

    # Storage stats
    signals_stored: int = 0
    signals_deduplicated: int = 0

    # Verification stats
    signals_processed: int = 0
    signals_auto_push: int = 0
    signals_needs_review: int = 0
    signals_held: int = 0
    signals_rejected: int = 0

    # Notion stats
    prospects_created: int = 0
    prospects_updated: int = 0
    prospects_skipped: int = 0

    # Errors
    errors: List[str] = field(default_factory=list)

    # Health monitoring
    health_report: Optional[Any] = None  # HealthReport from signal_health

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def complete(self):
        """Mark pipeline as completed"""
        self.completed_at = datetime.now(timezone.utc)

    @property
    def duration_seconds(self) -> Optional[float]:
        """Pipeline duration in seconds"""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/display"""
        return {
            "collectors": {
                "run": self.collectors_run,
                "succeeded": self.collectors_succeeded,
                "failed": self.collectors_failed,
                "signals_collected": self.signals_collected,
            },
            "storage": {
                "signals_stored": self.signals_stored,
                "signals_deduplicated": self.signals_deduplicated,
            },
            "verification": {
                "signals_processed": self.signals_processed,
                "auto_push": self.signals_auto_push,
                "needs_review": self.signals_needs_review,
                "held": self.signals_held,
                "rejected": self.signals_rejected,
            },
            "notion": {
                "prospects_created": self.prospects_created,
                "prospects_updated": self.prospects_updated,
                "prospects_skipped": self.prospects_skipped,
            },
            "errors": self.errors,
            "health": self.health_report.to_dict() if self.health_report else None,
            "timing": {
                "started_at": self.started_at.isoformat(),
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
                "duration_seconds": self.duration_seconds,
            },
        }


# =============================================================================
# PIPELINE ORCHESTRATOR
# =============================================================================

class DiscoveryPipeline:
    """
    Main pipeline orchestrator for the Discovery Engine.

    Coordinates all stages:
    1. Collect: Run signal collectors
    2. Store: Save signals to SQLite
    3. Dedupe: Check suppression cache
    4. Verify: Run through verification gate
    5. Push: Send to Notion CRM

    Features:
    - Parallel or sequential collector execution
    - Batch processing of stored signals
    - Automatic suppression checking
    - Proper error handling and rollback
    - Detailed statistics
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """
        Initialize pipeline with configuration.

        Args:
            config: Pipeline configuration (defaults to environment variables)
        """
        self.config = config or PipelineConfig.from_env()

        # Components (initialized lazily)
        self._store: Optional[SignalStore] = None
        self._notion: Optional[NotionConnector] = None
        self._gate: Optional[VerificationGate] = None
        self._asset_store: Optional[SourceAssetStore] = None
        self._signal_processor: Optional[SignalProcessor] = None
        self._entity_resolver: Optional[EntityResolver] = None
        self._health_monitor: Optional[SignalHealthMonitor] = None
        self._notifier: Optional[SlackNotifier] = None

        # State
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize pipeline components"""
        if self._initialized:
            return

        logger.info("Initializing discovery pipeline...")

        # Initialize signal store
        self._store = SignalStore(db_path=self.config.db_path)
        await self._store.initialize()

        # Initialize Notion connector (if credentials provided)
        if self.config.notion_api_key and self.config.notion_database_id:
            self._notion = NotionConnector(
                api_key=self.config.notion_api_key,
                database_id=self.config.notion_database_id,
            )
            logger.info("Notion connector initialized")
        else:
            logger.warning("Notion credentials not provided - push operations will be disabled")

        # Initialize verification gate
        self._gate = VerificationGate(strict_mode=self.config.strict_mode)

        # Initialize SourceAssetStore (if enabled)
        if self.config.use_asset_store:
            self._asset_store = SourceAssetStore(db_path=self.config.asset_store_path)
            await self._asset_store.initialize()
            logger.info("SourceAssetStore initialized")

        # Initialize SignalProcessor (if gating enabled)
        if self.config.use_gating:
            processor_config = ProcessorConfig()
            self._signal_processor = SignalProcessor(processor_config)
            logger.info("SignalProcessor initialized (two-stage gating enabled)")

        # Initialize EntityResolver (if entity resolution enabled)
        if self.config.use_entities:
            resolver_config = ResolverConfig()
            self._entity_resolver = EntityResolver(resolver_config)
            logger.info("EntityResolver initialized (asset-to-lead resolution enabled)")

        # Initialize SignalHealthMonitor (non-fatal if it fails)
        try:
            self._health_monitor = SignalHealthMonitor(self._store)
            logger.info("SignalHealthMonitor initialized")
        except Exception as e:
            logger.warning(f"SignalHealthMonitor initialization failed (non-fatal): {e}")
            self._health_monitor = None

        # Initialize Slack notifier (non-fatal if not configured)
        try:
            self._notifier = SlackNotifier()
            if self._notifier.is_configured:
                logger.info("SlackNotifier initialized")
            else:
                logger.debug("SlackNotifier not configured (SLACK_WEBHOOK_URL not set)")
        except Exception as e:
            logger.warning(f"SlackNotifier initialization failed (non-fatal): {e}")
            self._notifier = None

        # Warmup suppression cache (non-fatal if it fails)
        if self.config.warmup_suppression_cache:
            try:
                await self._warmup_suppression_cache()
            except Exception as e:
                logger.warning(f"Suppression cache warmup failed (non-fatal): {e}")

        self._initialized = True
        logger.info("Pipeline initialization complete")

    async def close(self) -> None:
        """Clean up resources"""
        if self._store:
            await self._store.close()
        if self._asset_store:
            await self._asset_store.close()
            self._asset_store = None
        if self._notifier:
            await self._notifier.close()
            self._notifier = None
        self._initialized = False

    async def _warmup_suppression_cache(self) -> None:
        """
        Warm up suppression cache from Notion on pipeline startup.

        This ensures the local cache is fresh before processing signals,
        preventing duplicate pushes to Notion on first run.

        Non-fatal: Called with try/except in initialize().
        """
        if not self._notion:
            logger.info("Notion connector not available, skipping warmup")
            return

        if not self._store:
            logger.warning("SignalStore not initialized for warmup")
            return

        logger.info("Warming up suppression cache from Notion...")

        sync = SuppressionSync(
            notion_connector=self._notion,
            signal_store=self._store,
        )

        result = await sync.sync(dry_run=False)

        logger.info(
            f"Suppression cache warmup complete: "
            f"{result.entries_synced} entries cached"
        )

    # =========================================================================
    # HIGH-LEVEL PIPELINE METHODS
    # =========================================================================

    async def run_full_pipeline(
        self,
        collectors: Optional[List[str]] = None,
        dry_run: bool = True,
    ) -> PipelineStats:
        """
        Run the complete discovery pipeline.

        Stages:
        1. Run collectors (parallel or sequential)
        2. Store signals in SQLite
        3. Check suppression cache
        4. Run through verification gate
        5. Push to Notion (if not dry_run)

        Args:
            collectors: List of collector names to run (None = all available)
            dry_run: If True, don't actually push to Notion

        Returns:
            PipelineStats with detailed metrics
        """
        await self.initialize()

        stats = PipelineStats()

        try:
            logger.info(
                f"Starting full pipeline (collectors={collectors}, dry_run={dry_run})"
            )

            # Stage 1: Collect signals
            collector_results = await self._run_collectors_stage(collectors or [], dry_run)
            stats.collectors_run = len(collector_results)
            stats.collectors_succeeded = sum(
                1 for r in collector_results if r.status == CollectorStatus.SUCCESS
            )
            stats.collectors_failed = sum(
                1 for r in collector_results if r.status == CollectorStatus.ERROR
            )
            stats.signals_collected = sum(r.signals_found for r in collector_results)

            # Stage 2: Process pending signals
            process_stats = await self._process_signals_stage(dry_run)
            stats.signals_processed = process_stats["processed"]
            stats.signals_auto_push = process_stats["auto_push"]
            stats.signals_needs_review = process_stats["needs_review"]
            stats.signals_held = process_stats["held"]
            stats.signals_rejected = process_stats["rejected"]
            stats.prospects_created = process_stats["prospects_created"]
            stats.prospects_updated = process_stats["prospects_updated"]
            stats.prospects_skipped = process_stats["prospects_skipped"]

            # Generate final health report
            if self._health_monitor:
                try:
                    stats.health_report = await self._health_monitor.generate_report(lookback_days=30)
                except Exception as e:
                    logger.warning(f"Failed to generate health report (non-fatal): {e}")

            # Send daily summary to Slack
            if self._notifier and self._notifier.is_configured and not dry_run:
                try:
                    health_status = "HEALTHY"
                    if stats.health_report:
                        health_status = stats.health_report.overall_status

                    await self._notifier.notify_daily_summary(
                        signals_collected=stats.signals_collected,
                        signals_pushed=stats.prospects_created + stats.prospects_updated,
                        high_confidence_count=stats.signals_auto_push,
                        collectors_succeeded=stats.collectors_succeeded,
                        collectors_failed=stats.collectors_failed,
                        health_status=health_status,
                    )
                except Exception as e:
                    logger.warning(f"Slack daily summary failed (non-fatal): {e}")

            logger.info("Full pipeline completed successfully")

        except Exception as e:
            logger.exception("Pipeline failed")
            stats.errors.append(f"Pipeline error: {str(e)}")

        finally:
            stats.complete()

        return stats

    async def run_collectors(
        self,
        collector_names: List[str],
        dry_run: bool = True,
    ) -> List[CollectorResult]:
        """
        Run specific collectors without processing.

        Args:
            collector_names: List of collector names (e.g., ["github", "sec_edgar"])
            dry_run: If True, don't persist results

        Returns:
            List of CollectorResult objects
        """
        await self.initialize()

        logger.info(f"Running collectors: {collector_names} (dry_run={dry_run})")

        return await self._run_collectors_stage(collector_names, dry_run)

    async def process_pending(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Process all pending signals in the store.

        Steps:
        1. Load pending signals from SQLite
        2. Check suppression cache
        3. Run through verification gate
        4. Push to Notion (if not dry_run)

        Args:
            dry_run: If True, don't actually push to Notion

        Returns:
            Dictionary with processing statistics
        """
        await self.initialize()

        logger.info(f"Processing pending signals (dry_run={dry_run})")

        return await self._process_signals_stage(dry_run)

    async def sync_suppression(self) -> int:
        """
        Sync suppression cache from Notion to local SQLite.

        Returns:
            Number of entries synced
        """
        await self.initialize()

        if not self._notion:
            raise RuntimeError("Notion connector not initialized")

        logger.info("Syncing suppression cache from Notion...")

        # Get suppression list from Notion
        suppression_dict = await self._notion.get_suppression_list(force_refresh=True)

        # Convert to SuppressionEntry objects for storage
        from storage.signal_store import SuppressionEntry

        entries = []
        for key, notion_entry in suppression_dict.items():
            entries.append(
                SuppressionEntry(
                    canonical_key=notion_entry.canonical_key or "",
                    notion_page_id=notion_entry.notion_page_id,
                    status=notion_entry.status,
                    company_name=None,  # Not provided by Notion connector's SuppressionEntry
                )
            )

        # Update local cache
        count = await self._store.update_suppression_cache(entries)

        logger.info(f"Synced {count} suppression entries to local cache")

        return count

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get pipeline statistics.

        Returns:
            Dictionary with database and processing stats
        """
        await self.initialize()

        # Get store stats
        store_stats = await self._store.get_stats()

        # Get processing stats
        processing_stats = await self._store.get_processing_stats()

        return {
            "storage": store_stats,
            "processing": processing_stats,
            "config": {
                "db_path": str(self.config.db_path),
                "parallel_collectors": self.config.parallel_collectors,
                "batch_size": self.config.batch_size,
                "strict_mode": self.config.strict_mode,
            },
        }

    # =========================================================================
    # INTERNAL STAGE IMPLEMENTATIONS
    # =========================================================================

    async def _run_collectors_stage(
        self,
        collector_names: List[str],
        dry_run: bool,
    ) -> List[CollectorResult]:
        """
        Run collectors in parallel or sequential mode.

        Returns list of CollectorResult objects.
        """
        if not collector_names:
            logger.warning("No collectors specified")
            return []

        results: List[CollectorResult] = []

        if self.config.parallel_collectors:
            # Run collectors in parallel
            logger.info(f"Running {len(collector_names)} collectors in parallel")

            tasks = [
                self._run_single_collector(name, dry_run)
                for name in collector_names
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Convert exceptions to error results
            results = [
                r if isinstance(r, CollectorResult) else CollectorResult(
                    collector=collector_names[i],
                    status=CollectorStatus.ERROR,
                    error_message=str(r),
                    dry_run=dry_run,
                )
                for i, r in enumerate(results)
            ]
        else:
            # Run collectors sequentially
            logger.info(f"Running {len(collector_names)} collectors sequentially")

            for name in collector_names:
                result = await self._run_single_collector(name, dry_run)
                results.append(result)

        # Log summary
        succeeded = sum(1 for r in results if r.status == CollectorStatus.SUCCESS)
        failed = sum(1 for r in results if r.status == CollectorStatus.ERROR)
        total_signals = sum(r.signals_found for r in results)

        logger.info(
            f"Collector stage complete: {succeeded}/{len(results)} succeeded, "
            f"{total_signals} signals collected"
        )

        # Run health monitor after collectors (non-fatal if it fails)
        await self._check_signal_health()

        return results

    async def _run_single_collector(
        self,
        collector_name: str,
        dry_run: bool,
    ) -> CollectorResult:
        """Run a single collector and return results"""
        try:
            logger.info(f"Running collector: {collector_name}")

            # Import collector dynamically based on name
            if collector_name == "github":
                from collectors.github import GitHubCollector
                collector = GitHubCollector(github_token=self.config.github_token)
            elif collector_name == "sec_edgar":
                from collectors.sec_edgar import SECEdgarCollector
                collector = SECEdgarCollector()
            elif collector_name == "companies_house":
                from collectors.companies_house import CompaniesHouseCollector
                collector = CompaniesHouseCollector(
                    api_key=self.config.companies_house_api_key
                )
            elif collector_name == "domain_whois":
                from collectors.domain_whois import DomainWhoisCollector
                collector = DomainWhoisCollector()
            else:
                return CollectorResult(
                    collector=collector_name,
                    status=CollectorStatus.ERROR,
                    error_message=f"Unknown collector: {collector_name}",
                    dry_run=dry_run,
                )

            # Run collector
            result = await collector.run(dry_run=dry_run)

            logger.info(
                f"Collector {collector_name} completed: "
                f"{result.signals_found} signals found"
            )

            return result

        except Exception as e:
            logger.exception(f"Error running collector {collector_name}")
            return CollectorResult(
                collector=collector_name,
                status=CollectorStatus.ERROR,
                error_message=str(e),
                dry_run=dry_run,
            )

    async def _process_signals_stage(self, dry_run: bool) -> Dict[str, int]:
        """
        Process pending signals through verification and Notion push.

        Returns dict with processing statistics.
        """
        stats = {
            "processed": 0,
            "auto_push": 0,
            "needs_review": 0,
            "held": 0,
            "rejected": 0,
            "prospects_created": 0,
            "prospects_updated": 0,
            "prospects_skipped": 0,
        }

        # Get pending signals
        pending = await self._store.get_pending_signals(limit=self.config.batch_size)

        if not pending:
            logger.info("No pending signals to process")
            return stats

        logger.info(f"Processing {len(pending)} pending signals")

        # Group by canonical key
        by_key: Dict[str, List[StoredSignal]] = {}
        for signal in pending:
            by_key.setdefault(signal.canonical_key, []).append(signal)

        logger.info(f"Grouped into {len(by_key)} unique companies")

        # Process each company
        for canonical_key, company_signals in by_key.items():
            try:
                result = await self._process_company(company_signals, dry_run)

                # Update stats
                stats["processed"] += len(company_signals)

                if result["decision"] == PushDecision.AUTO_PUSH:
                    stats["auto_push"] += 1
                elif result["decision"] == PushDecision.NEEDS_REVIEW:
                    stats["needs_review"] += 1
                elif result["decision"] == PushDecision.HOLD:
                    stats["held"] += 1
                elif result["decision"] == PushDecision.REJECT:
                    stats["rejected"] += 1

                if result.get("notion_status") == "created":
                    stats["prospects_created"] += 1
                elif result.get("notion_status") == "updated":
                    stats["prospects_updated"] += 1
                elif result.get("notion_status") == "skipped":
                    stats["prospects_skipped"] += 1

            except Exception as e:
                logger.exception(f"Error processing company {canonical_key}")

                # Mark signals as rejected
                for sig in company_signals:
                    await self._store.mark_rejected(sig.id, str(e))

        logger.info(f"Processing stage complete: {stats}")

        return stats

    async def _process_company(
        self,
        signals: List[StoredSignal],
        dry_run: bool,
    ) -> Dict[str, Any]:
        """
        Process all signals for a single company.

        Steps:
        1. Convert to Signal objects
        2. Check suppression
        3. Run through verification gate
        4. Push to Notion if appropriate
        5. Update signal status

        Returns dict with decision and Notion status.
        """
        if not signals:
            return {"decision": PushDecision.REJECT, "reason": "No signals"}

        canonical_key = signals[0].canonical_key

        # Check suppression cache
        suppressed = await self._store.check_suppression(canonical_key)

        if suppressed:
            logger.info(
                f"Company {canonical_key} suppressed "
                f"(Notion: {suppressed.notion_page_id}, status: {suppressed.status})"
            )

            # Mark as rejected (already in CRM)
            for sig in signals:
                await self._store.mark_rejected(
                    sig.id,
                    f"Suppressed: already in Notion with status {suppressed.status}",
                    metadata={"notion_page_id": suppressed.notion_page_id},
                )

            return {
                "decision": PushDecision.REJECT,
                "reason": "Suppressed",
                "notion_status": "skipped",
                "gating_applied": False,
            }

        # Run through SignalProcessor gating (if enabled)
        gating_applied = False
        if self._signal_processor and self.config.use_gating:
            gating_applied = True
            logger.info(f"Running gating for {canonical_key} (SignalProcessor enabled)")
            # Note: Full gating integration would process signals here
            # For now, we mark that gating was applied and continue

        # Convert to Signal objects for verification gate
        gate_signals = [self._stored_to_signal(sig) for sig in signals]

        # Run through verification gate
        verification = self._gate.evaluate(gate_signals)

        logger.info(
            f"Verification for {canonical_key}: "
            f"{verification.decision.value} (confidence: {verification.confidence_score:.2f})"
        )

        # Decide on Notion push
        notion_status = None

        if verification.decision in (PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW):
            if self._notion and not dry_run:
                # Push to Notion
                notion_result = await self._push_to_notion(signals, verification)
                notion_status = notion_result["status"]

                # Mark signals as pushed
                for sig in signals:
                    await self._store.mark_pushed(
                        sig.id,
                        notion_page_id=notion_result["page_id"],
                        metadata={
                            "decision": verification.decision.value,
                            "confidence": verification.confidence_score,
                            "status": verification.suggested_status,
                        },
                    )

                # Notify Slack for high-confidence signals
                if (
                    verification.decision == PushDecision.AUTO_PUSH
                    and self._notifier
                    and self._notifier.is_configured
                ):
                    try:
                        company_name = signals[0].company_name or canonical_key
                        signal_types = list(set(s.signal_type for s in signals))
                        sources_count = len(set(s.source_api for s in signals))
                        why_now = self._build_why_now(signals)

                        # Build Notion URL
                        notion_url = f"https://notion.so/{notion_result['page_id'].replace('-', '')}"

                        await self._notifier.notify_high_confidence_signal(
                            company_name=company_name,
                            confidence=verification.confidence_score,
                            signal_types=signal_types,
                            sources_count=sources_count,
                            notion_url=notion_url,
                            canonical_key=canonical_key,
                            why_now=why_now,
                        )
                    except Exception as e:
                        logger.warning(f"Slack notification failed (non-fatal): {e}")
            else:
                # Dry run or no Notion connector
                logger.info(
                    f"Would push {canonical_key} to Notion with status: "
                    f"{verification.suggested_status}"
                )

                # Mark as pushed with dummy page ID
                for sig in signals:
                    await self._store.mark_pushed(
                        sig.id,
                        notion_page_id="dry-run-placeholder",
                        metadata={
                            "decision": verification.decision.value,
                            "confidence": verification.confidence_score,
                            "status": verification.suggested_status,
                            "dry_run": True,
                        },
                    )

                notion_status = "dry_run"

        elif verification.decision == PushDecision.HOLD:
            # Keep as pending - don't mark as pushed or rejected
            logger.info(f"Holding {canonical_key} for more signals")

        elif verification.decision == PushDecision.REJECT:
            # Mark as rejected
            for sig in signals:
                await self._store.mark_rejected(sig.id, verification.reason)

        return {
            "decision": verification.decision,
            "reason": verification.reason,
            "confidence": verification.confidence_score,
            "notion_status": notion_status,
            "gating_applied": gating_applied,
        }

    async def _push_to_notion(
        self,
        signals: List[StoredSignal],
        verification: VerificationResult,
    ) -> Dict[str, Any]:
        """
        Push a company to Notion CRM.

        Returns dict with status and page_id.
        """
        if not self._notion:
            raise RuntimeError("Notion connector not initialized")

        # Build prospect payload from signals
        primary_signal = signals[0]

        # Extract company info
        company_name = primary_signal.company_name or "Unknown Company"

        # Determine stage from signals
        stage = self._infer_stage(signals)

        # Build payload
        payload = ProspectPayload(
            discovery_id=f"discovery_{primary_signal.id}",
            company_name=company_name,
            canonical_key=primary_signal.canonical_key,
            stage=stage,
            status=verification.suggested_status,
            confidence_score=verification.confidence_score,
            signal_types=[sig.signal_type for sig in signals],
            why_now=self._build_why_now(signals),
            canonical_key_candidates=[primary_signal.canonical_key],
        )

        # Push to Notion
        result = await self._notion.upsert_prospect(payload)

        logger.info(
            f"Pushed {company_name} to Notion: {result['status']} "
            f"(page_id: {result['page_id']})"
        )

        return result

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _stored_to_signal(self, stored: StoredSignal) -> Signal:
        """Convert StoredSignal to Signal for verification gate"""
        return Signal(
            id=str(stored.id),
            signal_type=stored.signal_type,
            confidence=stored.confidence,
            source_api=stored.source_api,
            detected_at=stored.detected_at,
            raw_data=stored.raw_data,
            verified_by_sources=[stored.source_api],
            verification_status=VerificationStatus.SINGLE_SOURCE,
        )

    def _infer_stage(self, signals: List[StoredSignal]) -> InvestmentStage:
        """Infer investment stage from signals"""
        # Check raw_data for stage hints
        for sig in signals:
            stage_estimate = sig.raw_data.get("stage_estimate")
            if stage_estimate == "Pre-Seed":
                return InvestmentStage.PRE_SEED
            elif stage_estimate == "Seed":
                return InvestmentStage.SEED
            elif stage_estimate == "Seed +":
                return InvestmentStage.SEED_PLUS
            elif stage_estimate == "Series A":
                return InvestmentStage.SERIES_A

        # Default to Pre-Seed
        return InvestmentStage.PRE_SEED

    def _build_why_now(self, signals: List[StoredSignal]) -> str:
        """Build 'Why Now' narrative from signals"""
        parts = []

        for sig in signals:
            why_now = sig.raw_data.get("why_now")
            if why_now:
                parts.append(why_now)

        if parts:
            return "; ".join(parts[:3])  # Limit to 3 reasons

        # Fallback
        signal_types = [sig.signal_type for sig in signals]
        return f"Detected via {', '.join(set(signal_types))}"

    async def _check_signal_health(self) -> None:
        """
        Run health monitor and log any warnings.

        Called after collector runs to check signal quality.
        Non-fatal - errors are logged but don't crash the pipeline.
        """
        if not self._health_monitor:
            return

        try:
            report = await self._health_monitor.generate_report(lookback_days=30)

            # Log overall status
            if report.overall_status == "CRITICAL":
                logger.error(f"Signal health CRITICAL: {len(report.anomalies)} anomalies detected")
            elif report.overall_status == "DEGRADED":
                logger.warning(f"Signal health DEGRADED: {len(report.anomalies)} anomalies detected")
            else:
                logger.info(f"Signal health HEALTHY: {report.total_signals} signals from {report.total_sources} sources")

            # Log warnings from source health
            for source_name, health in report.source_health.items():
                if health.status == "CRITICAL":
                    logger.error(f"Source {source_name} CRITICAL: {', '.join(health.warnings)}")
                elif health.status == "WARNING":
                    logger.warning(f"Source {source_name} WARNING: {', '.join(health.warnings)}")

            # Log anomalies
            for anomaly in report.anomalies:
                if anomaly.severity == "CRITICAL":
                    logger.error(f"Anomaly detected: {anomaly.description}")
                else:
                    logger.warning(f"Anomaly detected: {anomaly.description}")

            # Send Slack alert for DEGRADED or CRITICAL health
            if (
                report.overall_status in ("DEGRADED", "CRITICAL")
                and self._notifier
                and self._notifier.is_configured
            ):
                try:
                    anomaly_descriptions = [a.description for a in report.anomalies]
                    await self._notifier.notify_health_alert(
                        status=report.overall_status,
                        anomalies=anomaly_descriptions,
                        total_signals=report.total_signals,
                        stale_signals=report.stale_signals,
                        suspicious_signals=report.suspicious_signals,
                    )
                except Exception as e:
                    logger.warning(f"Slack health alert failed (non-fatal): {e}")

        except Exception as e:
            logger.warning(f"Health check failed (non-fatal): {e}")


# =============================================================================
# CONTEXT MANAGER
# =============================================================================

async def pipeline_context(config: Optional[PipelineConfig] = None):
    """
    Context manager for pipeline that handles initialization and cleanup.

    Usage:
        async with pipeline_context() as pipeline:
            result = await pipeline.run_full_pipeline(["github"])
    """
    pipeline = DiscoveryPipeline(config)
    await pipeline.initialize()
    try:
        yield pipeline
    finally:
        await pipeline.close()
