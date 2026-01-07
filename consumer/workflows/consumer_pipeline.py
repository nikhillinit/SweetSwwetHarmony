"""
Consumer Pipeline Orchestrator

End-to-end pipeline for consumer signal discovery:
1. Collect signals from all sources
2. Filter through two-stage thesis filter
3. Push qualified signals to Notion
4. Poll for user decisions

Usage:
    pipeline = ConsumerPipeline()
    await pipeline.run()

    # Or run individual stages
    await pipeline.collect()
    await pipeline.filter_pending()
    await pipeline.push_qualified()
    await pipeline.poll_decisions()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..storage.consumer_store import ConsumerStore, consumer_store, StoredSignal
from ..thesis_filter.pipeline import ThesisFilterPipeline, FilterResult, FilterResultType
from ..collectors.base import ConsumerCollector, CollectorResult, run_collectors
from ..collectors.hn_collector import HNCollector
from ..collectors.bevnet_collector import BevNetCollector
from ..collectors.reddit_collector import RedditCollector
from ..notion.pusher import NotionPusher, PushResult
from ..notion.poller import NotionPoller

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class PipelineRunResult:
    """Result of a full pipeline run."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Collection stats
    signals_collected: int = 0
    signals_new: int = 0
    collector_results: List[CollectorResult] = field(default_factory=list)

    # Filter stats
    signals_filtered: int = 0
    auto_rejected: int = 0
    llm_rejected: int = 0
    llm_review: int = 0
    llm_auto_approved: int = 0

    # Push stats
    signals_pushed: int = 0
    push_errors: int = 0
    push_skipped: int = 0

    # Poll stats
    decisions_synced: int = 0

    # Errors
    errors: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# =============================================================================
# CONSUMER PIPELINE
# =============================================================================

