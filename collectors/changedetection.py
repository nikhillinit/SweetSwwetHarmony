"""
ChangeDetection.io Collector - Monitor website changes for startup signals.

when_to_use: When monitoring consumer startup websites for pricing changes,
  hiring activity, terms updates, and product announcements.

API: changedetection.io REST API (self-hosted or SaaS)
Cost: FREE (self-hosted) or ~$8/month (SaaS)
Signal Strength: MEDIUM-HIGH (0.5-0.85)

Website changes indicate:
1. Pricing strategy shifts (pricing page changes)
2. Hiring activity (careers page changes)
3. Policy/regulatory changes (terms/privacy updates)
4. Product evolution (product page updates)
5. Market positioning changes (landing page updates)

Aligned with Press On Ventures thesis:
- Changes to consumer startup websites are leading indicators
- Pricing changes often precede funding or pivots
- Hiring bursts indicate growth stage changes

Usage:
    # Mode 1: From environment variables
    export CHANGEDETECTION_URL=https://your-instance.local
    export CHANGEDETECTION_API_KEY=your-api-key

    collector = ChangeDetectionCollector()
    result = await collector.run(dry_run=True)

    # Mode 2: Explicit configuration
    collector = ChangeDetectionCollector(
        base_url="https://your-instance.local",
        api_key="your-api-key",
    )

Setup:
    1. Deploy changedetection.io via Docker:
       docker run -d -p 5000:5000 -v datastore:/datastore dgtlmoon/changedetection.io

    2. Add watches for target companies with tags:
       - pricing: /pricing, /plans, /pro
       - careers: /careers, /jobs, /join
       - terms: /terms, /privacy, /legal
       - product: /product, /features

    3. Configure API key in changedetection.io settings
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Page type to signal type mapping
PAGE_TYPE_SIGNALS: Dict[str, str] = {
    "pricing": "pricing_change",
    "careers": "hiring_signal",
    "jobs": "hiring_signal",
    "terms": "terms_change",
    "privacy": "terms_change",
    "legal": "terms_change",
    "product": "product_update",
    "features": "product_update",
    "landing": "website_update",
    "general": "website_update",
}

# Page type base confidence scores
CHANGE_TYPE_CONFIDENCE: Dict[str, float] = {
    "pricing": 0.75,      # High signal - pricing strategy
    "careers": 0.70,      # High signal - hiring activity
    "jobs": 0.70,
    "terms": 0.55,        # Medium signal - policy changes
    "privacy": 0.55,
    "legal": 0.55,
    "product": 0.65,      # Medium-high - product evolution
    "features": 0.65,
    "landing": 0.50,      # Medium - positioning changes
    "general": 0.45,      # Lower - unknown page type
}

# High-value page types (always significant)
HIGH_VALUE_PAGE_TYPES = {"pricing", "careers", "jobs", "terms", "privacy"}

# Minimum lines changed for significance (if not high-value page)
MIN_SIGNIFICANT_LINES = 10


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class WatchConfig:
    """Configuration for a single watch in changedetection.io."""

    uuid: str
    url: str
    title: str
    page_type: str = "general"
    company_name: Optional[str] = None
    canonical_key: Optional[str] = None
    check_interval: int = 3600  # seconds
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, uuid: str, data: Dict[str, Any]) -> "WatchConfig":
        """Create WatchConfig from changedetection.io API response."""
        tags = []
        if "tag" in data and data["tag"]:
            tags = [t.strip() for t in data["tag"].split(",")]

        # Infer page_type from tags or URL
        page_type = "general"
        url_lower = data.get("url", "").lower()
        for pt in PAGE_TYPE_SIGNALS.keys():
            if pt in tags or f"/{pt}" in url_lower:
                page_type = pt
                break

        # Extract company info from tags or title
        company_name = None
        canonical_key = None
        for tag in tags:
            if tag.startswith("company:"):
                company_name = tag.replace("company:", "")
            elif tag.startswith("domain:") or tag.startswith("name:"):
                canonical_key = tag

        return cls(
            uuid=uuid,
            url=data.get("url", ""),
            title=data.get("title", ""),
            page_type=page_type,
            company_name=company_name,
            canonical_key=canonical_key,
            check_interval=data.get("time_between_check", {}).get("seconds", 3600),
            tags=tags,
        )


@dataclass
class ChangeEvent:
    """A detected change from changedetection.io."""

    watch_uuid: str
    watch_url: str
    watch_title: str
    page_type: str
    company_name: Optional[str]
    canonical_key: Optional[str]
    change_detected_at: datetime
    previous_hash: str
    current_hash: str
    diff_summary: str
    diff_lines_added: int
    diff_lines_removed: int
    change_type: str  # "content", "visual", "text"
    snapshot_url: Optional[str] = None

    @property
    def total_lines_changed(self) -> int:
        """Total number of lines changed."""
        return self.diff_lines_added + self.diff_lines_removed

    @property
    def is_significant(self) -> bool:
        """Check if this change is significant enough to report."""
        # High-value page types are always significant
        if self.page_type in HIGH_VALUE_PAGE_TYPES:
            return True
        # For other pages, require minimum change size
        return self.total_lines_changed >= MIN_SIGNIFICANT_LINES

    @property
    def age_days(self) -> int:
        """Age of change in days."""
        delta = datetime.now(timezone.utc) - self.change_detected_at
        return max(0, delta.days)

    @classmethod
    def from_api_response(
        cls,
        watch: WatchConfig,
        history_entry: Dict[str, Any],
        diff_data: Optional[Dict[str, Any]] = None,
    ) -> "ChangeEvent":
        """Create ChangeEvent from API response."""
        timestamp = history_entry.get("timestamp")
        if isinstance(timestamp, str):
            detected_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        elif isinstance(timestamp, (int, float)):
            detected_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            detected_at = datetime.now(timezone.utc)

        diff_summary = ""
        lines_added = 0
        lines_removed = 0
        if diff_data:
            diff_summary = diff_data.get("summary", "")
            lines_added = diff_data.get("lines_added", 0)
            lines_removed = diff_data.get("lines_removed", 0)

        return cls(
            watch_uuid=watch.uuid,
            watch_url=watch.url,
            watch_title=watch.title,
            page_type=watch.page_type,
            company_name=watch.company_name,
            canonical_key=watch.canonical_key,
            change_detected_at=detected_at,
            previous_hash=history_entry.get("previous_md5", ""),
            current_hash=history_entry.get("current_md5", ""),
            diff_summary=diff_summary,
            diff_lines_added=lines_added,
            diff_lines_removed=lines_removed,
            change_type="content",
            snapshot_url=history_entry.get("screenshot"),
        )


# =============================================================================
# COLLECTOR IMPLEMENTATION
# =============================================================================

class ChangeDetectionCollector(BaseCollector):
    """
    Collector for changedetection.io website monitoring.

    Fetches change events from a changedetection.io instance
    and converts them to signals for the discovery pipeline.
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        lookback_days: int = 7,
        min_significance: bool = True,
        http: Optional[Any] = None,
        asset_store: Optional[Any] = None,
    ):
        """
        Initialize the changedetection.io collector.

        Args:
            store: Optional SignalStore for persistence
            base_url: URL of changedetection.io instance
            api_key: API key for authentication
            lookback_days: How far back to look for changes (default: 7)
            min_significance: Whether to filter insignificant changes (default: True)
            http: Optional shared CollectorHttpClient for connection pooling
            asset_store: Optional SourceAssetStore for change detection
        """
        super().__init__(
            store=store,
            collector_name="changedetection",
            retry_config=RetryConfig(max_retries=2, backoff_base=1.0),
            api_name="changedetection",
            http=http,
            asset_store=asset_store,
        )

        # Get configuration from params or environment
        self.base_url = base_url or os.getenv("CHANGEDETECTION_URL")
        self.api_key = api_key or os.getenv("CHANGEDETECTION_API_KEY")

        if not self.base_url:
            raise ValueError(
                "CHANGEDETECTION_URL environment variable or base_url parameter required"
            )

        # Ensure base_url has no trailing slash
        self.base_url = self.base_url.rstrip("/")

        self.lookback_days = lookback_days
        self.min_significance = min_significance

        # Track processed changes
        self._processed_hashes: set[str] = set()

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from changedetection.io.

        Returns:
            List of Signal objects from detected changes
        """
        signals = []

        try:
            # Fetch all watches
            watches = await self._fetch_watches()
            logger.info(f"Found {len(watches)} watches in changedetection.io")

            # For each watch, get recent changes
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

            for watch in watches:
                try:
                    events = await self._fetch_changes_for_watch(watch, cutoff_date)

                    for event in events:
                        # Skip if already processed (by hash pair)
                        hash_key = f"{event.previous_hash}:{event.current_hash}"
                        if hash_key in self._processed_hashes:
                            continue
                        self._processed_hashes.add(hash_key)

                        # Skip old changes
                        if event.age_days > self.lookback_days:
                            continue

                        # Skip insignificant changes if filtering enabled
                        if self.min_significance and not event.is_significant:
                            continue

                        # Convert to signal
                        signal = self._event_to_signal(event)
                        signals.append(signal)

                except Exception as e:
                    logger.warning(f"Error fetching changes for watch {watch.uuid}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error collecting from changedetection.io: {e}")
            raise

        logger.info(f"Collected {len(signals)} change signals")
        return signals

    async def _fetch_watches(self) -> List[WatchConfig]:
        """
        Fetch all watches from changedetection.io.

        Returns:
            List of WatchConfig objects
        """
        headers = self._get_headers()
        url = f"{self.base_url}/api/v1/watch"

        async def fetch():
            if self.http:
                response = await self.http._client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()

        data = await self._fetch_with_retry(fetch)

        watches = []
        for uuid, watch_data in data.items():
            watches.append(WatchConfig.from_api_response(uuid, watch_data))

        return watches

    async def _fetch_changes_for_watch(
        self,
        watch: WatchConfig,
        cutoff_date: datetime,
    ) -> List[ChangeEvent]:
        """
        Fetch change history for a specific watch.

        Args:
            watch: WatchConfig to fetch changes for
            cutoff_date: Only return changes after this date

        Returns:
            List of ChangeEvent objects
        """
        headers = self._get_headers()
        url = f"{self.base_url}/api/v1/watch/{watch.uuid}/history"

        async def fetch():
            if self.http:
                response = await self.http._client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()

        try:
            data = await self._fetch_with_retry(fetch)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []  # No history yet
            raise

        events = []
        history = data.get("history", data) if isinstance(data, dict) else data

        # Handle both list and dict formats
        if isinstance(history, dict):
            history_items = [{"timestamp": k, **v} for k, v in history.items()]
        else:
            history_items = history

        for entry in history_items:
            event = ChangeEvent.from_api_response(watch, entry)

            # Skip changes before cutoff
            if event.change_detected_at < cutoff_date:
                continue

            events.append(event)

        return events

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for API requests."""
        headers = {
            "Accept": "application/json",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _calculate_confidence(self, event: ChangeEvent) -> float:
        """
        Calculate confidence score for a change event.

        Args:
            event: ChangeEvent to score

        Returns:
            Confidence score (0.0 to 0.95)
        """
        # Base confidence from page type
        base = CHANGE_TYPE_CONFIDENCE.get(event.page_type, 0.45)

        # Boost for large changes
        if event.total_lines_changed > 50:
            base += 0.10
        elif event.total_lines_changed > 20:
            base += 0.05

        # Freshness boost
        if event.age_days <= 1:
            base += 0.05
        elif event.age_days > 7:
            base -= 0.05

        # Canonical key boost (we know the company)
        if event.canonical_key:
            base += 0.05

        return min(max(base, 0.0), 0.95)

    def _classify_signal_type(self, event: ChangeEvent) -> str:
        """
        Classify the signal type based on page type.

        Args:
            event: ChangeEvent to classify

        Returns:
            Signal type string
        """
        return PAGE_TYPE_SIGNALS.get(event.page_type, "website_update")

    def _generate_why_now(self, event: ChangeEvent) -> str:
        """
        Generate a 'why now' explanation for the change.

        Args:
            event: ChangeEvent to explain

        Returns:
            Human-readable explanation
        """
        page_type_explanations = {
            "pricing": f"Pricing page changed: {event.diff_summary or 'pricing strategy may be shifting'}",
            "careers": f"Careers page updated: {event.diff_summary or 'hiring activity detected'}",
            "jobs": f"Jobs page updated: {event.diff_summary or 'new positions may be open'}",
            "terms": f"Terms/legal updated: {event.diff_summary or 'policy changes detected'}",
            "privacy": f"Privacy policy changed: {event.diff_summary or 'data practices may have changed'}",
            "product": f"Product page updated: {event.diff_summary or 'product evolution signal'}",
            "features": f"Features page changed: {event.diff_summary or 'new capabilities added'}",
            "landing": f"Website updated: {event.diff_summary or 'positioning may have changed'}",
        }

        return page_type_explanations.get(
            event.page_type,
            f"Website change detected: {event.diff_summary or 'content updated'}"
        )

    def _event_to_signal(self, event: ChangeEvent) -> Signal:
        """
        Convert a ChangeEvent to a Signal object.

        Args:
            event: ChangeEvent to convert

        Returns:
            Signal object
        """
        signal_type = self._classify_signal_type(event)
        confidence = self._calculate_confidence(event)
        why_now = self._generate_why_now(event)

        # Generate unique signal ID
        hash_input = f"{event.watch_uuid}:{event.current_hash}:{event.change_detected_at.isoformat()}"
        signal_hash = hashlib.md5(hash_input.encode()).hexdigest()[:12]
        signal_id = f"cd_{signal_hash}"

        # Build canonical key candidates
        canonical_keys = []
        if event.canonical_key:
            canonical_keys.append(event.canonical_key)

        return Signal(
            id=signal_id,
            signal_type=signal_type,
            confidence=confidence,
            source_api="changedetection",
            source_url=event.watch_url,
            detected_at=event.change_detected_at,
            raw_data={
                "watch_uuid": event.watch_uuid,
                "watch_url": event.watch_url,
                "watch_title": event.watch_title,
                "page_type": event.page_type,
                "company_name": event.company_name,
                "canonical_key": event.canonical_key,
                "canonical_key_candidates": canonical_keys,
                "change_detected_at": event.change_detected_at.isoformat(),
                "previous_hash": event.previous_hash,
                "current_hash": event.current_hash,
                "diff_summary": event.diff_summary,
                "diff_lines_added": event.diff_lines_added,
                "diff_lines_removed": event.diff_lines_removed,
                "change_type": event.change_type,
                "snapshot_url": event.snapshot_url,
                "is_significant": event.is_significant,
                "why_now": why_now,
            },
        )


# =============================================================================
# MOCK COLLECTOR FOR TESTING
# =============================================================================

class MockChangeDetectionCollector(ChangeDetectionCollector):
    """
    Mock changedetection.io collector for testing without API calls.

    Returns sample change events for consumer startups.
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        lookback_days: int = 7,
        min_significance: bool = False,
    ):
        # Skip parent __init__ URL validation for mock
        BaseCollector.__init__(
            self,
            store=store,
            collector_name="changedetection",
            retry_config=RetryConfig(max_retries=2, backoff_base=1.0),
            api_name="changedetection",
        )
        self.base_url = "https://mock.changedetection.local"
        self.api_key = "mock-key"
        self.lookback_days = lookback_days
        self.min_significance = min_significance
        self._processed_hashes: set[str] = set()

    async def _collect_signals(self) -> List[Signal]:
        """Return mock signals for testing."""
        now = datetime.now(timezone.utc)

        mock_events = [
            # Pricing change - high signal
            ChangeEvent(
                watch_uuid="watch-pricing-1",
                watch_url="https://wellnessapp.com/pricing",
                watch_title="WellnessApp Pricing",
                page_type="pricing",
                company_name="WellnessApp",
                canonical_key="domain:wellnessapp.com",
                change_detected_at=now - timedelta(hours=6),
                previous_hash="abc123",
                current_hash="def456",
                diff_summary="Monthly price changed from $9.99 to $14.99",
                diff_lines_added=3,
                diff_lines_removed=2,
                change_type="content",
            ),
            # Careers change - hiring signal
            ChangeEvent(
                watch_uuid="watch-careers-1",
                watch_url="https://mealbox.io/careers",
                watch_title="MealBox Careers",
                page_type="careers",
                company_name="MealBox",
                canonical_key="domain:mealbox.io",
                change_detected_at=now - timedelta(days=1),
                previous_hash="111aaa",
                current_hash="222bbb",
                diff_summary="Added 5 new engineering positions",
                diff_lines_added=45,
                diff_lines_removed=5,
                change_type="content",
            ),
            # Terms change - policy signal
            ChangeEvent(
                watch_uuid="watch-terms-1",
                watch_url="https://fittrack.app/privacy",
                watch_title="FitTrack Privacy Policy",
                page_type="privacy",
                company_name="FitTrack",
                canonical_key="domain:fittrack.app",
                change_detected_at=now - timedelta(days=2),
                previous_hash="333ccc",
                current_hash="444ddd",
                diff_summary="Updated data retention policy section",
                diff_lines_added=12,
                diff_lines_removed=8,
                change_type="content",
            ),
            # Old change - should be filtered
            ChangeEvent(
                watch_uuid="watch-old-1",
                watch_url="https://oldstartup.com/pricing",
                watch_title="OldStartup Pricing",
                page_type="pricing",
                company_name="OldStartup",
                canonical_key="domain:oldstartup.com",
                change_detected_at=now - timedelta(days=30),  # Too old
                previous_hash="old111",
                current_hash="old222",
                diff_summary="Old change",
                diff_lines_added=5,
                diff_lines_removed=3,
                change_type="content",
            ),
            # Small general change - insignificant if filtering enabled
            ChangeEvent(
                watch_uuid="watch-general-1",
                watch_url="https://randomsite.com/about",
                watch_title="Random Site About",
                page_type="general",
                company_name="RandomSite",
                canonical_key="domain:randomsite.com",
                change_detected_at=now - timedelta(hours=12),
                previous_hash="small111",
                current_hash="small222",
                diff_summary="Minor text tweak",
                diff_lines_added=1,
                diff_lines_removed=1,
                change_type="content",
            ),
        ]

        signals = []
        for event in mock_events:
            # Skip old changes
            if event.age_days > self.lookback_days:
                continue

            # Skip insignificant if filtering enabled
            if self.min_significance and not event.is_significant:
                continue

            signal = self._event_to_signal(event)
            signals.append(signal)

        return signals


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

if __name__ == "__main__":
    import asyncio

    async def main():
        # Use mock collector for demo
        collector = MockChangeDetectionCollector()
        result = await collector.run(dry_run=True)

        print("=" * 50)
        print("CHANGEDETECTION.IO COLLECTOR RESULTS")
        print("=" * 50)
        print(f"Status: {result.status.value}")
        print(f"Signals found: {result.signals_found}")
        print(f"New signals: {result.signals_new}")
        print(f"Suppressed: {result.signals_suppressed}")

    asyncio.run(main())
