"""
Discovery Pipeline Orchestrator

Coordinates the full discovery workflow:
1. Run collectors to gather signals
2. Process signals through verification gate
3. Push qualified prospects to Notion
4. Sync suppression cache

Usage:
    from workflows.pipeline import DiscoveryPipeline, PipelineConfig

    config = PipelineConfig.from_env()
    pipeline = DiscoveryPipeline(config)

    await pipeline.initialize()
    stats = await pipeline.run_full_pipeline(
        collectors=["github", "sec_edgar"],
        dry_run=True
    )
    await pipeline.close()
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from connectors.notion_connector_v2 import NotionConnector, ProspectPayload, InvestmentStage
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    PushDecision,
    VerificationResult,
)
from workflows.suppression_sync import SuppressionSync

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class PipelineMode(str, Enum):
    """Pipeline execution mode."""
    FULL = "full"  # Collect → Process → Push
    COLLECT_ONLY = "collect"  # Only run collectors
    PROCESS_ONLY = "process"  # Only process pending signals
    SYNC_ONLY = "sync"  # Only sync suppression cache


# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class PipelineConfig:
    """Configuration for the discovery pipeline."""
    # Database
    db_path: str = "signals.db"

    # Notion
    notion_api_key: str = ""
    notion_database_id: str = ""

    # Execution
    parallel_collectors: bool = True
    batch_size: int = 50
    strict_mode: bool = False  # Require 2+ sources for auto-push

    # Rate limiting
    collector_delay_seconds: float = 1.0
    notion_rate_limit_delay: float = 0.35

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Load configuration from environment variables."""
        return cls(
            db_path=os.environ.get("DISCOVERY_DB_PATH", "signals.db"),
            notion_api_key=os.environ.get("NOTION_API_KEY", ""),
            notion_database_id=os.environ.get("NOTION_DATABASE_ID", ""),
            parallel_collectors=os.environ.get("PARALLEL_COLLECTORS", "true").lower() == "true",
            batch_size=int(os.environ.get("BATCH_SIZE", "50")),
            strict_mode=os.environ.get("STRICT_MODE", "false").lower() == "true",
        )


# =============================================================================
# STATISTICS
# =============================================================================