class ConsumerPipeline:
    """
    End-to-end consumer signal discovery pipeline.

    Stages:
    1. collect() - Gather signals from HN, BevNet, Reddit, etc.
    2. filter_pending() - Run two-stage thesis filter
    3. push_qualified() - Push passing signals to Notion
    4. poll_decisions() - Sync user decisions from Notion

    Usage:
        async with consumer_store("signals.db") as store:
            pipeline = ConsumerPipeline(store)
            result = await pipeline.run()
    """

    def __init__(
        self,
        store: Optional[ConsumerStore] = None,
        db_path: str = "consumer_signals.db",
        skip_llm: bool = False,
        skip_notion: bool = False,
    ):
        """
        Initialize pipeline.

        Args:
            store: ConsumerStore instance (created if not provided)
            db_path: Database path (used if store not provided)
            skip_llm: Skip LLM classification (for testing)
            skip_notion: Skip Notion push/poll (for testing)
        """
        self._store = store
        self._db_path = db_path
        self.skip_llm = skip_llm
        self.skip_notion = skip_notion

        self._thesis_filter: Optional[ThesisFilterPipeline] = None
        self._pusher: Optional[NotionPusher] = None
        self._poller: Optional[NotionPoller] = None

    @property
    def store(self) -> ConsumerStore:
        if self._store is None:
            raise RuntimeError("Store not initialized. Use 'async with' or call initialize()")
        return self._store

    async def initialize(self) -> None:
        """Initialize pipeline components."""
        if self._store is None:
            self._store = ConsumerStore(self._db_path)
            await self._store.initialize()

        self._thesis_filter = ThesisFilterPipeline(skip_llm=self.skip_llm)

        if not self.skip_notion:
            self._pusher = NotionPusher(self._store)
            self._poller = NotionPoller(self._store)

    async def close(self) -> None:
        """Close pipeline resources."""
        if self._store:
            await self._store.close()
            self._store = None

    async def __aenter__(self) -> ConsumerPipeline:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # =========================================================================
    # MAIN RUN
    # =========================================================================

    async def run(self) -> PipelineRunResult:
        """
        Run full pipeline: collect → filter → push → poll.

        Returns:
            PipelineRunResult with all statistics
        """
        result = PipelineRunResult()

        try:
            # Stage 1: Collect
            logger.info("Stage 1: Collecting signals...")
            collect_result = await self.collect()
            result.collector_results = collect_result
            result.signals_collected = sum(r.signals_found for r in collect_result)
            result.signals_new = sum(r.signals_new for r in collect_result)

            # Stage 2: Filter
            logger.info("Stage 2: Filtering pending signals...")
            filter_stats = await self.filter_pending()
            result.signals_filtered = filter_stats.get("total", 0)
            result.auto_rejected = filter_stats.get("auto_reject", 0)
            result.llm_rejected = filter_stats.get("llm_reject", 0)
            result.llm_review = filter_stats.get("llm_review", 0)
            result.llm_auto_approved = filter_stats.get("llm_auto", 0)

            # Stage 3: Push
            if not self.skip_notion:
                logger.info("Stage 3: Pushing qualified signals to Notion...")
                push_stats = await self.push_qualified()
                result.signals_pushed = push_stats.get("pushed", 0)
                result.push_errors = push_stats.get("errors", 0)
                result.push_skipped = push_stats.get("skipped", 0)

            # Stage 4: Poll
            if not self.skip_notion:
                logger.info("Stage 4: Polling Notion for decisions...")
                result.decisions_synced = await self.poll_decisions()

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            result.errors.append(str(e))

        result.completed_at = datetime.now(timezone.utc)

        # Summary logging
        logger.info(
            f"Pipeline complete in {result.duration_seconds:.1f}s: "
            f"{result.signals_new} new signals, "
            f"{result.signals_pushed} pushed to Notion"
        )

        return result

    # =========================================================================
    # COLLECTION
    # =========================================================================

    async def collect(self) -> List[CollectorResult]:
        """
        Run all collectors.

        Returns:
            List of CollectorResults
        """
        collectors: List[ConsumerCollector] = [
            HNCollector(self.store),
            BevNetCollector(self.store),
            RedditCollector(self.store),
            # USPTOCollector requires API key setup
        ]

        return await run_collectors(collectors, max_concurrent=3)

    # =========================================================================
    # FILTERING
    # =========================================================================

    async def filter_pending(self, batch_size: int = 50) -> Dict[str, int]:
        """
        Filter pending signals through thesis filter.

        Returns:
            Dict with counts by filter result type
        """
        if self._thesis_filter is None:
            self._thesis_filter = ThesisFilterPipeline(skip_llm=self.skip_llm)

        # Get pending signals
        pending = await self.store.get_pending_signals(limit=batch_size)
        logger.debug(f"Found {len(pending)} pending signals")

        stats = {
            "total": 0,
            "auto_reject": 0,
            "llm_reject": 0,
            "llm_review": 0,
            "llm_auto": 0,
        }

        for signal in pending:
            # Build signal data dict for filter
            signal_data = {
                "title": signal.title or "",
                "url": signal.url,
                "source_api": signal.source_api,
                "source_context": signal.source_context or "",
            }

            # Run filter
            result = await self._thesis_filter.filter(signal_data)
            stats["total"] += 1
            stats[result.result_type.value] = stats.get(result.result_type.value, 0) + 1

            # Update signal in store
            await self.store.update_signal_filter_result(
                signal_id=signal.id,
                filter_result=result.result_type.value,
                filter_stage=result.stage,
            )

            # Save classification if present
            if result.classification:
                await self.store.save_classification(
                    signal_id=signal.id,
                    model=result.classification.model,
                    prompt_version=result.classification.prompt_version,
                    thesis_match=result.classification.thesis_match,
                    confidence=result.classification.thesis_fit_score,
                    categories=[result.classification.category],
                    reasoning=result.classification.rationale,
                    input_tokens=result.classification.input_tokens,
                    output_tokens=result.classification.output_tokens,
                    latency_ms=result.classification.latency_ms,
                )

        return stats

    # =========================================================================
    # NOTION PUSH
    # =========================================================================

    async def push_qualified(self) -> Dict[str, int]:
        """
        Push qualified signals (LLM_REVIEW or LLM_AUTO) to Notion.

        Returns:
            Dict with push statistics
        """
        if self.skip_notion or self._pusher is None:
            return {"pushed": 0, "errors": 0, "skipped": 0}

        # Query for qualified signals not yet in Notion
        # (status still 'pending' but filter_result is llm_review or llm_auto)
        if not self.store._db:
            return {"pushed": 0, "errors": 0, "skipped": 0}

        cursor = await self.store._db.execute(
            """
            SELECT id, source_api, source_id, signal_type, content_hash,
                   title, url, source_context, raw_metadata,
                   status, filter_result, filter_stage,
                   extracted_company_name, notion_page_id, company_id,
                   first_seen_at, last_seen_at, created_at, updated_at
            FROM signals
            WHERE status = 'pending'
              AND filter_result IN ('llm_review', 'llm_auto')
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        rows = await cursor.fetchall()
        signals = [self.store._row_to_signal(row) for row in rows]

        stats = {"pushed": 0, "errors": 0, "skipped": 0}

        for signal in signals:
            # Get classification for this signal
            classification = await self.store.get_classification(signal.id)

            # Build filter result from stored data
            result_type = (
                FilterResultType.LLM_AUTO_APPROVE
                if signal.filter_result == "llm_auto"
                else FilterResultType.LLM_REVIEW
            )

            from ..thesis_filter.llm_classifier import ThesisClassification
            thesis_class = None
            if classification:
                thesis_class = ThesisClassification(
                    thesis_match=classification.thesis_match,
                    thesis_fit_score=classification.confidence,
                    category=classification.categories[0] if classification.categories else "other",
                    stage_estimate="unknown",
                    confidence="high" if classification.confidence > 0.7 else "medium",
                    company_name=signal.extracted_company_name,
                    rationale=classification.reasoning,
                    key_signals=[],
                    prompt_version=classification.prompt_version,
                    model=classification.model,
                )

            filter_result = FilterResult(
                result_type=result_type,
                passed=True,
                stage="llm_classifier",
                classification=thesis_class,
                score=classification.confidence if classification else 0.5,
                category=classification.categories[0] if classification and classification.categories else "other",
            )

            # Push to Notion
            push_result = await self._pusher.push_signal(signal, filter_result)

            if push_result.success:
                stats["pushed"] += 1
            elif push_result.skipped_reason:
                stats["skipped"] += 1
            else:
                stats["errors"] += 1

        return stats

    # =========================================================================
    # NOTION POLL
    # =========================================================================

    async def poll_decisions(self, since_minutes: int = 10) -> int:
        """
        Poll Notion for user decisions.

        Returns:
            Number of decisions synced
        """
        if self.skip_notion or self._poller is None:
            return 0

        return await self._poller.poll_and_sync(since_minutes=since_minutes)

    # =========================================================================
    # UTILITIES
    # =========================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """Get overall pipeline statistics."""
        store_stats = await self.store.get_stats()

        # Add cost summary
        cost_summary = await self.store.get_cost_summary(days=30)

        return {
            **store_stats,
            "cost_last_30_days": cost_summary,
        }
