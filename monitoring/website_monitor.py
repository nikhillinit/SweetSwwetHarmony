"""
Website Monitor - Core monitoring logic for Discovery Engine

Coordinates:
- Fetching pages via URLProfiler.fetch_single()
- Computing diffs via DiffEngine
- Storing results via MonitorStore
- Triggering actions based on severity

Usage:
    monitor = WebsiteMonitor(signal_store, embedding_store, generator)
    result = await monitor.check_watch(watch)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from storage.embedding_store import EmbeddingStore
    from utils.embedding_generator import EmbeddingGenerator
    from profilers.url_profiler import URLProfiler

from monitoring.models import (
    Watch,
    Snapshot,
    Diff,
    MonitoringAlert,
    MonitoringConfig,
)
from monitoring.monitor_store import MonitorStore
from monitoring.diff_engine import DiffEngine, DiffResult
from monitoring.events import EventType, ProfileUpdateRequestedPayload
from monitoring.failure_classifier import FailureClassifier, classify_failure
from monitoring.gating import GatingEngine, GatingConfig
from utils.page_state import detect_page_state
from profilers.url_profiler import HASHER_VERSION

logger = logging.getLogger(__name__)


class MonitoringResult:
    """Result of checking a single watch."""

    def __init__(
        self,
        watch: Watch,
        snapshot: Optional[Snapshot] = None,
        diff: Optional[Diff] = None,
        alert: Optional[MonitoringAlert] = None,
        error: Optional[str] = None,
        skipped: bool = False,
        skip_reason: Optional[str] = None,
    ):
        self.watch = watch
        self.snapshot = snapshot
        self.diff = diff
        self.alert = alert
        self.error = error
        self.skipped = skipped
        self.skip_reason = skip_reason

    @property
    def success(self) -> bool:
        return self.snapshot is not None and self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "watch_id": self.watch.id,
            "canonical_key": self.watch.canonical_key,
            "url": self.watch.url,
            "success": self.success,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "snapshot_id": self.snapshot.id if self.snapshot else None,
            "diff_id": self.diff.id if self.diff else None,
            "severity_score": self.diff.severity_score if self.diff else None,
            "alert_id": self.alert.id if self.alert else None,
            "error": self.error,
        }


class WebsiteMonitor:
    """
    Main class for monitoring website changes.

    Coordinates fetching, diffing, and action triggering.
    """

    def __init__(
        self,
        signal_store: "SignalStore",
        embedding_store: Optional["EmbeddingStore"] = None,
        embedding_generator: Optional["EmbeddingGenerator"] = None,
        profiler: Optional["URLProfiler"] = None,
        config: Optional[MonitoringConfig] = None,
    ):
        """
        Initialize WebsiteMonitor.

        Args:
            signal_store: Signal store for database access
            embedding_store: Store for embeddings (for semantic drift)
            embedding_generator: Generator for embeddings
            profiler: URLProfiler instance (created if not provided)
            config: Monitoring configuration
        """
        self._signal_store = signal_store
        self._embedding_store = embedding_store
        self._embedding_generator = embedding_generator
        self._profiler = profiler
        self._config = config

        # Lazy-initialized components
        self._store: Optional[MonitorStore] = None
        self._diff_engine: Optional[DiffEngine] = None

    @property
    def store(self) -> MonitorStore:
        """Get or create MonitorStore."""
        if self._store is None:
            self._store = MonitorStore(self._signal_store)
        return self._store

    @property
    def diff_engine(self) -> DiffEngine:
        """Get or create DiffEngine."""
        if self._diff_engine is None:
            self._diff_engine = DiffEngine(
                embedding_store=self._embedding_store,
                embedding_generator=self._embedding_generator,
                config=self._config or MonitoringConfig(),
            )
        return self._diff_engine

    async def get_config(self) -> MonitoringConfig:
        """Get monitoring configuration from database."""
        if self._config is None:
            self._config = await self.store.get_config()
        return self._config

    async def get_profiler(self) -> "URLProfiler":
        """Get or create URLProfiler."""
        if self._profiler is None:
            from profilers.url_profiler import URLProfiler
            self._profiler = URLProfiler(signal_store=self._signal_store)
        return self._profiler

    async def check_watch(self, watch: Watch) -> MonitoringResult:
        """
        Check a single watch for changes.

        This is the main entry point for monitoring a URL.

        Args:
            watch: Watch to check

        Returns:
            MonitoringResult with snapshot, diff, and action info
        """
        logger.info(f"Checking watch {watch.id}: {watch.url}")

        # Get latest snapshot for comparison
        old_snapshot = await self.store.get_latest_snapshot(watch.id)

        # Fetch the page
        profiler = await self.get_profiler()
        try:
            fetch_result = await profiler.fetch_single(watch.url)
        except Exception as e:
            logger.error(f"Failed to fetch {watch.url}: {e}")
            # Classify failure and apply backoff (v2.4)
            failure = classify_failure(
                status_code=None,
                error_message=str(e),
                retry_after=None,
            )
            await self.store.update_watch_failed(
                watch.id,
                failure_category=failure.category.value,
                error_message=str(e)[:500],
                backoff_seconds=failure.backoff.total_seconds(),
            )
            return MonitoringResult(watch=watch, error=str(e))

        # Check for fetch error
        if fetch_result.error:
            logger.warning(f"Fetch error for {watch.url}: {fetch_result.error}")
            # Classify failure and apply backoff (v2.4)
            failure = classify_failure(
                status_code=fetch_result.status_code,
                error_message=fetch_result.error,
                retry_after=None,
            )
            await self.store.update_watch_failed(
                watch.id,
                failure_category=failure.category.value,
                error_message=fetch_result.error[:500] if fetch_result.error else None,
                backoff_seconds=failure.backoff.total_seconds(),
            )
            return MonitoringResult(watch=watch, error=fetch_result.error)

        # Create snapshot
        config = await self.get_config()
        snapshot = self._create_snapshot(watch, fetch_result)

        # Recent-hash guard (v2.4): Two-step idempotency check
        # Step 1: Check if content unchanged from old_snapshot
        if old_snapshot and old_snapshot.content_hash == snapshot.content_hash:
            # Step 2: Also check recent snapshots within window (hasher_version match)
            is_unchanged, recent = await self.store.check_hash_unchanged(
                watch,
                snapshot.content_hash,
                snapshot.hasher_version,
            )
            if is_unchanged:
                logger.debug(f"Content unchanged for watch {watch.id}, updating timestamp only")
                await self.store.update_watch_checked(watch.id)
                return MonitoringResult(
                    watch=watch,
                    skipped=True,
                    skip_reason="content_unchanged",
                )

        # Compute diff
        diff_result = await self.diff_engine.compute_diff(
            old_snapshot=old_snapshot,
            new_snapshot=snapshot,
            new_text_content=fetch_result.text_content,
        )

        # Store snapshot and diff atomically
        snapshot_id, diff_id = await self.store.save_snapshot_and_update_watch(
            watch=watch,
            snapshot=snapshot,
            diff=diff_result.diff if old_snapshot else None,  # No diff for first snapshot
        )

        # Update IDs
        snapshot.id = snapshot_id
        if diff_result.diff:
            diff_result.diff.id = diff_id
            diff_result.diff.new_snapshot_id = snapshot_id

        # Handle actions based on severity
        alert = None
        if diff_result.should_create_alert:
            alert = await self.store.create_monitoring_alert(
                watch_id=watch.id,
                diff_id=diff_id,
                alert_reason=diff_result.trigger_reason or "high_severity",
                severity_score=diff_result.diff.severity_score,
                payload={
                    "url": watch.url,
                    "canonical_key": watch.canonical_key,
                    "old_snapshot_id": old_snapshot.id if old_snapshot else None,
                    "new_snapshot_id": snapshot_id,
                },
            )

        # Trigger profile update if needed
        if diff_result.should_trigger_profile_update:
            # Check debounce
            should_trigger = await self.store.update_debounce_state(
                watch.id,
                diff_result.diff.severity_score,
                config,
            )

            if should_trigger:
                await self._enqueue_profile_update(
                    watch=watch,
                    snapshot_id=snapshot_id,
                    diff_id=diff_id,
                    trigger=diff_result.trigger_reason or "high_severity",
                )

                # Set cooldown
                await self.store.set_cooldown(watch.id, config)

        return MonitoringResult(
            watch=watch,
            snapshot=snapshot,
            diff=diff_result.diff if old_snapshot else None,
            alert=alert,
        )

    def _create_snapshot(
        self,
        watch: Watch,
        fetch_result: "PageFetchResult",
    ) -> Snapshot:
        """Create a Snapshot from a fetch result."""
        from profilers.url_profiler import PageFetchResult

        # Detect page state
        page_state = detect_page_state(
            fetch_result.text_content,
            status_code=fetch_result.status_code,
            text_length=len(fetch_result.text_content),
        )

        # Extract final host
        final_host = None
        if fetch_result.url:
            parsed = urlparse(fetch_result.url)
            final_host = parsed.netloc.lower()

        # Get hasher_version from fetch result (v2.4)
        hasher_version = getattr(fetch_result, 'hasher_version', HASHER_VERSION)

        return Snapshot(
            watch_id=watch.id,
            fetched_at=fetch_result.fetch_time,
            status_code=fetch_result.status_code,
            requested_url=watch.url,
            final_url=fetch_result.url,
            final_host=final_host,
            page_state=page_state,
            content_hash=fetch_result.content_hash or "",
            hasher_version=hasher_version,
            text_length=len(fetch_result.text_content),
            text_content_preview=fetch_result.text_content[:500] if fetch_result.text_content else None,
            error=fetch_result.error,
        )

    async def _enqueue_profile_update(
        self,
        watch: Watch,
        snapshot_id: int,
        diff_id: Optional[int],
        trigger: str,
    ) -> None:
        """Enqueue a profile update request via the outbox."""
        payload = ProfileUpdateRequestedPayload(
            watch_id=watch.id,
            snapshot_id=snapshot_id,
            diff_id=diff_id or 0,
            trigger=trigger,
            canonical_key=watch.canonical_key,
            url=watch.url,
        )

        # Enqueue via outbox
        idempotency_key = payload.dedupe_key()
        await self._signal_store.enqueue_notion_write(
            idempotency_key=idempotency_key,
            payload=payload.model_dump(),
        )

        logger.info(f"Enqueued profile update for {watch.canonical_key}: {trigger}")

    async def run_due_checks(
        self,
        limit: int = 100,
        watch_type: Optional[str] = None,
    ) -> List[MonitoringResult]:
        """
        Run checks on all due watches.

        Args:
            limit: Maximum number of watches to check
            watch_type: Optional filter for watch type (e.g., 'portfolio')

        Returns:
            List of MonitoringResults
        """
        run_id = str(uuid.uuid4())[:8]
        type_filter = f" ({watch_type})" if watch_type else ""
        logger.info(f"Starting monitoring run {run_id}{type_filter}")

        # Record run start
        await self.store.start_monitoring_run(run_id)

        # Get due watches (with optional type filter)
        watches = await self.store.get_due_watches(limit=limit, watch_type=watch_type)
        logger.info(f"Found {len(watches)} due watches{type_filter}")

        results = []
        high_severity_count = 0
        profile_updates = 0
        errors = []

        for watch in watches:
            try:
                result = await self.check_watch(watch)
                results.append(result)

                if result.diff and result.diff.severity_score >= 0.8:
                    high_severity_count += 1

                if result.alert:
                    profile_updates += 1

            except Exception as e:
                logger.error(f"Error checking watch {watch.id}: {e}")
                errors.append(f"Watch {watch.id}: {str(e)}")
                results.append(MonitoringResult(watch=watch, error=str(e)))

        # Record run completion
        await self.store.complete_monitoring_run(
            run_id=run_id,
            watches_checked=len(watches),
            snapshots_taken=sum(1 for r in results if r.snapshot),
            diffs_computed=sum(1 for r in results if r.diff),
            high_severity_events=high_severity_count,
            profile_updates_triggered=profile_updates,
            errors=errors if errors else None,
        )

        logger.info(
            f"Monitoring run {run_id} complete: "
            f"{len(watches)} checked, "
            f"{sum(1 for r in results if r.snapshot)} snapshots, "
            f"{high_severity_count} high severity"
        )

        return results


async def create_watch_for_company(
    signal_store: "SignalStore",
    canonical_key: str,
    url: str,
    watch_type: str = "website",
    interval_seconds: int = 86400,
) -> Watch:
    """
    Convenience function to create a watch for a company.

    Args:
        signal_store: Signal store instance
        canonical_key: Canonical key for the company
        url: URL to monitor
        watch_type: Type of watch
        interval_seconds: Check interval

    Returns:
        Created Watch
    """
    store = MonitorStore(signal_store)
    return await store.create_watch(
        canonical_key=canonical_key,
        url=url,
        watch_type=watch_type,
        interval_seconds=interval_seconds,
    )