@dataclass
class PipelineStats:
    """Statistics from a pipeline run."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

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
            "collectors_run": self.collectors_run,
            "collectors_succeeded": self.collectors_succeeded,
            "collectors_failed": self.collectors_failed,
            "signals_collected": self.signals_collected,
            "signals_stored": self.signals_stored,
            "signals_deduplicated": self.signals_deduplicated,
            "signals_processed": self.signals_processed,
            "signals_auto_push": self.signals_auto_push,
            "signals_needs_review": self.signals_needs_review,
            "signals_held": self.signals_held,
            "signals_rejected": self.signals_rejected,
            "prospects_created": self.prospects_created,
            "prospects_updated": self.prospects_updated,
            "prospects_skipped": self.prospects_skipped,
            "errors": self.errors[:20],  # Limit errors
            "error_count": len(self.errors),
        }


# =============================================================================
# COLLECTOR REGISTRY
# =============================================================================

# Maps collector names to their module and class
COLLECTOR_REGISTRY: Dict[str, Dict[str, str]] = {
    "github": {
        "module": "collectors.github",
        "class": "GitHubCollector",
    },
    "github_activity": {
        "module": "collectors.github_activity",
        "class": "GitHubActivityCollector",
    },
    "sec_edgar": {
        "module": "collectors.sec_edgar",
        "class": "SECEdgarCollector",
    },
    "companies_house": {
        "module": "collectors.companies_house",
        "class": "CompaniesHouseCollector",
    },
    "domain_whois": {
        "module": "collectors.domain_whois",
        "class": "DomainWhoisCollector",
    },
    "job_postings": {
        "module": "collectors.job_postings",
        "class": "JobPostingsCollector",
    },
    "product_hunt": {
        "module": "collectors.product_hunt",
        "class": "ProductHuntCollector",
    },
    "arxiv": {
        "module": "collectors.arxiv",
        "class": "ArxivCollector",
    },
    "uspto": {
        "module": "collectors.uspto",
        "class": "USPTOCollector",
    },
}


# =============================================================================
# PIPELINE
# =============================================================================

class DiscoveryPipeline:
    """
    Orchestrates the discovery workflow.

    Coordinates:
    - Signal collection from multiple sources
    - Signal storage and deduplication
    - Verification gate evaluation
    - Notion CRM integration
    - Suppression cache management
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.store: Optional[SignalStore] = None
        self.notion: Optional[NotionConnector] = None
        self.gate: Optional[VerificationGate] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize all components."""
        if self._initialized:
            return

        logger.info("Initializing pipeline...")

        # Initialize signal store
        self.store = SignalStore(db_path=self.config.db_path)
        await self.store.initialize()

        # Initialize Notion connector (if credentials available)
        if self.config.notion_api_key and self.config.notion_database_id:
            self.notion = NotionConnector(
                api_key=self.config.notion_api_key,
                database_id=self.config.notion_database_id,
                rate_limit_delay=self.config.notion_rate_limit_delay,
            )
        else:
            logger.warning("Notion credentials not configured - CRM integration disabled")

        # Initialize verification gate
        self.gate = VerificationGate(strict_mode=self.config.strict_mode)

        self._initialized = True
        logger.info("Pipeline initialized")

    async def close(self) -> None:
        """Close all resources."""
        if self.store:
            await self.store.close()
        self._initialized = False
        logger.info("Pipeline closed")

    # =========================================================================
    # MAIN WORKFLOWS
    # =========================================================================

    async def run_full_pipeline(
        self,
        collectors: Optional[List[str]] = None,
        dry_run: bool = True,
    ) -> PipelineStats:
        """
        Run the complete pipeline: collect → process → push.

        Args:
            collectors: List of collector names to run (None = all)
            dry_run: If True, don't persist to storage or push to Notion

        Returns:
            PipelineStats with results
        """
        if not self._initialized:
            await self.initialize()

        stats = PipelineStats()

        try:
            # Step 1: Run collectors
            logger.info("Step 1: Running collectors...")
            collector_results = await self.run_collectors(
                collector_names=collectors or list(COLLECTOR_REGISTRY.keys()),
                dry_run=dry_run,
            )

            for result in collector_results:
                stats.collectors_run += 1
                stats.signals_collected += result.signals_found
                if result.status in (CollectorStatus.SUCCESS, CollectorStatus.DRY_RUN):
                    stats.collectors_succeeded += 1
                    stats.signals_stored += result.signals_new
                    stats.signals_deduplicated += result.signals_suppressed
                else:
                    stats.collectors_failed += 1
                    if result.error_message:
                        stats.errors.append(f"{result.collector}: {result.error_message}")

            # Step 2: Process pending signals
            if not dry_run:
                logger.info("Step 2: Processing pending signals...")
                process_result = await self.process_pending(dry_run=False)

                stats.signals_processed = process_result["processed"]
                stats.signals_auto_push = process_result["auto_push"]
                stats.signals_needs_review = process_result["needs_review"]
                stats.signals_held = process_result["held"]
                stats.signals_rejected = process_result["rejected"]
                stats.prospects_created = process_result["prospects_created"]
                stats.prospects_updated = process_result["prospects_updated"]
                stats.prospects_skipped = process_result["prospects_skipped"]

            stats.completed_at = datetime.now(timezone.utc)
            logger.info(f"Pipeline complete in {stats.duration_seconds:.2f}s")

        except Exception as e:
            logger.exception("Pipeline failed")
            stats.errors.append(f"Pipeline error: {str(e)}")
            stats.completed_at = datetime.now(timezone.utc)

        return stats

    async def run_collectors(
        self,
        collector_names: List[str],
        dry_run: bool = True,
    ) -> List[CollectorResult]:
        """
        Run specified collectors.

        Args:
            collector_names: Names of collectors to run
            dry_run: If True, don't persist signals

        Returns:
            List of CollectorResult
        """
        if not self._initialized:
            await self.initialize()

        results: List[CollectorResult] = []

        # Filter valid collectors
        valid_collectors = [
            name for name in collector_names
            if name in COLLECTOR_REGISTRY
        ]

        invalid_collectors = set(collector_names) - set(valid_collectors)
        if invalid_collectors:
            logger.warning(f"Skipping unknown collectors: {invalid_collectors}")

        if self.config.parallel_collectors:
            # Run collectors in parallel
            tasks = [
                self._run_single_collector(name, dry_run)
                for name in valid_collectors
            ]
            results = await asyncio.gather(*tasks)
        else:
            # Run collectors sequentially
            for name in valid_collectors:
                result = await self._run_single_collector(name, dry_run)
                results.append(result)
                await asyncio.sleep(self.config.collector_delay_seconds)

        return results

    async def _run_single_collector(
        self,
        collector_name: str,
        dry_run: bool,
    ) -> CollectorResult:
        """Run a single collector by name."""
        try:
            registry_entry = COLLECTOR_REGISTRY.get(collector_name)
            if not registry_entry:
                return CollectorResult(
                    collector=collector_name,
                    status=CollectorStatus.NOT_FOUND,
                    error_message=f"Unknown collector: {collector_name}",
                )

            # Dynamic import
            import importlib
            module = importlib.import_module(registry_entry["module"])
            collector_class = getattr(module, registry_entry["class"])

            # Check if collector uses BaseCollector interface
            from collectors.base import BaseCollector
            if issubclass(collector_class, BaseCollector):
                # BaseCollector interface
                collector = collector_class(
                    store=self.store,
                    collector_name=collector_name,
                )
                return await collector.run(dry_run=dry_run)
            else:
                # Legacy interface (job_postings, github_activity)
                collector = collector_class()

                # Try to run with appropriate parameters
                if hasattr(collector, "run"):
                    # Handle different run signatures
                    if collector_name == "job_postings":
                        # JobPostingsCollector needs domains
                        # In a real scenario, you'd get domains from somewhere
                        result = await collector.run(domains=[], dry_run=dry_run)
                        return CollectorResult(
                            collector=collector_name,
                            status=CollectorStatus.SUCCESS if result.get("status") == "SUCCESS" else CollectorStatus.ERROR,
                            signals_found=result.get("signals_found", 0),
                            signals_new=result.get("signals_found", 0),
                            dry_run=dry_run,
                        )
                    else:
                        # Generic run
                        async with collector:
                            result = await collector.run(dry_run=dry_run)
                            if isinstance(result, dict):
                                return CollectorResult(
                                    collector=collector_name,
                                    status=CollectorStatus.SUCCESS,
                                    signals_found=result.get("signals_found", 0),
                                    signals_new=result.get("signals_new", 0),
                                    dry_run=dry_run,
                                )
                            return result

                return CollectorResult(
                    collector=collector_name,
                    status=CollectorStatus.ERROR,
                    error_message="Collector doesn't have run() method",
                )

        except ModuleNotFoundError as e:
            logger.warning(f"Collector module not found: {collector_name} - {e}")
            return CollectorResult(
                collector=collector_name,
                status=CollectorStatus.NOT_FOUND,
                error_message=f"Module not found: {e}",
            )
        except Exception as e:
            logger.exception(f"Collector {collector_name} failed")
            return CollectorResult(
                collector=collector_name,
                status=CollectorStatus.ERROR,
                error_message=str(e),
            )

    async def process_pending(self, dry_run: bool = True) -> Dict[str, int]:
        """
        Process pending signals through verification gate.

        Returns:
            Dict with counts of actions taken
        """
        if not self._initialized:
            await self.initialize()

        result = {
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
        pending = await self.store.get_pending_signals(limit=self.config.batch_size)
        logger.info(f"Processing {len(pending)} pending signals")

        # Group signals by canonical key for multi-signal evaluation
        by_key: Dict[str, List[Signal]] = {}
        signal_id_map: Dict[str, int] = {}  # canonical_key -> stored_signal.id

        for stored_signal in pending:
            # Convert StoredSignal to Signal for verification
            signal = Signal(
                id=f"signal_{stored_signal.id}",
                signal_type=stored_signal.signal_type,
                confidence=stored_signal.confidence,
                source_api=stored_signal.source_api,
                detected_at=stored_signal.detected_at,
                raw_data=stored_signal.raw_data,
            )

            key = stored_signal.canonical_key
            if key not in by_key:
                by_key[key] = []
                signal_id_map[key] = stored_signal.id
            by_key[key].append(signal)

        # Process each entity
        for canonical_key, signals in by_key.items():
            result["processed"] += len(signals)

            # Evaluate through verification gate
            verification = self.gate.evaluate(signals)

            # Track decision
            if verification.decision == PushDecision.AUTO_PUSH:
                result["auto_push"] += 1
            elif verification.decision == PushDecision.NEEDS_REVIEW:
                result["needs_review"] += 1
            elif verification.decision == PushDecision.HOLD:
                result["held"] += 1
            elif verification.decision == PushDecision.REJECT:
                result["rejected"] += 1

            # Push to Notion if qualified
            if not dry_run and self.notion:
                if verification.decision in (PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW):
                    notion_result = await self._push_to_notion(
                        canonical_key=canonical_key,
                        signals=signals,
                        verification=verification,
                    )

                    if notion_result["status"] == "created":
                        result["prospects_created"] += 1
                    elif notion_result["status"] == "updated":
                        result["prospects_updated"] += 1
                    else:
                        result["prospects_skipped"] += 1

                    # Mark signal as pushed
                    if notion_result.get("page_id"):
                        stored_id = signal_id_map.get(canonical_key)
                        if stored_id:
                            await self.store.mark_pushed(
                                stored_id,
                                notion_result["page_id"],
                            )

        return result

    async def _push_to_notion(
        self,
        canonical_key: str,
        signals: List[Signal],
        verification: VerificationResult,
    ) -> Dict[str, Any]:
        """Push a qualified prospect to Notion."""
        if not self.notion:
            return {"status": "skipped", "reason": "Notion not configured"}

        # Extract company info from signals
        company_name = "Unknown"
        company_domain = ""
        signal_types = list(set(s.signal_type for s in signals))

        for signal in signals:
            raw = signal.raw_data or {}
            if "company_name" in raw and not company_name or company_name == "Unknown":
                company_name = raw["company_name"]
            if "company_domain" in raw and not company_domain:
                company_domain = raw["company_domain"]

        # Build prospect payload
        prospect = ProspectPayload(
            discovery_id=f"disc_{canonical_key.replace(':', '_')}",
            company_name=company_name,
            canonical_key=canonical_key,
            stage=InvestmentStage.PRE_SEED,  # Default stage
            status=verification.suggested_status or "Source",
            website=f"https://{company_domain}" if company_domain else "",
            confidence_score=verification.confidence_score,
            signal_types=signal_types,
            why_now=verification.reason,
        )

        try:
            return await self.notion.upsert_prospect(prospect)
        except Exception as e:
            logger.error(f"Failed to push to Notion: {e}")
            return {"status": "error", "reason": str(e)}

    async def sync_suppression(self) -> int:
        """
        Sync suppression cache from Notion.

        Returns:
            Number of entries synced
        """
        if not self._initialized:
            await self.initialize()

        if not self.notion or not self.store:
            logger.warning("Cannot sync suppression - Notion or store not configured")
            return 0

        sync = SuppressionSync(
            notion_connector=self.notion,
            signal_store=self.store,
            ttl_days=7,
        )

        stats = await sync.sync(dry_run=False)
        return stats.entries_synced

    async def get_stats(self) -> Dict[str, Any]:
        """Get current pipeline statistics."""
        if not self._initialized:
            await self.initialize()

        stats: Dict[str, Any] = {
            "storage": {},
            "processing": {},
            "config": {
                "parallel_collectors": self.config.parallel_collectors,
                "batch_size": self.config.batch_size,
                "strict_mode": self.config.strict_mode,
            },
        }

        # Storage stats
        if self.store:
            stats["storage"]["database_path"] = self.config.db_path

            # Get signal counts
            total_signals = await self._count_signals()
            stats["storage"]["total_signals"] = total_signals

            # Get by type
            by_type = await self._count_signals_by_type()
            stats["storage"]["signals_by_type"] = by_type

            # Get suppression stats
            suppression_stats = await self.store.get_suppression_stats()
            stats["storage"]["active_suppression_entries"] = suppression_stats.get("active_entries", 0)

        # Processing stats
        if self.store:
            pending = await self.store.get_pending_signals(limit=1)
            stats["processing"]["pending"] = len(pending) > 0

        return stats

    async def _count_signals(self) -> int:
        """Count total signals in store."""
        # This is a simple helper - could be added to SignalStore
        if not self.store or not self.store._conn:
            return 0
        cursor = self.store._conn.execute("SELECT COUNT(*) FROM signals")
        return cursor.fetchone()[0]

    async def _count_signals_by_type(self) -> Dict[str, int]:
        """Count signals grouped by type."""
        if not self.store or not self.store._conn:
            return {}
        cursor = self.store._conn.execute(
            "SELECT signal_type, COUNT(*) FROM signals GROUP BY signal_type"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}
