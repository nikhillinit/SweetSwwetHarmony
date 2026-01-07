"""
Base Collector Class for Discovery Engine

Provides common functionality for all collectors:
- SignalStore integration for persistence
- Async context manager pattern
- Deduplication checking
- Error handling for batch operations
- Accurate signal counting (new vs suppressed)
- Retry logic with exponential backoff
- Per-API rate limiting

All collectors should inherit from BaseCollector and implement:
- _collect_signals(): Fetch raw signals from source
- _convert_to_signals(): Convert raw data to Signal objects
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, TypeVar

import httpx

from collectors.retry_strategy import RetryConfig, with_retry
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.rate_limiter import AsyncRateLimiter, get_rate_limiter
from verification.verification_gate_v2 import Signal

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseCollector(ABC):
    """
    Base class for all signal collectors.

    Provides:
    - Optional SignalStore integration for persistence
    - Deduplication checking via canonical keys
    - Batch error handling (don't fail entire run if one signal fails)
    - Accurate counting (signals_new vs signals_suppressed)
    - Async context manager pattern

    Usage:
        class MyCollector(BaseCollector):
            async def _collect_signals(self) -> List[Signal]:
                # Fetch and convert signals
                return signals

        collector = MyCollector(store=signal_store)
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        collector_name: str = "unknown",
        retry_config: Optional[RetryConfig] = None,
        api_name: Optional[str] = None,
    ):
        """
        Args:
            store: Optional SignalStore instance for persistence
            collector_name: Name of collector (for logging and results)
            retry_config: Configuration for retry behavior (default: RetryConfig())
            api_name: API name for rate limiting (e.g., "github", "sec_edgar")
        """
        self.store = store
        self.collector_name = collector_name
        self.retry_config = retry_config or RetryConfig()
        self.api_name = api_name

        # Set up rate limiter based on api_name
        if api_name:
            self._rate_limiter = get_rate_limiter(api_name)
        else:
            # Unlimited rate limiter for unknown APIs
            self._rate_limiter = AsyncRateLimiter(rate=None, period=1)

        # Track what we've seen in this run
        self._processed_canonical_keys: set[str] = set()

        # Statistics
        self._signals_found = 0
        self._signals_new = 0
        self._signals_suppressed = 0
        self._retry_count = 0
        self._errors: List[str] = []

    async def __aenter__(self):
        """Async context manager entry - implement in subclass if needed"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - implement in subclass if needed"""
        pass

    @abstractmethod
    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from the source.

        This method should be implemented by subclasses to:
        1. Fetch raw data from the source API
        2. Convert to Signal objects
        3. Return list of signals

        Returns:
            List of Signal objects
        """
        pass

    async def run(self, dry_run: bool = True) -> CollectorResult:
        """
        Main entry point: run the collector and optionally save to SignalStore.

        Args:
            dry_run: If True, don't persist signals (just collect and analyze)

        Returns:
            CollectorResult with statistics
        """
        logger.info(
            f"Starting {self.collector_name} collector "
            f"(dry_run={dry_run}, store={'enabled' if self.store else 'disabled'})"
        )

        # Reset statistics
        self._signals_found = 0
        self._signals_new = 0
        self._signals_suppressed = 0
        self._errors = []

        try:
            # Use context manager if needed
            async with self:
                # Collect signals from source
                signals = await self._collect_signals()
                self._signals_found = len(signals)

                logger.info(f"Collected {self._signals_found} signals from {self.collector_name}")

                # If we have a store and not in dry run mode, save signals
                if self.store and not dry_run:
                    await self._save_signals(signals)
                else:
                    # In dry run or no store, just check for duplicates
                    if self.store:
                        await self._check_duplicates(signals)
                    else:
                        # No store = all signals are "new"
                        self._signals_new = self._signals_found
                        self._signals_suppressed = 0

                # Determine status
                if dry_run:
                    status = CollectorStatus.DRY_RUN
                elif self._errors:
                    status = CollectorStatus.PARTIAL_SUCCESS
                else:
                    status = CollectorStatus.SUCCESS

                return CollectorResult(
                    collector=self.collector_name,
                    status=status,
                    signals_found=self._signals_found,
                    signals_new=self._signals_new,
                    signals_suppressed=self._signals_suppressed,
                    dry_run=dry_run,
                    error_message="; ".join(self._errors[:5]) if self._errors else None,
                )

        except Exception as e:
            logger.exception(f"{self.collector_name} collector failed")
            return CollectorResult(
                collector=self.collector_name,
                status=CollectorStatus.ERROR,
                signals_found=0,
                signals_new=0,
                signals_suppressed=0,
                dry_run=dry_run,
                error_message=str(e),
            )

    async def _save_signals(self, signals: List[Signal]) -> None:
        """
        Save signals to SignalStore with deduplication checking.

        Updates:
        - self._signals_new: Count of successfully saved signals
        - self._signals_suppressed: Count of duplicate/suppressed signals
        - self._errors: List of error messages

        Args:
            signals: List of Signal objects to save
        """
        if not self.store:
            logger.warning("No SignalStore configured, skipping save")
            return

        logger.info(f"Saving {len(signals)} signals to SignalStore...")

        for signal in signals:
            try:
                # Extract canonical key from signal
                canonical_key = self._extract_canonical_key(signal)

                if not canonical_key:
                    logger.warning(
                        f"Signal {signal.id} has no canonical key, "
                        f"using signal ID as fallback"
                    )
                    canonical_key = signal.id

                # Skip if we already processed this key in this run
                if canonical_key in self._processed_canonical_keys:
                    logger.debug(f"Already processed {canonical_key} in this run")
                    self._signals_suppressed += 1
                    continue

                # Check if already in database
                is_duplicate = await self.store.is_duplicate(canonical_key)

                if is_duplicate:
                    logger.debug(f"Duplicate signal: {canonical_key}")
                    self._signals_suppressed += 1
                    self._processed_canonical_keys.add(canonical_key)
                    continue

                # Check suppression cache (already in Notion?)
                suppression = await self.store.check_suppression(canonical_key)
                if suppression:
                    logger.debug(
                        f"Suppressed signal: {canonical_key} "
                        f"(already in Notion as {suppression.notion_page_id})"
                    )
                    self._signals_suppressed += 1
                    self._processed_canonical_keys.add(canonical_key)
                    continue

                # New signal! Save it
                signal_id = await self.store.save_signal(
                    signal_type=signal.signal_type,
                    source_api=signal.source_api,
                    canonical_key=canonical_key,
                    confidence=signal.confidence,
                    raw_data=signal.raw_data,
                    company_name=signal.raw_data.get("company_name"),
                    detected_at=signal.detected_at,
                )

                logger.info(
                    f"Saved signal {signal_id}: {signal.signal_type} "
                    f"for {canonical_key} (confidence: {signal.confidence:.2f})"
                )

                self._signals_new += 1
                self._processed_canonical_keys.add(canonical_key)

            except Exception as e:
                error_msg = f"Error saving signal {signal.id}: {str(e)}"
                logger.error(error_msg)
                self._errors.append(error_msg)
                # Continue with next signal - don't fail entire batch

        logger.info(
            f"Save complete: {self._signals_new} new, "
            f"{self._signals_suppressed} suppressed, "
            f"{len(self._errors)} errors"
        )

    async def _check_duplicates(self, signals: List[Signal]) -> None:
        """
        Check signals against SignalStore for duplicates (dry run mode).

        Updates:
        - self._signals_new: Count of non-duplicate signals
        - self._signals_suppressed: Count of duplicate signals

        Args:
            signals: List of Signal objects to check
        """
        if not self.store:
            return

        logger.info(f"Checking {len(signals)} signals for duplicates (dry run)...")

        for signal in signals:
            try:
                canonical_key = self._extract_canonical_key(signal)

                if not canonical_key:
                    canonical_key = signal.id

                # Skip if already checked in this run
                if canonical_key in self._processed_canonical_keys:
                    self._signals_suppressed += 1
                    continue

                # Check database
                is_duplicate = await self.store.is_duplicate(canonical_key)

                if is_duplicate:
                    self._signals_suppressed += 1
                else:
                    # Check suppression cache
                    suppression = await self.store.check_suppression(canonical_key)
                    if suppression:
                        self._signals_suppressed += 1
                    else:
                        self._signals_new += 1

                self._processed_canonical_keys.add(canonical_key)

            except Exception as e:
                logger.warning(f"Error checking signal {signal.id}: {e}")
                # Assume new if we can't check
                self._signals_new += 1

    def _extract_canonical_key(self, signal: Signal) -> str:
        """
        Extract canonical key from a signal's raw_data.

        Looks for:
        1. raw_data["canonical_key"]
        2. raw_data["canonical_key_candidates"][0]
        3. Falls back to signal.id

        Args:
            signal: Signal object

        Returns:
            Canonical key string
        """
        raw_data = signal.raw_data or {}

        # First try direct canonical_key
        if "canonical_key" in raw_data and raw_data["canonical_key"]:
            return raw_data["canonical_key"]

        # Try canonical_key_candidates
        if "canonical_key_candidates" in raw_data:
            candidates = raw_data["canonical_key_candidates"]
            if isinstance(candidates, list) and len(candidates) > 0:
                return candidates[0]

        # Fall back to signal ID
        return signal.id

    @property
    def rate_limiter(self) -> AsyncRateLimiter:
        """Get the rate limiter for this collector's API."""
        return self._rate_limiter

    async def _fetch_with_retry(self, func: Callable[[], T]) -> T:
        """
        Execute an async function with retry and rate limiting.

        This helper combines:
        1. Rate limit acquisition (waits if needed)
        2. Retry with exponential backoff on transient errors

        Args:
            func: Async function to execute (no arguments)

        Returns:
            Result of func() on success

        Raises:
            The last exception if all retries exhausted
        """
        async def rate_limited_func() -> T:
            await self._rate_limiter.acquire()
            return await func()

        # Track retries
        original_retry_count = self._retry_count

        async def tracking_func() -> T:
            try:
                return await rate_limited_func()
            except Exception:
                self._retry_count += 1
                raise

        try:
            return await with_retry(tracking_func, self.retry_config)
        except Exception:
            # Don't count the final failure as a retry
            self._retry_count = max(0, self._retry_count - 1)
            raise

    async def _http_get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Any:
        """
        Make an HTTP GET request with retry and rate limiting.

        Convenience method that combines:
        - Rate limit acquisition
        - Retry on transient HTTP errors
        - JSON response parsing

        Args:
            url: URL to fetch
            headers: Optional request headers
            params: Optional query parameters
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors
            Exception: On exhausted retries
        """
        async def do_request() -> Any:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

        return await self._fetch_with_retry(do_request)


# =============================================================================
# CONTEXT MANAGER FOR EASY USAGE
# =============================================================================

@asynccontextmanager
async def with_store(
    collector_class: type[BaseCollector],
    store: Optional[SignalStore] = None,
    **collector_kwargs
) -> AsyncIterator[BaseCollector]:
    """
    Context manager for collectors with SignalStore integration.

    Usage:
        from storage.signal_store import signal_store

        async with signal_store("signals.db") as store:
            async with with_store(MyCollector, store=store, **kwargs) as collector:
                result = await collector.run(dry_run=False)

    Args:
        collector_class: BaseCollector subclass
        store: SignalStore instance
        **collector_kwargs: Arguments for collector constructor

    Yields:
        Collector instance
    """
    collector = collector_class(store=store, **collector_kwargs)
    async with collector:
        yield collector
