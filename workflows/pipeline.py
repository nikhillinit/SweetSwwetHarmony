"""
Pipeline Orchestrator for Discovery Engine

Ties together the entire discovery pipeline:
  collect() ΓåÆ store() ΓåÆ dedupe() ΓåÆ verify() ΓåÆ push()

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
import sqlite3
import uuid
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

# Storage
from storage.db_paths import resolve_canonical_db_path, resolve_private_graph_db_path
from storage.signal_store import SignalStore, StoredSignal
from utils.db_path_helper import resolve_db_path_env
from utils.signal_consolidator import SignalConsolidator, ConsolidatedSignal
from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentConfig
from utils.thesis_filter import ThesisFilter, ThesisFilterConfig, RoutingDecision
from utils.competitor_detector import CompetitorDetector
from utils.exit_predictor import ExitPredictor
from utils.investor_matching import InvestorMatcher, InvestorMatchResult
from storage.source_asset_store import SourceAssetStore, SourceAsset
from storage.founder_store import FounderStore
from storage.entity_resolution import EntityResolutionStore, AssetToLead
from consumer.signal_processor import SignalProcessor, ProcessorConfig
from consumer.entity_resolver import EntityResolver, ResolverConfig

# Velocity tracking (Harmonic enhancement)
from utils.signal_velocity import SignalVelocityTracker, VelocityConfig

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
    WarmIntroIndicator,
    InvestmentStage,
    DealStatus,
)
from connectors.notion_transport import NotionTransport

# Collectors (import dynamically to avoid circular imports)
from discovery_engine.mcp_server import CollectorResult, CollectorStatus

# Suppression sync (for cache warmup)
from workflows.suppression_sync import SuppressionSync
from workflows.notion_outbox_worker import NotionOutboxWorker
from services.watchlist_loader import WatchlistLoader

# Health monitoring
from utils.signal_health import SignalHealthMonitor

# Notifications
from utils.slack_notifier import SlackNotifier, SlackConfig

# Phase G Sprint 2: Identity Resolution & Bi-Temporal Claims
from storage.entity_identity_store import EntityIdentityStore, StrongKeyBinding, AliasKeyBinding, BlockingToken
from storage.claim_fact_store import ClaimFactStore, ClaimFact
from utils.phase_g_entity_resolver import PhaseGEntityResolver, ResolvedEntityGroup
from utils.claim_extractor import ClaimExtractor

# Feature states for SHADOW experimentation
from utils.feature_states import FeatureRegistry, FeatureState
from utils.boilerplate_detector import BoilerplateDetector

# Phase 1a: Identity gate + thin file promotion
from storage.identity_gate import check_identity_integrity, IdentityMigrationRequired
from workflows.thin_file_manager import run_promotion_sweep

# Phase C: HTTP primitive + run tracking
from collectors.http_client import CollectorHttpClient, RunContext
from workflows.run_manager import create_run, start_run, complete_run, fail_run
from ops.collector_heartbeat import initialize_collector_state, record_collector_heartbeat
from ops.collector_health import DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR

logger = logging.getLogger(__name__)

# ContextVar for per-collector HTTP request attribution in asyncio.gather()
_current_collector: ContextVar[str] = ContextVar("current_collector", default="unknown")

# Source-specific minimum confidence overrides.
# hacker_news: 98.69% FP over 153-signal baseline → require high-confidence signal only.
_SOURCE_MIN_CONFIDENCE: dict[str, float] = {
    "hacker_news": 0.70,
}


def _get_min_confidence(source_api: str) -> float:
    """Return the minimum routing confidence for a given source API.

    Falls back to the VerificationGate MEDIUM_CONFIDENCE_THRESHOLD (0.40)
    for sources not listed in _SOURCE_MIN_CONFIDENCE.
    """
    return _SOURCE_MIN_CONFIDENCE.get(source_api, 0.40)


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
    db_path: Optional[str] = None
    asset_store_path: str = "assets.db"  # SourceAssetStore path

    # Notion
    notion_api_key: Optional[str] = None
    notion_database_id: Optional[str] = None
    watchlist_database_id: Optional[str] = None

    # Collectors
    github_token: Optional[str] = None
    companies_house_api_key: Optional[str] = None

    # Execution
    parallel_collectors: bool = True  # Run collectors in parallel
    batch_size: int = 50             # Process signals in batches
    read_only: bool = False          # Defensive no-write mode for process dry-runs

    # Verification
    strict_mode: bool = False        # Require 2+ sources for auto-push

    # Warmup
    warmup_suppression_cache: bool = True  # Auto-sync suppression cache on init

    # Feature flags (v2 components)
    use_gating: bool = True          # Enable TriggerGate + LLMClassifierV2 (consumer filtering)
    use_entities: bool = False       # Enable EntityResolver
    use_asset_store: bool = False    # Save to SourceAssetStore

    # Harmonic-level enhancements
    use_founder_scoring: bool = True  # Enable founder intelligence scoring
    use_velocity_tracking: bool = True  # Enable signal velocity/momentum tracking

    # Signal consolidation
    use_consolidation: bool = True  # Enable signal consolidation before processing

    # Enrichment boost (Phase 2 enhancement)
    use_enrichment_boost: bool = True  # Enable enrichment boost calculation

    # Thesis filtering (Phase 3 enhancement)
    use_thesis_filter: bool = True  # Enable thesis filtering
    thesis_hold_threshold: float = 0.3  # Signals below this are HELD

    # Competitor detection
    use_competitor_detection: bool = True
    portfolio_path: str = "config/portfolio.json"

    # Exit prediction (Phase 4)
    use_exit_predictor: bool = False  # Enable exit prediction scoring

    # Investor matching (Sprint 5)
    use_investor_matching: bool = False  # Enable investor matching

    # Phase G Sprint 2: Identity Resolution & Bi-Temporal Claims
    use_phase_g_identity_resolution: bool = False  # Enable blocking-first fuzzy entity resolution
    use_claim_facts: bool = False  # Enable bi-temporal claim facts (SCD-2)

    # Wave 2: Shadow entity resolution
    use_shadow_entity_resolution: bool = False  # Enable shadow Phase G comparison

    # Phase 2: Functional schema extraction
    use_functional_schema: bool = False  # Enable LLM schema extraction in pipeline

    # Phase 1a: Canonical identity + thin files
    use_thin_files: bool = False  # Enable thin file upsert + promotion sweep

    # Timeout configuration (Phase C0)
    collector_connect_timeout: float = 10.0   # TCP connection establishment
    collector_search_timeout: float = 60.0    # List/search endpoints (often slowest)
    collector_enrich_timeout: float = 45.0    # Detail fetching
    collector_download_timeout: float = 90.0  # Large file downloads

    # Network Scout / Warm Intro Enrichment (Phase A)
    use_warm_intro_enrichment: bool = False   # Enable warm intro enrichment
    warm_intro_notion_mode: str = "off"       # off | shadow | live
    user_email: Optional[str] = None          # Required when use_warm_intro_enrichment=True
    # Privacy boundary for relationship data. None -> resolved in __post_init__
    # via resolve_private_graph_db_path() (PRIVATE_GRAPH_DB_PATH env, else
    # beside the canonical signals DB; fails closed on in-tree paths).
    private_graph_db_path: Optional[str] = None

    # Per-collector concurrency / backpressure (Phase C)
    github_concurrency: int = 5
    github_burst: Optional[int] = None
    sec_concurrency: int = 3
    sec_burst: Optional[int] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        self.db_path = resolve_db_path_env(self.db_path)

        # Q7: never default the privacy-sensitive graph DB into the cwd.
        if self.private_graph_db_path is None:
            self.private_graph_db_path = str(resolve_private_graph_db_path())

        if self.use_warm_intro_enrichment and not self.user_email:
            raise ValueError(
                "USER_EMAIL environment variable is required when "
                "ENABLE_WARM_INTRO_ENRICHMENT is true"
            )

        # Warm intro truth-table validation
        if self.warm_intro_notion_mode not in ("off", "shadow", "live"):
            raise ValueError(
                f"warm_intro_notion_mode must be off|shadow|live, "
                f"got '{self.warm_intro_notion_mode}'"
            )
        if not self.use_warm_intro_enrichment and self.warm_intro_notion_mode == "live":
            raise ValueError(
                "warm_intro_notion_mode=live requires use_warm_intro_enrichment=True"
            )
        if not self.use_warm_intro_enrichment and self.warm_intro_notion_mode == "shadow":
            logger.warning(
                "config_override: warm_intro_notion_mode shadow->off (enrichment disabled)"
            )
            self.warm_intro_notion_mode = "off"

    @classmethod
    def from_env(cls) -> PipelineConfig:
        """Load configuration from environment variables"""
        return cls(
            # #149 guard: fail closed if the canonical DB resolves in-tree
            # (DISCOVERY_DB_PATH > SIGNAL_DB_PATH > "signals.db"). Set
            # HARMONIC_ALLOW_IN_TREE_DB=true for fixtures/dev scratch DBs.
            db_path=str(resolve_canonical_db_path()),
            asset_store_path=os.getenv("ASSET_STORE_PATH", "assets.db"),
            notion_api_key=os.getenv("NOTION_API_KEY"),
            notion_database_id=os.getenv("NOTION_DATABASE_ID"),
            watchlist_database_id=os.getenv("NOTION_WATCHLIST_DATABASE_ID"),
            github_token=os.getenv("GITHUB_TOKEN"),
            companies_house_api_key=os.getenv("COMPANIES_HOUSE_API_KEY"),
            parallel_collectors=os.getenv("PARALLEL_COLLECTORS", "true").lower() == "true",
            batch_size=int(os.getenv("BATCH_SIZE", "50")),
            read_only=os.getenv("PIPELINE_READ_ONLY", "false").lower() == "true",
            strict_mode=os.getenv("STRICT_MODE", "false").lower() == "true",
            warmup_suppression_cache=os.getenv("WARMUP_SUPPRESSION_CACHE", "true").lower() == "true",
            use_gating=os.getenv("USE_GATING", "true").lower() == "true",
            use_entities=os.getenv("USE_ENTITIES", "false").lower() == "true",
            use_asset_store=os.getenv("USE_ASSET_STORE", "false").lower() == "true",
            use_founder_scoring=os.getenv("USE_FOUNDER_SCORING", "true").lower() == "true",
            use_velocity_tracking=os.getenv("USE_VELOCITY_TRACKING", "true").lower() == "true",
            use_thesis_filter=os.getenv("USE_THESIS_FILTER", "true").lower() == "true",
            use_competitor_detection=os.getenv("USE_COMPETITOR_DETECTION", "true").lower() == "true",
            use_exit_predictor=os.getenv("ENABLE_EXIT_PREDICTOR", "false").lower() == "true",
            use_investor_matching=os.getenv("ENABLE_INVESTOR_MATCHING", "false").lower() == "true",
            use_phase_g_identity_resolution=os.getenv("USE_PHASE_G_IDENTITY_RESOLUTION", "false").lower() == "true",
            use_shadow_entity_resolution=os.getenv("USE_SHADOW_ENTITY_RESOLUTION", "false").lower() in ("true", "1"),
            use_claim_facts=os.getenv("USE_CLAIM_FACTS", "false").lower() == "true",
            use_functional_schema=os.getenv("ENABLE_FUNCTIONAL_SCHEMA", "false").lower() == "true",
            use_thin_files=os.getenv("USE_THIN_FILES", "false").lower() == "true",
            # Timeout configuration
            collector_connect_timeout=float(os.getenv("COLLECTOR_CONNECT_TIMEOUT", "10.0")),
            collector_search_timeout=float(os.getenv("COLLECTOR_SEARCH_TIMEOUT", "60.0")),
            collector_enrich_timeout=float(os.getenv("COLLECTOR_ENRICH_TIMEOUT", "45.0")),
            collector_download_timeout=float(os.getenv("COLLECTOR_DOWNLOAD_TIMEOUT", "90.0")),
            # Warm intro enrichment
            use_warm_intro_enrichment=os.getenv("ENABLE_WARM_INTRO_ENRICHMENT", "false").lower() == "true",
            warm_intro_notion_mode=os.getenv("WARM_INTRO_NOTION_MODE", "off").lower(),
            user_email=os.getenv("USER_EMAIL"),
            # Q7 guard: PRIVATE_GRAPH_DB_PATH wins, else beside the canonical
            # signals DB; fails closed on in-tree paths (see storage/db_paths.py).
            private_graph_db_path=str(resolve_private_graph_db_path()),
            # Per-collector concurrency
            github_concurrency=int(os.getenv("GITHUB_CONCURRENCY", "5")),
            github_burst=int(os.getenv("GITHUB_BURST", "0")) or None,
            sec_concurrency=int(os.getenv("SEC_CONCURRENCY", "3")),
            sec_burst=int(os.getenv("SEC_BURST", "0")) or None,
        )


@dataclass
class PipelineStats:
    """Statistics from a pipeline run"""

    # Collector stats
    collectors_run: int = 0
    collectors_succeeded: int = 0
    collectors_failed: int = 0
    collectors_skipped: int = 0  # Skipped due to missing config
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

    # Thesis filtering stats
    thesis_rejected: int = 0
    thesis_held: int = 0
    thesis_passed: int = 0

    # Notion stats
    prospects_created: int = 0
    prospects_updated: int = 0
    prospects_skipped: int = 0

    # Shadow logging stats
    shadow_logs_written: int = 0

    # Functional schema stats (Phase 2)
    schemas_extracted: int = 0

    # Identity / thin file stats (Phase 1a)
    sweep_evaluated: int = 0   # candidates examined by promotion sweep
    sweep_promoted: int = 0    # companies promoted (thin → promoted)
    sweep_pages: int = 0       # pagination pages processed
    sweep_error: Optional[str] = None  # sweep failure message, if any

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
                "skipped": self.collectors_skipped,
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
            "thesis": {
                "rejected": self.thesis_rejected,
                "held": self.thesis_held,
                "passed": self.thesis_passed,
            },
            "notion": {
                "prospects_created": self.prospects_created,
                "prospects_updated": self.prospects_updated,
                "prospects_skipped": self.prospects_skipped,
            },
            "shadow": {
                "logs_written": self.shadow_logs_written,
            },
            "errors": self.errors,
            "health": self.health_report.to_dict() if self.health_report else None,
            "timing": {
                "started_at": self.started_at.isoformat(),
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
                "duration_seconds": self.duration_seconds,
            },
        }


@dataclass
class CollectorMetrics:
    """Metrics captured for a single collector run."""
    collector_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    signals_found: int = 0
    status: str = "pending"

    # API metrics
    api_calls: int = 0
    rate_limit_hits: int = 0
    retries: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)

    def complete(self):
        """Mark as completed and calculate duration."""
        self.completed_at = datetime.now(timezone.utc)
        self.duration_seconds = (self.completed_at - self.started_at).total_seconds()


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
        self._notion_transport: Optional[NotionTransport] = None
        self._notion_outbox_worker: Optional[NotionOutboxWorker] = None
        self._watchlist_loader: Optional[WatchlistLoader] = None
        self._gate: Optional[VerificationGate] = None
        self._asset_store: Optional[SourceAssetStore] = None
        self._signal_processor: Optional[SignalProcessor] = None
        self._entity_resolver: Optional[EntityResolver] = None
        self._entity_resolution_store: Optional[EntityResolutionStore] = None
        self._health_monitor: Optional[SignalHealthMonitor] = None
        self._notifier: Optional[SlackNotifier] = None

        # Harmonic enhancements
        self._founder_store: Optional[FounderStore] = None
        self._velocity_tracker: Optional[SignalVelocityTracker] = None
        self._exit_predictor: Optional[ExitPredictor] = None
        self._investor_matcher: Optional[InvestorMatcher] = None

        # Signal consolidation
        self._consolidator: Optional[SignalConsolidator] = (
            SignalConsolidator() if self.config.use_consolidation else None
        )

        # Enrichment boost calculator (Phase 2)
        self._enrichment_calculator: Optional[EnrichmentBoostCalculator] = (
            EnrichmentBoostCalculator() if self.config.use_enrichment_boost else None
        )

        # Thesis filter (Phase 3)
        self._thesis_filter: Optional[ThesisFilter] = None
        if self.config.use_thesis_filter:
            thesis_config = ThesisFilterConfig.from_env()
            # Preserve PipelineConfig override for hold_threshold
            if self.config.thesis_hold_threshold != 0.3:
                thesis_config.hold_threshold = self.config.thesis_hold_threshold
            self._thesis_filter = ThesisFilter(thesis_config)

        # Initialize competitor detector
        self._competitor_detector: Optional[CompetitorDetector] = None
        if self.config.use_competitor_detection:
            self._competitor_detector = CompetitorDetector(self.config.portfolio_path)

        # Phase G Sprint 2: Identity Resolution & Bi-Temporal Claims
        self._identity_store: Optional[EntityIdentityStore] = None
        self._phase_g_resolver: Optional[PhaseGEntityResolver] = None
        self._claim_fact_store: Optional[ClaimFactStore] = None
        self._claim_extractor: Optional[ClaimExtractor] = None

        # Feature states for SHADOW experimentation (Phase A)
        self._feature_registry = FeatureRegistry()

        # Functional schema extractor (Phase 2)
        self._schema_extractor = None
        if self.config.use_functional_schema:
            try:
                from consumer.functional_extractor import FunctionalExtractor
                self._schema_extractor = FunctionalExtractor()
                logger.info("Functional schema extractor initialized")
            except (ImportError, ValueError) as e:
                logger.warning(f"Functional schema extractor not available: {e}")

        # Phase C: Boilerplate detection in SHADOW mode
        self._boilerplate_detector = BoilerplateDetector()

        # Phase C: Shared HTTP client for collectors
        self._shared_httpx_client: Optional[httpx.AsyncClient] = None
        self._collector_http_client: Optional[CollectorHttpClient] = None

        # HTTP request counters populated by event hooks on _shared_httpx_client
        self._http_counters: Dict[str, Dict[str, int]] = {}

        # Run tracking (per-run, not per-initialize)
        self._execution_id: str = ""
        self._run_tracking_available: bool = False

        # State
        self._initialized = False

        # Collector metrics for current run
        self._collector_metrics: List[CollectorMetrics] = []

    def _relationship_store_db_path(self) -> str:
        """Return the configured private graph path for warm intro enrichment."""
        return self.config.private_graph_db_path

    async def initialize(self, read_only: Optional[bool] = None) -> None:
        """Initialize pipeline components"""
        if read_only is not None:
            self.config.read_only = read_only

        if self._initialized:
            if self.config.read_only:
                await self._enable_read_only_mode()
            elif self._store and getattr(self._store, "read_only", False):
                raise RuntimeError(
                    "DiscoveryPipeline cannot switch from read-only to write mode; "
                    "create a new pipeline instance for live processing"
                )
            return

        logger.info(
            "Initializing discovery pipeline%s...",
            " (read-only)" if self.config.read_only else "",
        )

        # Initialize signal store
        self._store = SignalStore(
            db_path=self.config.db_path,
            use_thin_files=self.config.use_thin_files,
            read_only=self.config.read_only,
        )
        await self._store.initialize()

        # Initialize Notion connector (if credentials provided)
        if self.config.read_only:
            logger.info("Read-only mode enabled - Notion writes and warmup are disabled")
        elif self.config.notion_api_key and self.config.notion_database_id:
            self._notion_transport = NotionTransport(api_key=self.config.notion_api_key)
            await self._notion_transport.start()

            self._notion = NotionConnector(
                api_key=self.config.notion_api_key,
                database_id=self.config.notion_database_id,
                transport=self._notion_transport,
            )
            self._notion_outbox_worker = NotionOutboxWorker(
                signal_store=self._store,
                notion_connector=self._notion,
            )
            if self.config.watchlist_database_id:
                self._watchlist_loader = WatchlistLoader(
                    database_id=self.config.watchlist_database_id,
                    transport=self._notion_transport,
                )
            logger.info("Notion connector initialized")
        else:
            logger.warning("Notion credentials not provided - push operations will be disabled")

        # Initialize verification gate
        self._gate = VerificationGate(strict_mode=self.config.strict_mode)

        # Initialize SourceAssetStore (if enabled)
        if self.config.use_asset_store and self.config.read_only:
            logger.info("Read-only mode enabled - SourceAssetStore initialization skipped")
        elif self.config.use_asset_store:
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

            # Initialize EntityResolutionStore
            self._entity_resolution_store = EntityResolutionStore(
                db_path=self.config.db_path,
                read_only=self.config.read_only,
            )
            await self._entity_resolution_store.initialize()

            logger.info("EntityResolver + EntityResolutionStore initialized")

        # Initialize FounderStore (if founder scoring enabled)
        if self.config.use_founder_scoring:
            self._founder_store = FounderStore(
                db_path=self.config.db_path,
                read_only=self.config.read_only,
            )
            await self._founder_store.initialize()
            logger.info("FounderStore initialized (founder intelligence enabled)")

        # Initialize SignalVelocityTracker (if velocity tracking enabled)
        if self.config.use_velocity_tracking:
            velocity_config = VelocityConfig()
            self._velocity_tracker = SignalVelocityTracker(self._store, velocity_config)
            logger.info("SignalVelocityTracker initialized (momentum detection enabled)")

        # Initialize ExitPredictor (if exit prediction enabled)
        if self.config.use_exit_predictor:
            self._exit_predictor = ExitPredictor(
                founder_store=self._founder_store,
                velocity_tracker=self._velocity_tracker,
                signal_store=self._store,
            )
            logger.info("ExitPredictor initialized (exit prediction enabled)")

        # Initialize InvestorMatcher (if investor matching enabled)
        if self.config.use_investor_matching:
            self._investor_matcher = InvestorMatcher(self._store)
            logger.info("InvestorMatcher initialized (investor matching enabled)")

        # Initialize Phase G Sprint 2 components (if identity resolution enabled)
        if self.config.use_phase_g_identity_resolution:
            self._identity_store = EntityIdentityStore(self._store)
            self._phase_g_resolver = PhaseGEntityResolver(self._identity_store)
            logger.info("PhaseGEntityResolver initialized (blocking-first fuzzy matching enabled)")

        # Phase 1a: Identity store for thin files (create if not already from Phase G)
        if self.config.use_thin_files and not self._identity_store:
            self._identity_store = EntityIdentityStore(self._store)
            logger.info("EntityIdentityStore initialized for thin files")

        # Wire identity store into SignalStore for save_signal() resolution
        if self._identity_store:
            self._store._identity_store = self._identity_store

        # Phase 1a: Validate Phase G tables exist when identity features active
        if self.config.use_thin_files or self.config.use_phase_g_identity_resolution:
            await self._validate_phase_g_tables()

        # Phase 1a: Identity gate — block pipeline if signals have NULL company_id
        if self.config.use_thin_files:
            await check_identity_integrity(self._store)
            logger.info("Identity gate passed")

        # Initialize Claim Fact Store (if claim facts enabled)
        if self.config.use_claim_facts:
            self._claim_fact_store = ClaimFactStore(self._store)
            self._claim_extractor = ClaimExtractor()
            logger.info("ClaimFactStore + ClaimExtractor initialized (bi-temporal claims enabled)")

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

        # Phase C: Create shared HTTP client for collectors with event hooks
        # for per-collector api_calls / rate_limit_hits attribution
        async def _on_request(request):
            name = _current_collector.get("unknown")
            if name not in self._http_counters:
                self._http_counters[name] = {"api_calls": 0, "rate_limit_hits": 0}
            self._http_counters[name]["api_calls"] += 1

        async def _on_response(response):
            name = _current_collector.get("unknown")
            if name in self._http_counters:
                status = response.status_code
                if status == 429:
                    self._http_counters[name]["rate_limit_hits"] += 1
                elif status == 403:
                    # GitHub-specific: secondary rate limit (abuse detection)
                    if "api.github.com" in str(response.url):
                        self._http_counters[name]["rate_limit_hits"] += 1

        self._shared_httpx_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )
        logger.info("Shared HTTP client initialized (Phase C)")

        # Warmup suppression cache (non-fatal if it fails)
        if self.config.read_only and self.config.warmup_suppression_cache:
            logger.info("Read-only mode enabled - suppression cache warmup skipped")
        elif self.config.warmup_suppression_cache:
            try:
                await self._warmup_suppression_cache()
            except Exception as e:
                logger.warning(f"Suppression cache warmup failed (non-fatal): {e}")

        self._initialized = True
        logger.info("Pipeline initialization complete")

    async def _enable_read_only_mode(self) -> None:
        """Turn existing store connections into defensive no-write connections."""
        self.config.read_only = True
        if self._store:
            await self._store.enable_read_only()
        if self._entity_resolution_store:
            await self._entity_resolution_store.enable_read_only()
        if self._founder_store:
            await self._founder_store.enable_read_only()

    async def close(self) -> None:
        """Clean up resources"""
        if self._store:
            await self._store.close()
        if self._asset_store:
            await self._asset_store.close()
            self._asset_store = None
        if self._entity_resolution_store:
            await self._entity_resolution_store.close()
            self._entity_resolution_store = None
        if self._founder_store:
            await self._founder_store.close()
            self._founder_store = None
        if self._notion_transport:
            await self._notion_transport.shutdown()
            self._notion_transport = None
        self._notion_outbox_worker = None
        self._notion = None
        self._watchlist_loader = None
        if self._notifier:
            await self._notifier.close()
            self._notifier = None
        # Phase C: Close shared HTTP client
        if self._shared_httpx_client:
            await self._shared_httpx_client.aclose()
            self._shared_httpx_client = None
        self._collector_http_client = None
        self._velocity_tracker = None
        self._initialized = False

    # =========================================================================
    # RUN TRACKING (per-run, not per-initialize)
    # =========================================================================

    async def _begin_run_tracking(
        self, run_type: str = "pipeline", inputs_summary: Optional[dict] = None
    ) -> str:
        """Create run + start it. Returns execution_id. Never raises."""
        try:
            record = await create_run(
                self._store, run_type=run_type, inputs_summary=inputs_summary
            )
            self._execution_id = record.id
            self._run_tracking_available = True
            try:
                await start_run(self._store, self._execution_id)
            except Exception:
                logger.warning("start_run failed for %s; continuing", self._execution_id)
        except Exception:
            self._execution_id = uuid.uuid4().hex[:16]
            self._run_tracking_available = False
            logger.warning(
                "run_history unavailable, using local execution_id=%s",
                self._execution_id,
            )
        # Create CollectorHttpClient for this run
        if self._shared_httpx_client:
            self._collector_http_client = CollectorHttpClient(
                self._shared_httpx_client,
                run_context=RunContext(execution_id=self._execution_id),
                collector_name="pipeline",
            )
        return self._execution_id

    async def _end_run_tracking(
        self, *, success: bool, stats: Optional[dict] = None, error: Optional[str] = None
    ) -> None:
        """Complete or fail the run. Never raises."""
        if not self._run_tracking_available:
            return
        try:
            if success:
                await complete_run(self._store, self._execution_id, result=stats)
            else:
                await fail_run(
                    self._store, self._execution_id, error_message=error or "unknown"
                )
        except Exception:
            logger.warning(
                "run_history write failed for %s; continuing", self._execution_id
            )

    async def _run_promotion_sweep(self, stats: PipelineStats) -> None:
        """Run paginated promotion sweep on updated CompanyFiles.

        Promotes thin files that meet criteria and creates ReviewItems.
        Non-fatal: logs errors but does not fail the pipeline.
        Writes sweep counters into *stats* for persistence in pipeline_runs.
        """
        try:
            total_promoted = 0
            pages = 0
            last_seen_cursor = None
            company_id_cursor = None

            while True:
                promoted, last_seen_cursor, company_id_cursor = (
                    await run_promotion_sweep(
                        self._store,
                        last_seen_cursor=last_seen_cursor,
                        company_id_cursor=company_id_cursor,
                    )
                )
                total_promoted += promoted
                pages += 1

                # No more pages
                if last_seen_cursor is None:
                    break

            stats.sweep_promoted = total_promoted
            stats.sweep_pages = pages

            if total_promoted > 0:
                logger.info(f"Promotion sweep: {total_promoted} companies promoted")
            else:
                logger.debug("Promotion sweep: no new promotions")

            # Audit: record sweep completion
            await self._write_sweep_audit(
                promoted=total_promoted, pages=pages, error=None,
            )

        except Exception as e:
            stats.sweep_error = str(e)
            logger.warning(f"Promotion sweep failed (non-fatal): {e}")
            # Audit: record sweep failure
            try:
                await self._write_sweep_audit(
                    promoted=0, pages=0, error=str(e),
                )
            except Exception:
                pass  # best-effort

    async def _write_sweep_audit(
        self,
        promoted: int,
        pages: int,
        error: Optional[str],
    ) -> None:
        """Write a single audit_log entry recording the promotion sweep outcome."""
        import json as _json
        now_iso = datetime.now(timezone.utc).isoformat()
        details = _json.dumps({
            "promoted": promoted,
            "pages": pages,
            "error": error,
        })
        await self._store._db.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("promotion_sweep", "pipeline", "sweep", "pipeline", details, now_iso),
        )
        await self._store._db.commit()

    async def _validate_phase_g_tables(self) -> None:
        """Validate that Phase G tables exist (migration 19+).

        Checks for both entity_aliases and entity_migrations tables.
        Raises RuntimeError with actionable message if missing.
        """
        required_tables = ["entity_aliases", "entity_migrations"]
        cursor = await self._store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
            tuple(required_tables),
        )
        found = {row[0] for row in await cursor.fetchall()}
        missing = [t for t in required_tables if t not in found]

        if missing:
            raise RuntimeError(
                f"Phase 1a requires Phase G tables (migration 19). "
                f"Missing: {missing}. "
                f"Run: python -m storage.migrate --db \"$DISCOVERY_DB_PATH\""
            )

        logger.debug("Phase G table validation passed")

    async def _warmup_suppression_cache(self) -> None:
        """
        Warm up suppression cache from Notion on pipeline startup.

        This ensures the local cache is fresh before processing signals,
        preventing duplicate pushes to Notion on first run.

        Non-fatal: Called with try/except in initialize().
        """
        if self.config.read_only:
            logger.info("Read-only mode enabled, skipping suppression cache warmup")
            return

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

    async def _save_pipeline_metrics(
        self,
        stats: PipelineStats,
    ) -> Optional[str]:
        """
        Save pipeline run and collector metrics to database.

        Returns run_id if successful, None otherwise.
        """
        try:
            run_id = await self._store.save_pipeline_run(stats)
            logger.info(f"Pipeline metrics saved (run_id: {run_id})")

            # Save collector metrics
            for metrics in self._collector_metrics:
                await self._store.save_collector_metrics(run_id, metrics)

            logger.info(f"Saved {len(self._collector_metrics)} collector metrics")
            return run_id

        except Exception as e:
            logger.warning(f"Failed to save pipeline metrics (non-fatal): {e}")
            return None

    async def run_full_pipeline(
        self,
        collectors: Optional[List[str]] = None,
        dry_run: bool = True,
        progress_callback=None,
    ) -> PipelineStats:
        """
        Run the complete discovery pipeline.

        Stages:
        1. Run collectors (parallel or sequential)
        2. Store signals in SQLite
        3. Check suppression cache
        4. Run through verification gate
        5. Queue Notion writes and drain outbox (if not dry_run)

        Args:
            collectors: List of collector names to run (None = all available)
            dry_run: If True, don't actually queue or push to Notion
            progress_callback: Optional callable(phase: int, total: int, msg: str)
                for reporting progress to the caller.

        Returns:
            PipelineStats with detailed metrics
        """
        def _progress(phase: int, total: int, msg: str):
            if progress_callback:
                progress_callback(phase, total, msg)
        await self.initialize()

        # Per-run tracking (with config pinning)
        from monitoring.feature_gate import compute_config_snapshot
        await self._begin_run_tracking(
            "pipeline",
            {
                "collectors": collectors or [],
                "dry_run": dry_run,
                "mode": "full",
                "config_snapshot": compute_config_snapshot(),
            },
        )

        stats = PipelineStats()

        # Reset collector metrics for this run
        self._collector_metrics = []
        run_error: Optional[str] = None

        try:
            logger.info(
                f"Starting full pipeline (collectors={collectors}, dry_run={dry_run})"
            )

            # Stage 1: Collect signals
            _progress(1, 3, f"Collecting signals... ({len(collectors or [])} collectors)")
            collector_results = await self._run_collectors_stage(collectors or [], dry_run)
            stats.collectors_run = len(collector_results)
            stats.collectors_succeeded = sum(
                1 for r in collector_results if r.status == CollectorStatus.SUCCESS
            )
            stats.collectors_failed = sum(
                1 for r in collector_results if r.status == CollectorStatus.ERROR
            )
            stats.collectors_skipped = sum(
                1 for r in collector_results if r.status == CollectorStatus.SKIPPED
            )
            stats.signals_collected = sum(r.signals_found for r in collector_results)
            stats.signals_stored = sum(
                getattr(r, "signals_new", 0) or 0 for r in collector_results
            )
            stats.signals_deduplicated = sum(
                getattr(r, "signals_suppressed", 0) or 0
                for r in collector_results
            )
            # Note: signals_suppressed includes in-run dedup, DB-level dedup,
            # and Notion suppression cache hits. All represent "not stored
            # because already known."
            # getattr guards against collectors that bypass BaseCollector or
            # return partial CollectorResult objects.

            # Stage 1.5: Run promotion sweep on updated CompanyFiles
            if self.config.use_thin_files:
                await self._run_promotion_sweep(stats)

            # Stage 2: Process pending signals
            _progress(2, 3, "Processing signals...")
            process_stats = await self._process_signals_stage(dry_run)
            stats.signals_processed = process_stats["processed"]
            stats.signals_auto_push = process_stats["auto_push"]
            stats.signals_needs_review = process_stats["needs_review"]
            stats.signals_held = process_stats["held"]
            stats.signals_rejected = process_stats["rejected"]
            stats.prospects_created = process_stats["prospects_created"]
            stats.prospects_updated = process_stats["prospects_updated"]
            stats.prospects_skipped = process_stats["prospects_skipped"]
            stats.schemas_extracted = process_stats.get("schemas_extracted", 0)

            if not dry_run:
                outbox_stats = await self._drain_notion_outbox(limit=self.config.batch_size)
                if outbox_stats["processed"] > 0:
                    stats.prospects_created = outbox_stats["created"]
                    stats.prospects_updated = outbox_stats["updated"]
                    stats.prospects_skipped = outbox_stats["skipped"]

            # Stage 3: Finalize
            _progress(3, 3, "Finalizing...")

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

            # Daily aggregator hook — keep SPC baseline current
            if not dry_run:
                try:
                    from monitoring.daily_aggregator import backfill_daily_metrics
                    import sqlite3 as _sqlite3
                    sync_conn = _sqlite3.connect(str(self._store.db_path), timeout=5)
                    try:
                        sync_conn.execute("PRAGMA busy_timeout=5000")
                        backfill_daily_metrics(sync_conn, days=15)
                    finally:
                        sync_conn.close()
                except Exception as exc:
                    logger.warning("Daily aggregator hook failed (non-fatal): %s", exc)

            logger.info("Full pipeline completed successfully")

        except Exception as e:
            logger.exception("Pipeline failed")
            stats.errors.append(f"Pipeline error: {str(e)}")
            run_error = str(e)

        finally:
            stats.complete()

            # Save metrics to database (non-fatal)
            if self._store and not dry_run:
                await self._save_pipeline_metrics(stats)

            # End run tracking
            await self._end_run_tracking(
                success=run_error is None,
                stats={"signals_collected": stats.signals_collected} if run_error is None else None,
                error=run_error,
            )

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

        from monitoring.feature_gate import compute_config_snapshot
        await self._begin_run_tracking(
            "pipeline",
            {
                "mode": "collect_only",
                "collectors": collector_names,
                "dry_run": dry_run,
                "config_snapshot": compute_config_snapshot(),
            },
        )

        # Reset collector metrics for this run
        self._collector_metrics = []

        logger.info(f"Running collectors: {collector_names} (dry_run={dry_run})")

        run_error: Optional[str] = None
        try:
            results = await self._run_collectors_stage(collector_names, dry_run)
        except Exception as e:
            run_error = str(e)
            raise
        finally:
            # Save metrics (collect-only mode) — skip on dry_run to preserve read-only invariant
            if self._store and not dry_run and self._collector_metrics:
                try:
                    stats = PipelineStats()
                    stats.collectors_run = len(self._collector_metrics)
                    stats.collectors_succeeded = sum(
                        1 for m in self._collector_metrics if m.status == "success"
                    )
                    stats.collectors_failed = sum(
                        1 for m in self._collector_metrics if m.status == "error"
                    )
                    stats.collectors_skipped = sum(
                        1 for m in self._collector_metrics if m.status == "skipped"
                    )
                    stats.signals_collected = sum(
                        m.signals_found for m in self._collector_metrics
                    )
                    if run_error:
                        stats.errors.append(run_error)
                    stats.complete()
                    await self._save_pipeline_metrics(stats)
                except Exception as save_err:
                    logger.warning(
                        f"Failed to save collect-mode metrics (non-fatal): {save_err}"
                    )

            try:
                await self._end_run_tracking(
                    success=run_error is None, error=run_error,
                )
            except Exception as track_err:
                logger.warning(
                    f"Failed to end run tracking (non-fatal): {track_err}"
                )

        return results

    async def process_pending(self, dry_run: bool = False, source_api: Optional[str] = None) -> Dict[str, int]:
        """
        Process all pending signals in the store.

        Steps:
        1. Load pending signals from SQLite
        2. Check suppression cache
        3. Run through verification gate
        4. Queue Notion writes and drain outbox (if not dry_run)

        Args:
            dry_run: If True, don't actually queue or push to Notion
            source_api: If set, only process signals from this source API

        Returns:
            Dictionary with processing statistics
        """
        await self.initialize(read_only=dry_run)

        from monitoring.feature_gate import compute_config_snapshot
        if dry_run:
            logger.info("Dry-run process is read-only; persistent run tracking skipped")
        else:
            await self._begin_run_tracking(
                "pipeline",
                {
                    "mode": "process_only",
                    "dry_run": dry_run,
                    "source_api_filter": source_api,
                    "config_snapshot": compute_config_snapshot(),
                },
            )

        logger.info(f"Processing pending signals (dry_run={dry_run}, source_api={source_api})")

        run_error: Optional[str] = None
        try:
            process_stats = await self._process_signals_stage(dry_run, source_api=source_api)

            if not dry_run:
                outbox_stats = await self._drain_notion_outbox(limit=self.config.batch_size)
                if outbox_stats["processed"] > 0:
                    process_stats["prospects_created"] = outbox_stats["created"]
                    process_stats["prospects_updated"] = outbox_stats["updated"]
                    process_stats["prospects_skipped"] = outbox_stats["skipped"]
        except Exception as e:
            run_error = str(e)
            raise
        finally:
            if not dry_run:
                await self._end_run_tracking(
                    success=run_error is None, error=run_error,
                )

        return process_stats

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

        # Materialize configured collectors before runtime updates.  This keeps
        # missing-key / intentionally disabled collectors visible to health
        # tooling even when they are not part of the current run.
        try:
            initialize_collector_state(runner="pipeline")
        except Exception as heartbeat_error:
            logger.warning(
                "Collector state bootstrap failed before collector stage: %s",
                heartbeat_error,
            )

        # Reset per-run HTTP counters so previous runs don't bleed through
        self._http_counters.clear()

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

    def _open_data_version_probe(self) -> Optional[sqlite3.Connection]:
        """Open a same-connection PRAGMA data_version probe for a collector run."""
        if not self._store:
            return None
        db_path = Path(getattr(self._store, "db_path", self.config.db_path))
        if str(db_path) == ":memory:" or not db_path.exists():
            return None
        try:
            return sqlite3.connect(str(db_path))
        except sqlite3.Error as exc:
            logger.debug("Could not open data_version probe for %s: %s", db_path, exc)
            return None

    @staticmethod
    def _read_data_version_probe(
        connection: Optional[sqlite3.Connection],
    ) -> Optional[int]:
        if connection is None:
            return None
        try:
            row = connection.execute("PRAGMA data_version").fetchone()
            return int(row[0]) if row else None
        except sqlite3.Error as exc:
            logger.debug("Could not read data_version probe: %s", exc)
            return None

    async def _count_recent_signals_for_collector(
        self,
        collector_name: str,
    ) -> Optional[int]:
        """Count this collector's DB-visible rows in the last 24 hours."""
        if not self._store or not getattr(self._store, "_db", None):
            return None
        source_apis = DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR.get(
            collector_name,
            (collector_name,),
        )
        if not source_apis:
            return 0
        placeholders = ",".join("?" for _ in source_apis)
        try:
            cursor = await self._store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
            )
            if await cursor.fetchone() is None:
                return 0
            cursor = await self._store._db.execute(
                f"""
                SELECT COUNT(*)
                FROM signals
                WHERE detected_at > datetime('now', '-24 hours')
                  AND source_api IN ({placeholders})
                """,
                tuple(source_apis),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error as exc:
            logger.debug(
                "Could not count recent signals for collector %s: %s",
                collector_name,
                exc,
            )
            return None

    async def _run_single_collector(
        self,
        collector_name: str,
        dry_run: bool,
    ) -> CollectorResult:
        """Run a single collector and return results"""
        # Start timing
        metrics = CollectorMetrics(
            collector_name=collector_name,
            started_at=datetime.now(timezone.utc),
        )
        collector = None
        result: Optional[CollectorResult] = None
        data_version_probe = self._open_data_version_probe()
        data_version_before = self._read_data_version_probe(data_version_probe)
        data_version_after: Optional[int] = None
        rows_total_last_24h: Optional[int] = None
        collector_class: Optional[str] = None

        def _make_result(**kwargs: Any) -> CollectorResult:
            nonlocal result
            result = CollectorResult(**kwargs)
            return result

        # Set ContextVar so httpx event hooks attribute requests to this collector
        token = _current_collector.set(collector_name)
        try:
            logger.info(f"Running collector: {collector_name}")

            # Common parameters for all collectors
            common_args: Dict[str, Any] = {
                "store": self._store,
            }
            # Phase C: Pass shared HTTP client to migrated collectors
            if self._collector_http_client:
                common_args["http"] = self._collector_http_client
            # Only include asset_store if enabled (collectors may not accept it)
            if self.config.use_asset_store and self._asset_store:
                common_args["asset_store"] = self._asset_store

            # Import collector dynamically based on name
            if collector_name == "github":
                from collectors.github import GitHubCollector, TopicMode
                collector = GitHubCollector(
                    **common_args,
                    github_token=self.config.github_token,
                    topic_mode=TopicMode.CONSUMER,  # Consumer thesis focus
                )
            elif collector_name == "sec_edgar":
                from collectors.sec_edgar import SECEdgarCollector
                all_filings = os.getenv("SEC_EDGAR_ALL_FILINGS", "").lower() in ("true", "1", "yes")
                if all_filings:
                    logger.info("SEC Edgar SIC filter bypassed (SEC_EDGAR_ALL_FILINGS=true)")
                collector = SECEdgarCollector(**common_args, target_sectors_only=not all_filings)
            elif collector_name == "companies_house":
                from collectors.companies_house import CompaniesHouseCollector
                collector = CompaniesHouseCollector(
                    **common_args,
                    api_key=self.config.companies_house_api_key
                )
            elif collector_name == "domain_whois":
                from collectors.domain_whois import DomainWhoisCollector
                collector = DomainWhoisCollector(**common_args)
            elif collector_name == "product_hunt":
                from collectors.product_hunt import ProductHuntCollector
                collector = ProductHuntCollector(
                    **common_args,
                    api_key=os.getenv("PH_API_KEY")
                )
            elif collector_name == "hacker_news":
                from collectors.hacker_news import HackerNewsCollector
                collector = HackerNewsCollector(**common_args)
            elif collector_name == "arxiv":
                from collectors.arxiv import ArxivCollector
                collector = ArxivCollector(**common_args)
            elif collector_name == "job_postings":
                from collectors.job_postings import JobPostingsCollector
                # Job postings requires domains to scan - use configured or default
                job_domains = os.getenv("JOB_POSTING_DOMAINS", "").split(",")
                job_domains = [d.strip() for d in job_domains if d.strip()]
                if not job_domains:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No JOB_POSTING_DOMAINS configured",
                        dry_run=dry_run,
                    )
                collector = JobPostingsCollector(**common_args, domains=job_domains)
            elif collector_name == "github_activity":
                from collectors.github_activity import GitHubActivityCollector
                # GitHub activity requires usernames or org names
                gh_usernames = os.getenv("GITHUB_ACTIVITY_USERNAMES", "").split(",")
                gh_usernames = [u.strip() for u in gh_usernames if u.strip()]
                gh_orgs = os.getenv("GITHUB_ACTIVITY_ORGS", "").split(",")
                gh_orgs = [o.strip() for o in gh_orgs if o.strip()]
                if not gh_usernames and not gh_orgs:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No GITHUB_ACTIVITY_USERNAMES or GITHUB_ACTIVITY_ORGS configured",
                        dry_run=dry_run,
                    )
                collector = GitHubActivityCollector(
                    **common_args,
                    usernames=gh_usernames if gh_usernames else None,
                    org_names=gh_orgs if gh_orgs else None,
                )
            elif collector_name == "linkedin":
                from collectors.linkedin import LinkedInCollector
                linkedin_key = os.getenv("PROXYCURL_API_KEY")
                if not linkedin_key:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No PROXYCURL_API_KEY configured",
                        dry_run=dry_run,
                    )
                # LinkedIn requires company URLs to scan
                linkedin_urls = os.getenv("LINKEDIN_COMPANY_URLS", "").split(",")
                linkedin_urls = [u.strip() for u in linkedin_urls if u.strip()]
                collector = LinkedInCollector(
                    **common_args,
                    api_key=linkedin_key,
                    company_urls=linkedin_urls if linkedin_urls else None,
                )
            elif collector_name == "crunchbase":
                from collectors.crunchbase import CrunchbaseCollector
                cb_key = os.getenv("CRUNCHBASE_API_KEY")
                if not cb_key:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No CRUNCHBASE_API_KEY configured",
                        dry_run=dry_run,
                    )
                collector = CrunchbaseCollector(**common_args, api_key=cb_key)
            elif collector_name == "uspto":
                from collectors.uspto import USPTOCollector
                collector = USPTOCollector(**common_args)
            elif collector_name == "opencorporates":
                from collectors.opencorporates import OpenCorporatesCollector
                oc_key = os.getenv("OPENCORPORATES_API_KEY")
                if not oc_key:
                    logger.warning("OPENCORPORATES_API_KEY not set - rate limits apply")
                collector = OpenCorporatesCollector(
                    **common_args,
                    api_key=oc_key,
                )
            elif collector_name == "telegram":
                from collectors.telegram import TelegramCollector
                telegram_api_id = os.getenv("TELEGRAM_API_ID")
                telegram_api_hash = os.getenv("TELEGRAM_API_HASH")
                if not telegram_api_id or not telegram_api_hash:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No TELEGRAM_API_ID or TELEGRAM_API_HASH configured",
                        dry_run=dry_run,
                    )
                # Get target channels from env
                telegram_channels = os.getenv("TELEGRAM_CHANNELS", "").split(",")
                telegram_channels = [c.strip() for c in telegram_channels if c.strip()]
                collector = TelegramCollector(
                    **common_args,
                    api_id=telegram_api_id,
                    api_hash=telegram_api_hash,
                    channels=telegram_channels if telegram_channels else None,
                )
            elif collector_name == "discord":
                from collectors.discord import DiscordCollector
                discord_token = os.getenv("DISCORD_BOT_TOKEN")
                if not discord_token:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No DISCORD_BOT_TOKEN configured",
                        dry_run=dry_run,
                    )
                # Get target servers from env
                discord_servers = os.getenv("DISCORD_SERVER_IDS", "").split(",")
                discord_servers = [int(s.strip()) for s in discord_servers if s.strip()]
                collector = DiscordCollector(
                    **common_args,
                    bot_token=discord_token,
                    guild_ids=discord_servers if discord_servers else None,
                )
            elif collector_name == "news_api":
                from collectors.news_api import NewsAPICollector
                gnews_key = os.getenv("GNEWS_API_KEY")
                if not gnews_key:
                    metrics.status = "skipped"
                    return _make_result(
                        collector=collector_name,
                        status=CollectorStatus.SKIPPED,
                        error_message="No GNEWS_API_KEY configured",
                        dry_run=dry_run,
                    )
                collector = NewsAPICollector(**common_args, api_key=gnews_key)
            elif collector_name == "rss_feeds":
                from collectors.rss_feeds import RSSFeedCollector
                # RSS feeds don't require API key - use default feeds or custom from env
                rss_feeds = os.getenv("RSS_FEEDS", "").split(",")
                rss_feeds = [f.strip() for f in rss_feeds if f.strip()]
                rss_categories = os.getenv("RSS_CATEGORIES", "").split(",")
                rss_categories = [c.strip() for c in rss_categories if c.strip()]
                collector = RSSFeedCollector(
                    **common_args,
                    feeds=rss_feeds if rss_feeds else None,
                    categories=rss_categories if rss_categories else None,
                )
            else:
                metrics.status = "error"
                metrics.error_messages = [f"Unknown collector: {collector_name}"]
                return _make_result(
                    collector=collector_name,
                    status=CollectorStatus.ERROR,
                    error_message=f"Unknown collector: {collector_name}",
                    dry_run=dry_run,
                )

            # Run collector
            collector_class = collector.__class__.__name__
            result = await collector.run(dry_run=dry_run)
            data_version_after = self._read_data_version_probe(data_version_probe)
            rows_total_last_24h = await self._count_recent_signals_for_collector(
                collector_name
            )

            # Capture metrics from collector
            metrics.signals_found = result.signals_found
            metrics.status = result.status.value
            metrics.retries = getattr(collector, '_retry_count', 0)
            metrics.errors = len(getattr(collector, '_errors', []))
            metrics.error_messages = getattr(collector, '_errors', [])

            # Wire api_calls and rate_limit_hits from httpx event hooks
            counters = self._http_counters.get(collector_name, {})
            metrics.api_calls = counters.get("api_calls", 0)
            metrics.rate_limit_hits = counters.get("rate_limit_hits", 0)

            logger.info(
                f"Collector {collector_name} completed: "
                f"{result.signals_found} signals found"
            )

            return result

        except Exception as e:
            logger.exception(f"Error running collector {collector_name}")
            metrics.status = "error"
            metrics.errors = 1
            metrics.error_messages = [str(e)]
            # Try to capture retry count from collector if it was created
            if collector is not None:
                metrics.retries = getattr(collector, '_retry_count', 0)
            return _make_result(
                collector=collector_name,
                status=CollectorStatus.ERROR,
                error_message=str(e),
                dry_run=dry_run,
            )
        finally:
            _current_collector.reset(token)
            # Always capture timing
            metrics.complete()
            if result is not None:
                if collector_class is None and collector is not None:
                    collector_class = collector.__class__.__name__
                if data_version_after is None:
                    data_version_after = self._read_data_version_probe(data_version_probe)
                if rows_total_last_24h is None:
                    rows_total_last_24h = await self._count_recent_signals_for_collector(
                        collector_name
                    )
                try:
                    record_collector_heartbeat(
                        result=result,
                        started_at=metrics.started_at,
                        finished_at=metrics.completed_at,
                        duration_seconds=metrics.duration_seconds,
                        api_calls=metrics.api_calls,
                        rate_limit_hits=metrics.rate_limit_hits,
                        retries=metrics.retries,
                        errors=metrics.errors,
                        error_messages=metrics.error_messages,
                        data_version_before=data_version_before,
                        data_version_after=data_version_after,
                        rows_inserted_this_iter=getattr(result, "signals_new", 0),
                        rows_total_last_24h=rows_total_last_24h,
                        collector_class=collector_class,
                        runner="pipeline",
                    )
                except Exception as heartbeat_error:
                    logger.warning(
                        "Collector heartbeat update failed for %s: %s",
                        collector_name,
                        heartbeat_error,
                    )
            if data_version_probe is not None:
                data_version_probe.close()
            self._collector_metrics.append(metrics)

    async def _process_signals_stage(self, dry_run: bool, source_api: Optional[str] = None) -> Dict[str, int]:
        """
        Process pending signals through verification and Notion queueing.

        Args:
            dry_run: If True, don't push to Notion
            source_api: If set, only process signals from this source API

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
            "signals_consolidated": 0,
            "conflicts_detected": 0,
            "enrichment_boosts_applied": 0,
            "total_enrichment_boost": 0.0,
            # Thesis filtering stats
            "thesis_rejected": 0,
            "thesis_held": 0,
            "thesis_passed": 0,
            # Functional schema stats (Phase 2)
            "schemas_extracted": 0,
            # Phase G Sprint 2 stats
            "phase_g_entities_resolved": 0,
            "phase_g_merges": 0,
            "claim_facts_extracted": 0,
            "claim_facts_saved": 0,
        }

        # Get pending signals
        pending = await self._store.get_pending_signals(limit=self.config.batch_size, source_api=source_api)

        if not pending:
            logger.info("No pending signals to process")
            return stats

        logger.info(f"Processing {len(pending)} pending signals")

        # Group by canonical key
        by_key: Dict[str, List[StoredSignal]] = {}
        for signal in pending:
            by_key.setdefault(signal.canonical_key, []).append(signal)

        logger.info(f"Grouped into {len(by_key)} unique companies")

        # Re-group by entity resolution (consolidate multi-asset companies)
        if self.config.use_entities:
            by_key = await self._regroup_signals_by_entity(by_key)
            logger.info(f"After entity regrouping: {len(by_key)} unique entities")

        # Phase G Sprint 2: Identity resolution with blocking-first fuzzy matching
        entity_id_map: Dict[str, str] = {}  # canonical_key -> entity_id
        if self.config.use_phase_g_identity_resolution and self._phase_g_resolver:
            by_key, entity_id_map, phase_g_stats = await self._apply_phase_g_identity_resolution(
                pending, by_key, dry_run=dry_run
            )
            stats["phase_g_entities_resolved"] = phase_g_stats.get("entities_resolved", 0)
            stats["phase_g_merges"] = phase_g_stats.get("merges", 0)
            logger.info(
                f"After Phase G resolution: {len(by_key)} entities "
                f"({stats['phase_g_merges']} merges)"
            )

        # Wave 2: Shadow entity resolution (read-only comparison)
        if self.config.use_shadow_entity_resolution:
            if dry_run:
                logger.info("Dry-run: skipping persistent shadow entity comparison")
                stats["shadow_entity"] = {"status": "skipped", "reason": "dry_run"}
            else:
                try:
                    shadow_stats = await self._run_shadow_entity_comparison(pending)
                    stats["shadow_entity"] = shadow_stats
                except Exception as e:
                    logger.warning("Shadow entity comparison failed (non-fatal): %s", e)
                    stats["shadow_entity"] = {"error": str(e)}

        # Consolidate signals if enabled
        consolidated_map: Dict[str, ConsolidatedSignal] = {}
        if self._consolidator:
            for key, sigs in by_key.items():
                consolidated_map[key] = self._consolidator.consolidate(sigs)

            conflicts = sum(1 for c in consolidated_map.values() if c.has_conflicts)
            if conflicts:
                logger.warning(f"Signal consolidation found {conflicts} companies with conflicts")

            # Update consolidation metrics
            stats["signals_consolidated"] = sum(
                c.signal_count for c in consolidated_map.values()
            )
            stats["conflicts_detected"] = sum(
                1 for c in consolidated_map.values() if c.has_conflicts
            )

        # Process each company
        for canonical_key, company_signals in by_key.items():
            try:
                consolidated = consolidated_map.get(canonical_key)
                result = await self._process_company(
                    company_signals, dry_run, consolidated=consolidated
                )

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

                # Track enrichment metrics
                enrichment_boost = result.get("enrichment_boost", 0.0)
                if enrichment_boost > 0:
                    stats["enrichment_boosts_applied"] += 1
                    stats["total_enrichment_boost"] += enrichment_boost

                # Track thesis filtering metrics
                thesis_routing = result.get("thesis_routing")
                if thesis_routing == RoutingDecision.REJECTED:
                    stats["thesis_rejected"] += 1
                elif thesis_routing == RoutingDecision.HELD:
                    stats["thesis_held"] += 1
                elif thesis_routing == RoutingDecision.QUALIFIED:
                    stats["thesis_passed"] += 1

                # Track schema extraction
                if result.get("schema_extracted"):
                    stats["schemas_extracted"] += 1

            except Exception as e:
                logger.exception(f"Error processing company {canonical_key}")

                if dry_run:
                    logger.info(
                        "[DRY RUN] Would mark %s signals for %s rejected after error: %s",
                        len(company_signals),
                        canonical_key,
                        e,
                    )
                else:
                    # Mark signals as rejected
                    for sig in company_signals:
                        await self._store.mark_rejected(sig.id, str(e))

        # Calculate average enrichment boost
        if stats["enrichment_boosts_applied"] > 0:
            stats["avg_enrichment_boost"] = stats["total_enrichment_boost"] / stats["enrichment_boosts_applied"]
        else:
            stats["avg_enrichment_boost"] = 0.0

        # Phase G Sprint 2: Extract claim facts (after consolidation)
        # NOTE: Extraction runs even in dry_run to validate the extractor.
        # Persistence is gated so dry_run never writes to the database.
        if self.config.use_claim_facts and self._claim_fact_store and self._claim_extractor:
            claim_stats = await self._extract_and_persist_claim_facts(
                consolidated_map,
                entity_id_map,
                dry_run=dry_run,
            )
            stats["claim_facts_extracted"] = claim_stats.get("facts_extracted", 0)
            stats["claim_facts_saved"] = claim_stats.get("facts_saved", 0)
            if dry_run:
                logger.info(
                    "Claim facts dry-run: %s extracted, 0 persisted",
                    stats["claim_facts_extracted"],
                )
            else:
                logger.info("Persisted %s claim facts", stats["claim_facts_saved"])

        logger.info(f"Processing stage complete: {stats}")

        return stats

    async def _drain_notion_outbox(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Drain queued Notion writes from the outbox."""
        if not self._notion_outbox_worker:
            logger.info("Notion outbox worker not available, skipping drain")
            return {
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "created": 0,
                "updated": 0,
                "skipped": 0,
            }

        try:
            stats = await self._notion_outbox_worker.drain(
                limit=limit or self.config.batch_size
            )
            logger.info(f"Notion outbox drain complete: {stats}")
            return stats
        except Exception as e:
            logger.warning(f"Notion outbox drain failed (non-fatal): {e}")
            return {
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "created": 0,
                "updated": 0,
                "skipped": 0,
            }

    async def _process_company(
        self,
        signals: List[StoredSignal],
        dry_run: bool,
        consolidated: Optional[ConsolidatedSignal] = None,
    ) -> Dict[str, Any]:
        """
        Process all signals for a single company.

        Steps:
        1. Convert to Signal objects
        2. Check suppression
        3. Run through verification gate
        4. Queue Notion write if appropriate
        5. Update signal status

        Args:
            signals: List of StoredSignal objects for this company
            dry_run: If True, don't actually push to Notion
            consolidated: Optional consolidated signal with merged field values

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

            if dry_run:
                logger.info(
                    "[DRY RUN] Would reject %s as suppressed by Notion status %s",
                    canonical_key,
                    suppressed.status,
                )
            else:
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

        # Run through EntityResolver (if enabled)
        entity_resolution = None
        if self._entity_resolver and self.config.use_entities:
            try:
                # Convert first signal to asset for resolution
                primary_asset = self._signal_to_asset(signals[0])
                best_candidate = await self._entity_resolver.get_best_candidate(
                    primary_asset, min_confidence=0.5
                )

                if best_candidate:
                    entity_resolution = {
                        "resolved_key": best_candidate.lead_canonical_key,
                        "method": best_candidate.method.value,
                        "confidence": best_candidate.confidence,
                        "reason": best_candidate.reason,
                    }
                    logger.info(
                        f"Entity resolution for {canonical_key}: "
                        f"resolved to {best_candidate.lead_canonical_key} "
                        f"(method: {best_candidate.method.value}, confidence: {best_candidate.confidence:.2f})"
                    )

                    # Create asset-to-lead link in EntityResolutionStore
                    if self._entity_resolution_store and not dry_run:
                        try:
                            link = AssetToLead(
                                asset_id=primary_asset.id or 0,
                                asset_source_type=primary_asset.source_type,
                                asset_external_id=primary_asset.external_id,
                                lead_canonical_key=best_candidate.lead_canonical_key,
                                confidence=best_candidate.confidence,
                                resolved_by=best_candidate.method,
                                metadata={"original_key": canonical_key, "reason": best_candidate.reason},
                            )
                            await self._entity_resolution_store.create_link(link)
                            logger.debug(f"Created asset-to-lead link: {primary_asset.external_id} → {best_candidate.lead_canonical_key}")
                        except Exception as e:
                            logger.warning(f"Failed to create asset-to-lead link (non-fatal): {e}")
                    elif self._entity_resolution_store and dry_run:
                        logger.info(
                            "[DRY RUN] Would create asset-to-lead link: %s -> %s",
                            primary_asset.external_id,
                            best_candidate.lead_canonical_key,
                        )

                    # If resolved key differs and has higher confidence, log it
                    if best_candidate.lead_canonical_key != canonical_key:
                        logger.info(
                            f"Entity resolution suggests alternative key: "
                            f"{best_candidate.lead_canonical_key} (original: {canonical_key})"
                        )
                else:
                    logger.debug(f"Entity resolution: no high-confidence candidate for {canonical_key}")

            except Exception as e:
                logger.warning(f"Entity resolution failed (non-fatal): {e}")
                entity_resolution = {"error": str(e)}

        # Get founder score (Harmonic enhancement)
        founder_score = 0.0
        if self._founder_store and self.config.use_founder_scoring:
            try:
                founder_score = await self._founder_store.get_aggregate_founder_score(canonical_key)
                if founder_score > 0:
                    logger.info(f"Founder score for {canonical_key}: {founder_score:.2f}")
            except Exception as e:
                logger.warning(f"Founder scoring failed (non-fatal): {e}")

        # Get velocity metrics (Harmonic enhancement)
        velocity_boost = 0.0
        momentum_score = 0.0
        if self._velocity_tracker and self.config.use_velocity_tracking:
            try:
                velocity = await self._velocity_tracker.get_velocity(canonical_key)
                velocity_boost = velocity.confidence_boost
                momentum_score = velocity.momentum_score
                if velocity_boost > 0:
                    logger.info(
                        f"Velocity for {canonical_key}: boost={velocity_boost:.2f}, "
                        f"momentum={momentum_score:.2f}, "
                        f"signals_48h={velocity.signals_48h}, "
                        f"types={len(velocity.unique_signal_types)}"
                    )
            except Exception as e:
                logger.warning(f"Velocity tracking failed (non-fatal): {e}")

        # Get enrichment boost (Phase 2 enhancement)
        enrichment_boost = 0.0
        if self._enrichment_calculator and consolidated:
            try:
                enrichment = self._enrichment_calculator.calculate(consolidated)
                enrichment_boost = enrichment.total_boost
                if enrichment_boost > 0:
                    logger.info(
                        f"Enrichment boost for {canonical_key}: {enrichment_boost:.3f} "
                        f"(age: {enrichment.company_age_days}d, social: {enrichment.social_proof_score})"
                    )
            except Exception as e:
                logger.warning(f"Enrichment calculation failed (non-fatal): {e}")

        # Phase C: Boilerplate detection in SHADOW mode
        # Run BEFORE thesis filtering so it logs even for rejected/held signals
        boilerplate_match = None
        if self._feature_registry.is_enabled("boilerplate_defense"):
            try:
                # Extract tokens from all signals' raw_data
                combined_raw = {}
                for sig in signals:
                    if sig.raw_data:
                        # Merge raw_data from all signals
                        for k, v in sig.raw_data.items():
                            if k not in combined_raw:
                                combined_raw[k] = v

                # Run boilerplate detection
                boilerplate_match = self._boilerplate_detector.detect_from_raw_data(combined_raw)

                # SHADOW log the result (don't affect routing)
                if self._store and not dry_run:
                    shadow_data = self._boilerplate_detector.get_shadow_log_data(
                        self._boilerplate_detector.extract_tokens(combined_raw),
                        boilerplate_match,
                    )
                    await self._store.log_shadow_computation(
                        feature_name="boilerplate_defense",
                        canonical_key=canonical_key,
                        computed_value=shadow_data,
                        signal_id=signals[0].id if signals else None,
                    )
                    self._run_stats.shadow_logs_written += 1

                    if boilerplate_match and boilerplate_match.is_boilerplate:
                        logger.info(
                            f"Boilerplate detected (SHADOW): {canonical_key} "
                            f"matches {boilerplate_match.signature_name} "
                            f"({boilerplate_match.similarity:.0%})"
                        )
                elif self._store and dry_run:
                    logger.info(
                        "[DRY RUN] Would log boilerplate shadow computation for %s",
                        canonical_key,
                    )

            except Exception as e:
                logger.debug(f"Boilerplate detection failed (non-fatal): {e}")

        # Thesis filtering (before verification gate)
        thesis_result = None
        thesis_routing = None
        if self._thesis_filter and consolidated:
            try:
                # Join descriptions list into single string for thesis matching
                description = " ".join(consolidated.descriptions) if consolidated.descriptions else ""

                # Check LLM mode (off/shadow/active)
                llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
                skip_llm = (llm_mode == "off")  # Shadow and active both call LLM
                is_shadow = (llm_mode == "shadow")

                thesis_result = await self._thesis_filter.classify(
                    description,
                    company_name=consolidated.company_name,
                    skip_llm=skip_llm,
                )
                thesis_routing = thesis_result.routing

                # Shadow logging — always before routing (Bug 0.10 fix)
                # Written when thesis_match feature is enabled. Routing is also
                # reconstructable from classification row data when shadow log
                # is unavailable.
                if self._feature_registry.is_enabled("thesis_match") and not dry_run:
                    try:
                        shadow_data = {
                            "keyword_score": thesis_result.keyword_score,
                            "keyword_category": thesis_result.keyword_category,
                            "keyword_matches": thesis_result.keyword_matches,
                            "negative_keywords": thesis_result.negative_keywords,
                            "intent_phrases_matched": thesis_result.intent_phrases_matched,
                            "domain_match": thesis_result.domain_match,
                            "domain_blacklisted": thesis_result.domain_blacklisted,
                            "routing": thesis_result.routing.value,
                            "confidence_adjustment": thesis_result.confidence_adjustment,
                            "v2_shadow": thesis_result.v2_shadow,
                        }
                        await self._store.log_shadow_computation(
                            feature_name="thesis_match",
                            canonical_key=canonical_key,
                            computed_value=shadow_data,
                            signal_id=signals[0].id if signals else None,
                        )
                        self._run_stats.shadow_logs_written += 1
                    except Exception as e:
                        logger.debug(f"Failed to log thesis_match shadow (non-fatal): {e}")
                elif self._feature_registry.is_enabled("thesis_match") and dry_run:
                    logger.info(
                        "[DRY RUN] Would log thesis_match shadow computation for %s",
                        canonical_key,
                    )

                # Route based on thesis result (with per-path classification persistence)
                _classification_persisted = False
                if thesis_result.routing == RoutingDecision.REJECTED:
                    # Persist classification for rejected signal (no competitor data)
                    if self._store and signals and not dry_run:
                        try:
                            await self._store.save_thesis_classification(
                                signal_id=signals[0].id,
                                canonical_key=canonical_key,
                                keyword_score=thesis_result.keyword_score,
                                keyword_category=thesis_result.keyword_category,
                                negative_keywords=thesis_result.negative_keywords,
                                thesis_fit_score=thesis_result.llm_score,
                                category=thesis_result.llm_category,
                                primary_end_user=getattr(
                                    thesis_result, "llm_primary_end_user", None
                                ),
                                paying_customer=getattr(
                                    thesis_result, "llm_paying_customer", None
                                ),
                                sells_to_or_operates_in=getattr(
                                    thesis_result, "llm_sells_to_or_operates_in", None
                                ),
                                prompt_version=getattr(
                                    thesis_result, "llm_prompt_version", None
                                ),
                                model=getattr(thesis_result, "llm_model", None),
                                rationale=thesis_result.llm_rationale,
                                classification_status=(
                                    thesis_result.llm_classification_status or "success"
                                ),
                                competitor_flag=False,
                                competitor_match=None,
                            )
                            _classification_persisted = True
                        except Exception as e:
                            logger.warning(f"Failed to save thesis classification (non-fatal): {e}")
                    if not is_shadow:
                        logger.info(f"Thesis REJECTED: {canonical_key}")
                        if dry_run:
                            logger.info(
                                "[DRY RUN] Would mark %s rejected by thesis filter",
                                canonical_key,
                            )
                        else:
                            for sig in signals:
                                await self._store.mark_rejected(
                                    sig.id,
                                    f"Thesis rejected: negative keywords {thesis_result.negative_keywords}",
                                )
                            await self._store.update_signal_status(
                                canonical_key,
                                "rejected",
                                error_message=f"Thesis rejected: {thesis_result.negative_keywords}",
                            )
                        return {
                            "decision": PushDecision.REJECT,
                            "reason": f"Thesis rejected: {thesis_result.negative_keywords}",
                            "thesis_routing": thesis_routing,
                            "gating_applied": False,
                            "enrichment_boost": enrichment_boost,
                        }
                    else:
                        logger.info(f"Thesis REJECTED (shadow, observed-only): {canonical_key}")

                elif thesis_result.routing == RoutingDecision.HELD:
                    # Persist classification for held signal (no competitor data)
                    if self._store and signals and not dry_run:
                        try:
                            await self._store.save_thesis_classification(
                                signal_id=signals[0].id,
                                canonical_key=canonical_key,
                                keyword_score=thesis_result.keyword_score,
                                keyword_category=thesis_result.keyword_category,
                                negative_keywords=thesis_result.negative_keywords,
                                thesis_fit_score=thesis_result.llm_score,
                                category=thesis_result.llm_category,
                                primary_end_user=getattr(
                                    thesis_result, "llm_primary_end_user", None
                                ),
                                paying_customer=getattr(
                                    thesis_result, "llm_paying_customer", None
                                ),
                                sells_to_or_operates_in=getattr(
                                    thesis_result, "llm_sells_to_or_operates_in", None
                                ),
                                prompt_version=getattr(
                                    thesis_result, "llm_prompt_version", None
                                ),
                                model=getattr(thesis_result, "llm_model", None),
                                rationale=thesis_result.llm_rationale,
                                classification_status=(
                                    thesis_result.llm_classification_status or "success"
                                ),
                                competitor_flag=False,
                                competitor_match=None,
                            )
                            _classification_persisted = True
                        except Exception as e:
                            logger.warning(f"Failed to save thesis classification (non-fatal): {e}")
                    if not is_shadow:
                        logger.info(f"Thesis HELD: {canonical_key}")
                        if dry_run:
                            logger.info(
                                "[DRY RUN] Would mark %s held by thesis filter",
                                canonical_key,
                            )
                        else:
                            await self._store.update_signal_status(
                                canonical_key,
                                "held",
                                error_message=f"Thesis held: score {thesis_result.keyword_score:.2f} below threshold",
                            )
                        return {
                            "decision": PushDecision.HOLD,
                            "reason": f"Thesis held: score {thesis_result.keyword_score:.2f} below threshold",
                            "thesis_routing": thesis_routing,
                            "gating_applied": False,
                            "enrichment_boost": enrichment_boost,
                        }
                    else:
                        logger.info(f"Thesis HELD (shadow, observed-only): {canonical_key}")

                # QUALIFIED (or shadow fall-through):
                # Competitor detection stays in qualified-only flow
                competitor_match = None
                if self._competitor_detector and thesis_result.keyword_category:
                    competitor_match = self._competitor_detector.check(
                        thesis_result.keyword_category,
                        description,
                    )
                    if competitor_match:
                        logger.warning(
                            f"Potential competitor detected: {canonical_key} "
                            f"similar to {competitor_match.portfolio_company}"
                        )

                # Persist classification with competitor data (qualified path only —
                # skip if already persisted for shadow-rejected/held signals)
                if self._store and signals and not _classification_persisted and not dry_run:
                    try:
                        await self._store.save_thesis_classification(
                            signal_id=signals[0].id,
                            canonical_key=canonical_key,
                            keyword_score=thesis_result.keyword_score,
                            keyword_category=thesis_result.keyword_category,
                            negative_keywords=thesis_result.negative_keywords,
                            thesis_fit_score=thesis_result.llm_score,
                            category=thesis_result.llm_category,
                            primary_end_user=getattr(
                                thesis_result, "llm_primary_end_user", None
                            ),
                            paying_customer=getattr(
                                thesis_result, "llm_paying_customer", None
                            ),
                            sells_to_or_operates_in=getattr(
                                thesis_result, "llm_sells_to_or_operates_in", None
                            ),
                            prompt_version=getattr(
                                thesis_result, "llm_prompt_version", None
                            ),
                            model=getattr(thesis_result, "llm_model", None),
                            rationale=thesis_result.llm_rationale,
                            classification_status=(
                                thesis_result.llm_classification_status or "success"
                            ),
                            competitor_flag=competitor_match is not None,
                            competitor_match=competitor_match.to_dict() if competitor_match else None,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to save thesis classification (non-fatal): {e}")

                # Apply confidence adjustment to enrichment boost
                enrichment_boost += thesis_result.confidence_adjustment

            except Exception as e:
                logger.warning(f"Thesis filtering failed (non-fatal): {e}")

        # Functional schema extraction (Phase 2, optional)
        schema_extracted = False
        if self._schema_extractor:
            try:
                company_id = signals[0].company_id
                if company_id and not await self._store.has_active_schema(company_id):
                    # Select best signal (highest confidence, prefer sec_edgar > job_postings > news_api)
                    _source_prio = {"sec_edgar": 0, "job_postings": 1, "news_api": 2}
                    best = sorted(
                        signals,
                        key=lambda s: (-s.confidence, _source_prio.get(s.source_api, 99)),
                    )[0]
                    signal_data = {
                        "title": best.company_name or canonical_key,
                        "source_context": (
                            " ".join(consolidated.descriptions)
                            if consolidated and consolidated.descriptions
                            else ""
                        ),
                        "source_api": best.source_api or "unknown",
                    }
                    schema = await self._schema_extractor.extract(
                        signal_data,
                        company_id=company_id,
                        evidence_signal_ids=[s.id for s in signals],
                    )
                    if schema:
                        if dry_run:
                            logger.info(
                                "[DRY RUN] Would persist functional schema for %s",
                                canonical_key,
                            )
                        else:
                            await self._store.save_functional_schema(schema.to_storage_dict())
                        schema_extracted = True
                        logger.info(
                            f"Schema extracted for {canonical_key}: {schema.customer_archetype}"
                        )
            except Exception as e:
                logger.warning(f"Schema extraction failed for {canonical_key} (non-fatal): {e}")

        # Run through SignalProcessor gating (if enabled)
        gating_applied = False
        gating_triggered = False
        gating_actionable = False
        gating_results = []
        gating_error = None

        if self._signal_processor and self.config.use_gating:
            gating_applied = True
            logger.info(f"Running gating for {canonical_key} (SignalProcessor enabled)")

            try:
                # Process each signal through SignalProcessor
                for signal in signals:
                    # Convert StoredSignal to dict format expected by SignalProcessor
                    signal_dict = {
                        "id": str(signal.id),
                        "signal_type": signal.signal_type,
                        "source_api": signal.source_api,
                        "canonical_key": signal.canonical_key,
                        "company_name": signal.company_name,
                        "confidence": signal.confidence,
                        "raw_data": signal.raw_data,
                        "detected_at": signal.detected_at,
                    }

                    # Run through two-stage gating
                    processing_result = await self._signal_processor.process_signal(signal_dict)
                    gating_results.append(processing_result)

                    # Track if any signal was triggered
                    if processing_result.triggered:
                        gating_triggered = True

                        # Check if actionable (pivot or expansion)
                        if processing_result.is_actionable:
                            gating_actionable = True
                            logger.info(
                                f"Signal {signal.id} is actionable: "
                                f"{processing_result.classification.label.value} "
                                f"(confidence: {processing_result.classification.confidence:.2f})"
                            )

                # Log summary
                triggered_count = sum(1 for r in gating_results if r.triggered)
                actionable_count = sum(1 for r in gating_results if r.is_actionable)

                logger.info(
                    f"Gating complete for {canonical_key}: "
                    f"{triggered_count}/{len(gating_results)} triggered, "
                    f"{actionable_count} actionable"
                )

            except Exception as e:
                logger.warning(f"SignalProcessor gating failed (non-fatal): {e}")
                gating_error = str(e)
                # Continue with normal flow - gating is optional

        # Per-source confidence floor: hold single-source groups from high-FP sources
        # that don't reach the source-specific minimum confidence.
        # Only fires when ALL signals in the group share the same high-FP source_api.
        if all(sig.source_api in _SOURCE_MIN_CONFIDENCE for sig in signals):
            dominant_source = signals[0].source_api
            min_conf = _SOURCE_MIN_CONFIDENCE[dominant_source]
            max_conf = max(s.confidence for s in signals)
            if max_conf < min_conf:
                logger.info(
                    "source_floor_hold canonical_key=%s source=%s max_confidence=%.2f floor=%.2f",
                    canonical_key, dominant_source, max_conf, min_conf,
                )
                if not dry_run:
                    await self._store.update_signal_status(
                        canonical_key,
                        "held",
                        error_message=(
                            f"Source floor hold: {dominant_source} "
                            f"max_confidence={max_conf:.2f} < floor={min_conf:.2f}"
                        ),
                    )
                return {
                    "decision": PushDecision.HOLD,
                    "reason": (
                        f"Source floor hold: {dominant_source} "
                        f"max_confidence={max_conf:.2f} < floor={min_conf:.2f}"
                    ),
                    "gating_applied": False,
                    "enrichment_boost": enrichment_boost,
                }

        # Convert to Signal objects for verification gate
        gate_signals = [self._stored_to_signal(sig) for sig in signals]

        # Run through verification gate (with Harmonic enhancements)
        verification = self._gate.evaluate(
            gate_signals,
            founder_score=founder_score,
            velocity_boost=velocity_boost,
            momentum_score=momentum_score,
            enrichment_boost=enrichment_boost,
            keyword_score=thesis_result.keyword_score if thesis_result else None,
            llm_score=thesis_result.llm_score if thesis_result else None,
        )

        logger.info(
            f"Verification for {canonical_key}: "
            f"{verification.decision.value} (confidence: {verification.confidence_score:.2f})"
        )

        # Persist confidence ledger (non-fatal, like exit_prediction below)
        if dry_run:
            logger.info(
                "[DRY RUN] Would persist confidence ledger for %s decision=%s",
                canonical_key,
                verification.decision.value,
            )
        else:
            try:
                ledger_id = await self._store.save_confidence_ledger(
                    canonical_key=canonical_key,
                    verification_result=verification,
                    signal_ids=[s.id for s in signals],
                    execution_id=self._execution_id or None,
                    company_id=signals[0].company_id if signals else None,
                    is_dry_run=dry_run,
                    evaluation_origin="pipeline",
                    policy_version=self._gate.POLICY_VERSION,
                    routing_config={
                        "high_threshold": self._gate.HIGH_CONFIDENCE_THRESHOLD,
                        "medium_threshold": self._gate.MEDIUM_CONFIDENCE_THRESHOLD,
                        "score_scale": self._gate.score_recalibration_factor,
                        "strict_mode": self._gate.strict_mode,
                    },
                )
                logger.info(
                    "confidence_ledger_saved canonical_key=%s decision=%s gate_score=%.3f signals=%d ledger_id=%d",
                    canonical_key, verification.decision.value,
                    verification.confidence_breakdown.get("overall", 0.0),
                    len(signals), ledger_id,
                )
            except Exception as e:
                logger.warning(
                    "confidence_ledger_save_failed canonical_key=%s error=%s",
                    canonical_key, e,
                )

        # Compute exit prediction (if enabled and passes gate)
        exit_prediction = None
        if (
            self._exit_predictor
            and verification.decision in (PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW)
            and consolidated
        ):
            try:
                exit_prediction = await self._exit_predictor.predict(
                    consolidated=consolidated,
                    thesis_classification=thesis_result,
                )
                if dry_run:
                    logger.info(
                        "[DRY RUN] Would persist exit prediction for %s",
                        canonical_key,
                    )
                else:
                    await self._store.store_exit_prediction(exit_prediction)
                logger.info(
                    f"Exit prediction for {canonical_key}: "
                    f"quality={exit_prediction.deal_quality_score:.2f}, "
                    f"rec={exit_prediction.recommendation}"
                )
            except Exception as e:
                logger.warning(f"Exit prediction failed for {canonical_key} (non-fatal): {e}")

        # Compute investor matches (if enabled and passes gate)
        investor_match_result: Optional[InvestorMatchResult] = None
        if (
            self._investor_matcher
            and verification.decision in (PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW)
        ):
            try:
                # Build company claims from consolidated signal
                company_claims = {}
                if consolidated:
                    if consolidated.company_name:
                        company_claims["company_name"] = consolidated.company_name
                    if consolidated.description:
                        company_claims["description"] = consolidated.description
                    if thesis_result:
                        company_claims["sector"] = thesis_result.category
                        company_claims["thesis_fit"] = thesis_result.thesis_fit

                investor_match_result = await self._investor_matcher.match(
                    company_key=canonical_key,
                    company_claims=company_claims if company_claims else None,
                    save_results=not dry_run,
                )
                if investor_match_result.matches:
                    top_match = investor_match_result.matches[0]
                    logger.info(
                        f"Investor matching for {canonical_key}: "
                        f"top={top_match.investor_name} (score={top_match.match_score:.2f}), "
                        f"total={len(investor_match_result.matches)} matches"
                    )
                else:
                    logger.debug(f"No investor matches found for {canonical_key}")
            except Exception as e:
                logger.warning(f"Investor matching failed for {canonical_key} (non-fatal): {e}")

        # Phase A: Warm intro enrichment (if enabled + investor matches exist)
        warm_intro_indicators: List[WarmIntroIndicator] = []
        if (
            self.config.use_warm_intro_enrichment
            and not dry_run
            and investor_match_result
            and investor_match_result.matches
        ):
            try:
                from utils.warm_intro_enricher import WarmIntroEnricher
                from utils.warm_intro_boost import WarmIntroBoost, RelationshipSource
                from storage.relationship_store import RelationshipStore

                enricher = WarmIntroEnricher(
                    relationship_store=RelationshipStore(
                        db_path=self._relationship_store_db_path()
                    ),
                    warm_intro_boost=WarmIntroBoost(),
                )

                for match in investor_match_result.matches[:5]:
                    investor_domain = match.investor_name  # Best available domain proxy
                    candidate = await enricher.enrich_investor(
                        investor_domain=investor_domain,
                        user_email=self.config.user_email or "",
                    )
                    if candidate:
                        warm_intro_indicators.append(WarmIntroIndicator(
                            investor_domain=candidate.investor_domain,
                            score_bucket=candidate.confidence,  # high|medium|low
                            badge=candidate.badge,
                            source_kind=candidate.source.value,  # gmail|notion_lp
                        ))

                if warm_intro_indicators:
                    logger.info(
                        "Warm intro enrichment for %s: %d indicators",
                        canonical_key, len(warm_intro_indicators),
                    )
            except Exception as e:
                logger.warning(
                    "Warm intro enrichment failed for %s (non-fatal): %s",
                    canonical_key, e,
                )

        # Decide on Notion push
        notion_status = None

        if verification.decision in (PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW):
            if self._notion and not dry_run:
                # Queue for Notion
                notion_result = await self._push_to_notion(
                    signals, verification, consolidated=consolidated,
                    investor_match_result=investor_match_result,
                    warm_intro_indicators=warm_intro_indicators,
                )
                notion_status = notion_result["status"]

                # Mark signals as queued
                for sig in signals:
                    await self._store.mark_queued(
                        sig.id,
                        metadata={
                            "decision": verification.decision.value,
                            "confidence": verification.confidence_score,
                            "status": verification.suggested_status,
                            "verification_status": verification.verification_status.value,
                            "outbox_id": notion_result["outbox_id"],
                            "idempotency_key": notion_result["idempotency_key"],
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

                        await self._notifier.notify_high_confidence_signal(
                            company_name=company_name,
                            confidence=verification.confidence_score,
                            signal_types=signal_types,
                            sources_count=sources_count,
                            canonical_key=canonical_key,
                            why_now=why_now,
                        )
                    except Exception as e:
                        logger.warning(f"Slack notification failed (non-fatal): {e}")
            else:
                # Dry run or no Notion connector
                if dry_run:
                    # Dry run — log only, NO state mutation
                    # Signals stay pending (dry_run is one-off, not repeated)
                    logger.info(
                        f"[DRY RUN] Would push {canonical_key} to Notion "
                        f"with status: {verification.suggested_status}"
                    )
                    notion_status = "dry_run"
                else:
                    # No Notion connector — mark held to prevent infinite
                    # reprocessing while preserving recoverability
                    logger.warning(
                        f"No Notion connector — holding {canonical_key}. "
                        f"Will be reprocessed when connector is configured."
                    )
                    for sig in signals:
                        await self._store.mark_held(
                            sig.id,
                            reason="no_connector: Notion connector not configured",
                            metadata={
                                "decision": verification.decision.value,
                                "confidence": verification.confidence_score,
                                "status": verification.suggested_status,
                                "no_connector": True,
                            },
                        )
                    notion_status = "no_connector"

        elif verification.decision == PushDecision.HOLD:
            # Keep as pending - don't mark as pushed or rejected
            logger.info(f"Holding {canonical_key} for more signals")

        elif verification.decision == PushDecision.REJECT:
            if dry_run:
                logger.info(
                    "[DRY RUN] Would mark %s rejected by verification gate",
                    canonical_key,
                )
            else:
                # Mark as rejected
                for sig in signals:
                    await self._store.mark_rejected(sig.id, verification.reason)

        return {
            "decision": verification.decision,
            "reason": verification.reason,
            "confidence": verification.confidence_score,
            "notion_status": notion_status,
            "gating_applied": gating_applied,
            "gating_triggered": gating_triggered,
            "gating_actionable": gating_actionable,
            "gating_error": gating_error,
            "entity_resolution": entity_resolution,
            # Harmonic enhancements
            "founder_score": founder_score,
            "velocity_boost": velocity_boost,
            "momentum_score": momentum_score,
            "enrichment_boost": enrichment_boost,
            # Thesis filtering
            "thesis_routing": thesis_routing,
            # Functional schema
            "schema_extracted": schema_extracted,
        }

    async def _push_to_notion(
        self,
        signals: List[StoredSignal],
        verification: VerificationResult,
        consolidated: Optional[ConsolidatedSignal] = None,
        investor_match_result: Optional[InvestorMatchResult] = None,
        warm_intro_indicators: Optional[List[WarmIntroIndicator]] = None,
    ) -> Dict[str, Any]:
        """
        Queue a company for Notion push via the outbox.

        Args:
            signals: List of StoredSignal objects
            verification: VerificationResult from the gate
            consolidated: Optional consolidated signal with merged field values
            investor_match_result: Optional investor matching results
            warm_intro_indicators: Optional warm intro indicators (Phase A)

        Returns dict with status and outbox metadata.
        """
        if not self._notion:
            raise RuntimeError("Notion connector not initialized")
        if not self._store:
            raise RuntimeError("SignalStore not initialized")

        # Build prospect payload from signals
        primary_signal = signals[0]

        # Extract company info - prefer consolidated data if available
        if consolidated:
            company_name = consolidated.company_name
            why_now = "; ".join(consolidated.why_now_parts[:3])  # Limit to 3 reasons
        else:
            company_name = primary_signal.company_name or "Unknown Company"
            why_now = self._build_why_now(signals)
        sector_candidate = self._extract_sector_candidate(signals)
        watchlists_matched = await self._match_watchlists(
            signals,
            verification.confidence_score,
            company_name,
            why_now,
        )

        # Determine stage from signals
        stage = self._infer_stage(signals)

        # Build investor matches summary for payload (top 5)
        investor_matches_summary = []
        if investor_match_result and investor_match_result.matches:
            for match in investor_match_result.matches[:5]:
                investor_matches_summary.append({
                    "investor_id": match.investor_id,
                    "investor_name": match.investor_name,
                    "investor_type": match.investor_type,
                    "match_score": round(match.match_score, 3),
                    "rank": match.rank,
                    "explanations": [
                        {"reason": exp.reason, "lift_score": round(exp.lift_score, 2)}
                        for exp in (match.explanations or [])[:2]  # Top 2 reasons
                    ],
                })

        # Build payload
        payload = ProspectPayload(
            discovery_id=f"discovery_{primary_signal.id}",
            company_name=company_name,
            canonical_key=primary_signal.canonical_key,
            stage=stage,
            status=verification.suggested_status,
            confidence_score=verification.confidence_score,
            signal_types=[sig.signal_type for sig in signals],
            why_now=why_now,
            canonical_key_candidates=[primary_signal.canonical_key],
            proposed_sector=sector_candidate,
            watchlists_matched=watchlists_matched,
            # Enrichment fields from consolidated signal
            founding_date=consolidated.founding_date if consolidated else None,
            social_proof_score=sum(consolidated.social_proof.values()) if consolidated and consolidated.social_proof else 0,
            # Investor matching (Sprint 5)
            investor_matches=investor_matches_summary,
            # Warm intro indicators (Phase A)
            warm_intro_indicators=warm_intro_indicators or [],
        )

        outbox_payload = {
            "prospect": self._serialize_prospect_payload(payload),
            "signal_ids": [s.id for s in signals],
            "metadata": {
                "confidence": verification.confidence_score,
                "status": verification.suggested_status,
                "decision": verification.decision.value,
                "verification_status": verification.verification_status.value,
            },
        }

        idempotency_key = payload.idempotency_key()
        outbox_id = await self._store.enqueue_notion_write(
            idempotency_key=idempotency_key,
            payload=outbox_payload,
        )

        logger.info(
            f"Queued {company_name} for Notion push "
            f"(outbox_id: {outbox_id})"
        )

        return {
            "status": "queued",
            "outbox_id": outbox_id,
            "idempotency_key": idempotency_key,
        }

    def _serialize_prospect_payload(self, payload: ProspectPayload) -> Dict[str, Any]:
        """Serialize ProspectPayload for storage in the outbox."""
        return payload.model_dump(mode="json")

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _match_watchlists(
        self,
        signals: List[StoredSignal],
        confidence_score: float,
        company_name: str,
        why_now: str,
    ) -> List[str]:
        """Match watchlists based on keywords and confidence score."""
        if not self._watchlist_loader:
            return []

        watchlists = await self._watchlist_loader.get_watchlists()
        if not watchlists:
            return []

        text = self._build_watchlist_text(signals, company_name, why_now)
        matched = []
        for watchlist in watchlists:
            if watchlist.matches(text, confidence_score):
                matched.append(watchlist.name)
        return matched

    def _build_watchlist_text(
        self,
        signals: List[StoredSignal],
        company_name: str,
        why_now: str,
    ) -> str:
        parts: List[str] = [company_name, why_now]
        for signal in signals:
            if signal.company_name:
                parts.append(signal.company_name)
            parts.append(signal.signal_type)
            raw_data = signal.raw_data or {}
            for key in ("description", "summary", "category", "sector", "industry", "title"):
                value = raw_data.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
        return " ".join(parts).lower()

    def _extract_sector_candidate(self, signals: List[StoredSignal]) -> Optional[str]:
        """Extract a sector/category hint from signal payloads."""
        for signal in signals:
            raw_data = signal.raw_data or {}
            for key in ("sector", "category", "industry", "vertical"):
                value = raw_data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

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

    def _signal_to_asset(self, stored: StoredSignal) -> SourceAsset:
        """Convert StoredSignal to SourceAsset for entity resolution"""
        return SourceAsset(
            source_type=stored.source_api,
            external_id=stored.canonical_key,
            raw_payload=stored.raw_data,
            fetched_at=stored.detected_at,
        )

    async def _regroup_signals_by_entity(
        self, signals_by_key: Dict[str, List[StoredSignal]]
    ) -> Dict[str, List[StoredSignal]]:
        """
        Re-group signals using EntityResolutionStore links.

        Multi-asset companies (e.g., GitHub repo + Product Hunt + domain) should
        consolidate to a single lead. This method checks if assets have been
        resolved to the same canonical key and re-groups them accordingly.

        Args:
            signals_by_key: Dictionary mapping canonical_key → list of signals

        Returns:
            Re-grouped dictionary with resolved canonical keys
        """
        if not self._entity_resolution_store:
            return signals_by_key

        regrouped: Dict[str, List[StoredSignal]] = {}

        for canonical_key, signals in signals_by_key.items():
            if not signals:
                continue

            asset = self._signal_to_asset(signals[0])

            # Check if asset has existing link in EntityResolutionStore
            resolved_key = await self._entity_resolution_store.get_lead_for_asset(
                asset.source_type, asset.external_id, min_confidence=0.5
            )

            # Use resolved key if available, otherwise use original key
            final_key = resolved_key or canonical_key

            if final_key not in regrouped:
                regrouped[final_key] = []
            regrouped[final_key].extend(signals)

            if resolved_key and resolved_key != canonical_key:
                logger.info(
                    f"Signal regrouping: {canonical_key} → {resolved_key} "
                    f"(consolidated {len(signals)} signals)"
                )

        return regrouped

    async def _run_shadow_entity_comparison(
        self,
        pending_signals: List[StoredSignal],
    ) -> Dict[str, Any]:
        """Run shadow entity comparison (read-only, fail-open).

        Wraps in circuit breaker. On failure or circuit-open, records
        a visibility artifact and returns error stats.
        """
        from utils.circuit_breaker import CircuitBreaker, CircuitOpenError
        from storage.readonly_identity_store import ReadOnlyIdentityStore
        from intelligence.shadow_entity_evaluator import (
            ShadowRunConfig, run_shadow_comparison, store_shadow_run,
            store_skipped_shadow_run,
        )

        # Lazily initialize circuit breaker (module-level would also work)
        if not hasattr(self, "_shadow_circuit_breaker"):
            self._shadow_circuit_breaker = CircuitBreaker(
                "shadow_entity", failure_threshold=3, recovery_timeout=300
            )

        cb = self._shadow_circuit_breaker

        # Check circuit state
        if cb.state == "open":
            logger.info("Shadow entity circuit breaker OPEN — recording skipped run")
            await store_skipped_shadow_run(self._store, reason="circuit_breaker_open")
            return {"status": "skipped", "reason": "circuit_breaker_open"}

        # Build ReadOnlyIdentityStore if needed
        if not hasattr(self, "_ro_identity_store") or self._ro_identity_store is None:
            if self._identity_store:
                self._ro_identity_store = ReadOnlyIdentityStore(
                    self._identity_store,
                    db_path=self.config.db_path,
                )
                await self._ro_identity_store.initialize()
            else:
                return {"status": "skipped", "reason": "no_identity_store"}

        config = ShadowRunConfig.from_env()

        try:
            async def _do_shadow():
                return await run_shadow_comparison(
                    self._store, self._ro_identity_store, config
                )

            result = await cb.call(_do_shadow)
            shadow_run_id = await store_shadow_run(self._store, result)

            # Auto-generate merge suggestions if over_merge disagreements exist
            over_merges = [
                d for d in result.disagreements
                if d.disagreement_type == "over_merge"
            ]
            if over_merges:
                try:
                    from intelligence.merge_suggestions import generate_merge_suggestions
                    await generate_merge_suggestions(
                        self._store,
                        self._ro_identity_store,
                        shadow_run_id=shadow_run_id,
                        config=config,
                    )
                except Exception as e:
                    logger.warning("Merge suggestion generation failed (non-fatal): %s", e)

            return {
                "status": result.status,
                "total_signals": result.total_signals,
                "agreements": result.agreements,
                "disagreements": result.disagreements_count,
                "agreement_rate": result.agreement_rate,
                "shadow_run_id": shadow_run_id,
            }

        except CircuitOpenError:
            logger.info("Shadow entity circuit breaker tripped — recording skipped run")
            await store_skipped_shadow_run(self._store, reason="circuit_breaker_open")
            return {"status": "skipped", "reason": "circuit_breaker_open"}
        except Exception as e:
            logger.warning("Shadow entity comparison failed: %s", e, exc_info=True)
            return {"status": "failed", "error": str(e)}

    async def _apply_phase_g_identity_resolution(
        self,
        pending_signals: List[StoredSignal],
        by_key: Dict[str, List[StoredSignal]],
        *,
        dry_run: bool = False,
    ) -> Tuple[Dict[str, List[StoredSignal]], Dict[str, str], Dict[str, int]]:
        """
        Apply Phase G Sprint 2 identity resolution with blocking-first fuzzy matching.

        This regroups signals by resolved entity ID, handling fuzzy name matching
        and entity merges.

        Args:
            pending_signals: All pending signals
            by_key: Current grouping by canonical_key

        Returns:
            Tuple of:
                - Regrouped signals by primary_canonical_key
                - Entity ID map (canonical_key -> entity_id)
                - Stats dict with resolution metrics
        """
        if not self._phase_g_resolver or not self._identity_store:
            return by_key, {}, {"entities_resolved": 0, "merges": 0}

        stats = {"entities_resolved": 0, "merges": 0}

        try:
            # Resolve signals into entity groups
            groups = await self._phase_g_resolver.resolve(pending_signals)

            total_merges = 0

            if dry_run:
                logger.info(
                    "[DRY RUN] Would persist Phase G identity bindings for %s groups",
                    len(groups),
                )
            else:
                # Persist identity state in batched transactions
                batch_size = 50

                for i in range(0, len(groups), batch_size):
                    batch = groups[i:i + batch_size]

                    async with self._store.transaction_immediate() as tx:
                        for group in batch:
                            # Upsert strong key bindings
                            bindings = [
                                StrongKeyBinding(
                                    strong_key=b[0],
                                    entity_id=b[1],
                                    source_signal_id=b[2],
                                    source_key=b[3]
                                )
                                for b in group.strong_keys_to_bind
                            ]
                            merges = await self._identity_store.upsert_strong_key_bindings(bindings, tx)
                            total_merges += len(merges)

                            # Upsert alias bindings
                            aliases = [
                                AliasKeyBinding(
                                    alias_key=a[0],
                                    entity_id=a[1],
                                    alias_type=a[2],
                                    confidence=a[3],
                                    source=a[4],
                                    expires_at=a[5]
                                )
                                for a in group.alias_keys_to_bind
                            ]
                            alias_merges = await self._identity_store.upsert_alias_bindings(aliases, tx)
                            total_merges += len(alias_merges)

                            # Upsert blocking tokens
                            tokens = [
                                BlockingToken(
                                    blocking_token=t[0],
                                    token_type=t[1],
                                    entity_id=t[2],
                                    alias_key=t[3]
                                )
                                for t in group.blocking_tokens_to_bind
                            ]
                            await self._identity_store.upsert_blocking_tokens(tokens, tx)

            # Rebuild by_key mapping from resolved groups
            regrouped: Dict[str, List[StoredSignal]] = {}
            entity_id_map: Dict[str, str] = {}

            for group in groups:
                regrouped[group.primary_canonical_key] = group.signals
                entity_id_map[group.primary_canonical_key] = group.entity_id

            stats["entities_resolved"] = len(groups)
            stats["merges"] = total_merges

            merge_stats = self._phase_g_resolver.get_merge_stats()
            logger.debug(f"Phase G resolver stats: {merge_stats}")

            return regrouped, entity_id_map, stats

        except Exception as e:
            logger.exception(f"Phase G identity resolution failed: {e}")
            # Fall back to original grouping
            return by_key, {}, stats

    async def _extract_and_persist_claim_facts(
        self,
        consolidated_map: Dict[str, ConsolidatedSignal],
        entity_id_map: Dict[str, str],
        *,
        dry_run: bool = False,
    ) -> Dict[str, int]:
        """
        Extract claim facts from consolidated signals and (optionally) persist them.

        Uses bi-temporal SCD-2 storage with authority-based supersession.

        Args:
            consolidated_map: Map of canonical_key -> ConsolidatedSignal
            entity_id_map: Map of canonical_key -> entity_id
            dry_run: If True, run extraction but skip all DB writes

        Returns:
            Stats dict with facts_extracted and facts_saved counts
        """
        if not self._claim_fact_store or not self._claim_extractor:
            return {"facts_extracted": 0, "facts_saved": 0}

        stats = {"facts_extracted": 0, "facts_saved": 0}

        try:
            # Extract all claim facts
            all_facts = self._claim_extractor.extract_batch(
                list(consolidated_map.values()),
                entity_id_map
            )

            # Count extracted facts regardless of persistence
            stats["facts_extracted"] = sum(len(facts) for facts in all_facts.values())

            # Dry run: validate extraction, skip persistence
            if dry_run:
                return stats

            # Persist in batched transactions
            batch_size = 50
            fact_items = list(all_facts.items())

            for i in range(0, len(fact_items), batch_size):
                batch = fact_items[i:i + batch_size]

                async with self._store.transaction_immediate() as tx:
                    for entity_id, facts in batch:
                        for fact in facts:
                            result = await self._claim_fact_store.save_fact(fact, tx)
                            if result.action in ("inserted", "superseded"):
                                stats["facts_saved"] += 1

            logger.debug(f"Persisted {stats['facts_saved']} claim facts")
            return stats

        except Exception as e:
            logger.exception(f"Claim facts persistence failed: {e}")
            return stats

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
