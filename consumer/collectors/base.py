"""
Base Collector for Consumer Discovery Engine

Abstract base class for all signal collectors.
Provides common functionality:
- Storage integration
- Run tracking
- Error handling
- Deduplication checks
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.consumer_store import ConsumerStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Signal:
    """Raw signal from a collector."""
    source_api: str
    source_id: str
    signal_type: str = "mention"
    title: Optional[str] = None
    url: Optional[str] = None
    source_context: Optional[str] = None
    raw_metadata: Optional[Dict[str, Any]] = None
    extracted_company_name: Optional[str] = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CollectorResult:
    """Result of a collector run."""
    collector_name: str
    signals_found: int = 0
    signals_new: int = 0
    signals_duplicate: int = 0
    api_calls_made: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """True if no errors occurred."""
        return len(self.errors) == 0

    @property
    def status(self) -> str:
        """Status string for database."""
        if not self.errors:
            return "success"
        elif self.signals_new > 0:
            return "partial_failure"
        else:
            return "failure"


# =============================================================================
# ABSTRACT BASE COLLECTOR
# =============================================================================

class ConsumerCollector(ABC):
    """
    Abstract base class for consumer signal collectors.

    Subclasses must implement:
    - name: Collector name (e.g., "hn", "reddit")
    - collect(): Async method to fetch signals

    Usage:
        class HNCollector(ConsumerCollector):
            name = "hn"

            async def collect(self) -> List[Signal]:
                # Fetch from HN API
                return signals

        async with consumer_store("db.sqlite") as store:
            collector = HNCollector(store)
            result = await collector.run()
    """

    # Override in subclasses
    name: str = "base"

    def __init__(self, store: Optional[ConsumerStore] = None):
        """
        Initialize collector.

        Args:
            store: ConsumerStore instance (optional, for dedup checks)
        """
        self.store = store
        self._run_id: Optional[int] = None
        self._api_calls = 0

    @abstractmethod
    async def collect(self) -> List[Signal]:
        """
        Collect signals from the source.

        Must be implemented by subclasses.

        Returns:
            List of Signal objects
        """
        raise NotImplementedError

    async def run(self) -> CollectorResult:
        """
        Execute the collector with full lifecycle management.

        1. Start run tracking (if store available)
        2. Call collect() to fetch signals
        3. Save new signals to store (if available)
        4. Complete run tracking
        5. Return result summary

        Returns:
            CollectorResult with statistics
        """
        start_time = datetime.now(timezone.utc)
        result = CollectorResult(collector_name=self.name)
        self._api_calls = 0

        # Start run tracking
        if self.store:
            self._run_id = await self.store.start_collector_run(self.name)

        try:
            # Collect signals
            signals = await self.collect()
            result.signals_found = len(signals)
            result.api_calls_made = self._api_calls

            # Save to store (with dedup)
            if self.store and signals:
                new_count, dup_count = await self._save_signals(signals)
                result.signals_new = new_count
                result.signals_duplicate = dup_count

            logger.info(
                f"{self.name}: Found {result.signals_found}, "
                f"new {result.signals_new}, duplicate {result.signals_duplicate}"
            )

        except Exception as e:
            logger.error(f"{self.name} collector failed: {e}")
            result.errors.append(str(e))

        # Calculate duration
        end_time = datetime.now(timezone.utc)
        result.duration_seconds = (end_time - start_time).total_seconds()

        # Complete run tracking
        if self.store and self._run_id:
            await self.store.complete_collector_run(
                run_id=self._run_id,
                status=result.status,
                signals_found=result.signals_found,
                signals_new=result.signals_new,
                error_message="; ".join(result.errors) if result.errors else None,
                api_calls_made=result.api_calls_made,
            )

        return result

    async def _save_signals(self, signals: List[Signal]) -> tuple[int, int]:
        """
        Save signals to store, handling duplicates.

        Returns:
            (new_count, duplicate_count)
        """
        new_count = 0
        dup_count = 0

        for signal in signals:
            signal_id, is_new = await self.store.save_signal(
                source_api=signal.source_api,
                source_id=signal.source_id,
                signal_type=signal.signal_type,
                title=signal.title,
                url=signal.url,
                source_context=signal.source_context,
                raw_metadata=signal.raw_metadata,
                extracted_company_name=signal.extracted_company_name,
            )

            if is_new:
                new_count += 1
            else:
                dup_count += 1

        return new_count, dup_count

    def track_api_call(self, count: int = 1) -> None:
        """Track API calls for monitoring."""
        self._api_calls += count

    async def is_duplicate(self, source_id: str) -> bool:
        """
        Check if signal already exists in store.

        Args:
            source_id: Source-specific signal ID

        Returns:
            True if duplicate
        """
        if not self.store:
            return False
        return await self.store.is_duplicate(self.name, source_id)


# =============================================================================
# UTILITIES
# =============================================================================

async def run_collectors(
    collectors: List[ConsumerCollector],
    max_concurrent: int = 3,
) -> List[CollectorResult]:
    """
    Run multiple collectors with concurrency control.

    Args:
        collectors: List of collector instances
        max_concurrent: Max concurrent collectors

    Returns:
        List of CollectorResults
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(collector: ConsumerCollector) -> CollectorResult:
        async with semaphore:
            return await collector.run()

    return await asyncio.gather(
        *[run_with_semaphore(c) for c in collectors]
    )
