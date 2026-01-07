"""
SignalProcessor: Orchestrates two-stage signal gating.

Stage 1: TriggerGate (deterministic, free) - filters 80%+ of signals
Stage 2: LLMClassifierV2 (semantic, ~$0.02/call) - classifies triggered signals

This processor:
1. Receives signals with optional `_previous_snapshot` in raw_data
2. Runs TriggerGate to determine if classification is needed
3. If triggered, runs LLMClassifierV2 to classify the change
4. Returns enriched results for downstream processing
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional

from consumer.trigger_gate import TriggerGate, TriggerResult, ChangeType
from consumer.llm_classifier_v2 import (
    LLMClassifierV2,
    ClassifierConfig,
    ClassificationResult,
    ClassificationLabel,
)

logger = logging.getLogger(__name__)


@dataclass
class ProcessorConfig:
    """Configuration for SignalProcessor."""
    # TriggerGate settings
    description_threshold: float = 0.2
    pivot_keywords: Optional[List[str]] = None

    # LLMClassifierV2 settings
    classifier_model: str = "gemini-2.0-flash"
    min_confidence: float = 0.7
    cache_enabled: bool = True

    # Processing settings
    dry_run: bool = False


@dataclass
class ProcessingResult:
    """Result of processing a single signal."""
    signal_id: str
    triggered: bool
    gating_skipped: bool = False
    skip_reason: Optional[str] = None
    trigger_result: Optional[TriggerResult] = None
    classification: Optional[ClassificationResult] = None
    processed_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_actionable(self) -> bool:
        """True if this signal warrants action (pivot, expansion)."""
        if not self.classification:
            return False
        return self.classification.label in {
            ClassificationLabel.PIVOT,
            ClassificationLabel.EXPANSION,
        }


@dataclass
class ProcessingStats:
    """Aggregated stats from batch processing."""
    total: int = 0
    triggered: int = 0
    not_triggered: int = 0
    skipped: int = 0
    llm_calls: int = 0
    cached_classifications: int = 0
    errors: int = 0

    # Classification breakdown
    pivots: int = 0
    expansions: int = 0
    rebrands: int = 0
    minors: int = 0
    needs_review: int = 0

    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def trigger_rate(self) -> float:
        """Percentage of signals that triggered classification."""
        eligible = self.total - self.skipped
        if eligible == 0:
            return 0.0
        return self.triggered / eligible

    @property
    def cache_hit_rate(self) -> float:
        """Percentage of classifications served from cache."""
        if self.triggered == 0:
            return 0.0
        return self.cached_classifications / self.triggered


class SignalProcessor:
    """
    Orchestrates two-stage signal gating.

    Usage:
        processor = SignalProcessor(ProcessorConfig())

        # Process single signal
        result = await processor.process_signal(signal)

        # Process batch
        stats = await processor.process_batch(signals)
    """

    def __init__(self, config: Optional[ProcessorConfig] = None):
        """
        Initialize SignalProcessor.

        Args:
            config: Processor configuration. Uses defaults if not provided.
        """
        self.config = config or ProcessorConfig()

        # Initialize Stage 1: TriggerGate
        self.trigger_gate = TriggerGate(
            description_threshold=self.config.description_threshold,
            pivot_keywords=self.config.pivot_keywords,
        )

        # Initialize Stage 2: LLMClassifierV2
        classifier_config = ClassifierConfig(
            model=self.config.classifier_model,
            min_confidence=self.config.min_confidence,
            cache_enabled=self.config.cache_enabled,
        )
        self.classifier = LLMClassifierV2(classifier_config)

    async def process_signal(self, signal: Dict[str, Any]) -> ProcessingResult:
        """
        Process a single signal through two-stage gating.

        Args:
            signal: Signal dict with 'id', 'raw_data', etc.
                   raw_data should contain '_previous_snapshot' for comparison.

        Returns:
            ProcessingResult with classification if triggered.
        """
        signal_id = signal.get("id", "unknown")
        raw_data = signal.get("raw_data", {})

        # Check for previous snapshot
        previous_snapshot = raw_data.get("_previous_snapshot")
        if not previous_snapshot:
            logger.debug(f"Signal {signal_id}: No previous snapshot, skipping gating")
            return ProcessingResult(
                signal_id=signal_id,
                triggered=False,
                gating_skipped=True,
                skip_reason="no_previous_snapshot",
            )

        # Build current snapshot (exclude the _previous_snapshot key)
        current_snapshot = {k: v for k, v in raw_data.items() if k != "_previous_snapshot"}

        # Stage 1: TriggerGate
        trigger_result = self.trigger_gate.should_classify(previous_snapshot, current_snapshot)

        if not trigger_result.should_trigger:
            logger.debug(f"Signal {signal_id}: Not triggered by gate")
            return ProcessingResult(
                signal_id=signal_id,
                triggered=False,
                trigger_result=trigger_result,
            )

        logger.info(f"Signal {signal_id}: Triggered - {trigger_result.trigger_reason}")

        # Stage 2: LLMClassifierV2 (only if not dry run)
        if self.config.dry_run:
            logger.info(f"Signal {signal_id}: [DRY RUN] Would classify")
            return ProcessingResult(
                signal_id=signal_id,
                triggered=True,
                trigger_result=trigger_result,
            )

        # Get descriptions for classification
        old_desc = previous_snapshot.get("description", "")
        new_desc = current_snapshot.get("description", "")

        classification = await self.classifier.classify(old_desc, new_desc)

        logger.info(
            f"Signal {signal_id}: Classified as {classification.label.value} "
            f"(confidence: {classification.confidence:.2f})"
        )

        return ProcessingResult(
            signal_id=signal_id,
            triggered=True,
            trigger_result=trigger_result,
            classification=classification,
        )

    async def process_batch(
        self,
        signals: List[Dict[str, Any]],
    ) -> ProcessingStats:
        """
        Process a batch of signals.

        Args:
            signals: List of signal dicts.

        Returns:
            ProcessingStats with aggregated metrics.
        """
        stats = ProcessingStats(total=len(signals))

        for signal in signals:
            try:
                result = await self.process_signal(signal)

                if result.gating_skipped:
                    stats.skipped += 1
                elif result.triggered:
                    stats.triggered += 1
                    stats.llm_calls += 1

                    # Track cache hits
                    if result.classification and result.classification.cached:
                        stats.cached_classifications += 1
                        stats.llm_calls -= 1  # Cached = no actual call

                    # Track classification breakdown
                    if result.classification:
                        label = result.classification.label
                        if label == ClassificationLabel.PIVOT:
                            stats.pivots += 1
                        elif label == ClassificationLabel.EXPANSION:
                            stats.expansions += 1
                        elif label == ClassificationLabel.REBRAND:
                            stats.rebrands += 1
                        elif label == ClassificationLabel.MINOR:
                            stats.minors += 1
                        elif label == ClassificationLabel.NEEDS_REVIEW:
                            stats.needs_review += 1
                else:
                    stats.not_triggered += 1

            except Exception as e:
                logger.error(f"Error processing signal {signal.get('id')}: {e}")
                stats.errors += 1

        stats.completed_at = datetime.utcnow()
        return stats

    async def process_pending_with_gating(
        self,
        store: Any,
        limit: Optional[int] = None,
        signal_type: Optional[str] = None,
    ) -> ProcessingStats:
        """
        Process pending signals from storage with gating.

        This method fetches pending signals from SignalStore, processes
        them through the two-stage gating system (TriggerGate + LLMClassifier),
        and returns aggregated stats.

        Args:
            store: SignalStore instance to fetch pending signals from.
            limit: Maximum number of signals to process (None = all pending).
            signal_type: Filter by signal type (None = all types).

        Returns:
            ProcessingStats with aggregated metrics from processing.
        """
        logger.info(
            f"Processing pending signals with gating "
            f"(limit={limit}, signal_type={signal_type})"
        )

        # Fetch pending signals from store
        pending_signals = await store.get_pending_signals(
            limit=limit,
            signal_type=signal_type,
        )

        if not pending_signals:
            logger.info("No pending signals to process")
            return ProcessingStats()

        logger.info(f"Found {len(pending_signals)} pending signals to process")

        # Convert StoredSignal objects to dict format expected by process_signal
        signal_dicts = [
            {
                "id": str(sig.id),
                "signal_type": sig.signal_type,
                "source_api": sig.source_api,
                "canonical_key": sig.canonical_key,
                "company_name": sig.company_name,
                "confidence": sig.confidence,
                "raw_data": sig.raw_data,
                "detected_at": sig.detected_at,
            }
            for sig in pending_signals
        ]

        # Process through batch gating
        stats = await self.process_batch(signal_dicts)

        logger.info(
            f"Gating complete: {stats.triggered}/{stats.total} triggered, "
            f"{stats.skipped} skipped, {stats.errors} errors"
        )

        return stats

    def save_classifier_cache(self, path: str) -> None:
        """Save classifier cache to file."""
        self.classifier.save_cache(path)

    def load_classifier_cache(self, path: str) -> None:
        """Load classifier cache from file."""
        self.classifier.load_cache(path)
