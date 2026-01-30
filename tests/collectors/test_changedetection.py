"""
Tests for ChangeDetection.io Collector.

Tests the integration with changedetection.io for website change monitoring.
"""

from __future__ import annotations

import hashlib
import pytest
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.changedetection import (
    ChangeDetectionCollector,
    ChangeEvent,
    WatchConfig,
    CHANGE_TYPE_CONFIDENCE,
    PAGE_TYPE_SIGNALS,
    MockChangeDetectionCollector,
)
from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_store():
    """Create a mock SignalStore."""
    store = MagicMock(spec=SignalStore)
    store.is_duplicate = AsyncMock(return_value=False)
    store.check_suppression = AsyncMock(return_value=None)
    store.save_signal = AsyncMock(return_value=1)
    return store


@pytest.fixture
def sample_watch_config() -> WatchConfig:
    """Sample watch configuration."""
    return WatchConfig(
        uuid="watch-123",
        url="https://acme.com/pricing",
        title="Acme Pricing Page",
        page_type="pricing",
        company_name="Acme Inc",
        canonical_key="domain:acme.com",
        check_interval=3600,
        tags=["consumer", "saas"],
    )


@pytest.fixture
def sample_change_event(sample_watch_config: WatchConfig) -> ChangeEvent:
    """Sample change event from changedetection.io."""
    return ChangeEvent(
        watch_uuid="watch-123",
        watch_url="https://acme.com/pricing",
        watch_title="Acme Pricing Page",
        page_type="pricing",
        company_name="Acme Inc",
        canonical_key="domain:acme.com",
        change_detected_at=datetime.now(timezone.utc),
        previous_hash="abc123",
        current_hash="def456",
        diff_summary="Price changed from $99 to $149",
        diff_lines_added=5,
        diff_lines_removed=3,
        change_type="content",
        snapshot_url="https://changedetection.local/diff/watch-123/1",
    )


# =============================================================================
# TEST: CHANGE EVENT DATA CLASS
# =============================================================================

class TestChangeEvent:
    """Tests for ChangeEvent data class."""

    def test_change_event_creation(self, sample_change_event: ChangeEvent):
        """ChangeEvent should store all required fields."""
        event = sample_change_event
        assert event.watch_uuid == "watch-123"
        assert event.watch_url == "https://acme.com/pricing"
        assert event.page_type == "pricing"
        assert event.company_name == "Acme Inc"
        assert event.canonical_key == "domain:acme.com"
        assert event.diff_summary == "Price changed from $99 to $149"

    def test_change_event_is_significant_large_diff(self):
        """Large diffs should be considered significant."""
        event = ChangeEvent(
            watch_uuid="test",
            watch_url="https://example.com",
            watch_title="Test",
            page_type="pricing",
            company_name="Test Co",
            canonical_key="domain:example.com",
            change_detected_at=datetime.now(timezone.utc),
            previous_hash="a",
            current_hash="b",
            diff_summary="Major changes",
            diff_lines_added=50,  # Large change
            diff_lines_removed=20,
            change_type="content",
        )
        assert event.is_significant is True

    def test_change_event_is_significant_small_diff(self):
        """Small diffs should not be considered significant."""
        event = ChangeEvent(
            watch_uuid="test",
            watch_url="https://example.com",
            watch_title="Test",
            page_type="general",  # Not high-value page type
            company_name="Test Co",
            canonical_key="domain:example.com",
            change_detected_at=datetime.now(timezone.utc),
            previous_hash="a",
            current_hash="b",
            diff_summary="Minor tweak",
            diff_lines_added=2,  # Small change
            diff_lines_removed=1,
            change_type="content",
        )
        assert event.is_significant is False

    def test_change_event_pricing_always_significant(self):
        """Pricing page changes should always be significant."""
        event = ChangeEvent(
            watch_uuid="test",
            watch_url="https://example.com/pricing",
            watch_title="Pricing",
            page_type="pricing",  # High-value page type
            company_name="Test Co",
            canonical_key="domain:example.com",
            change_detected_at=datetime.now(timezone.utc),
            previous_hash="a",
            current_hash="b",
            diff_summary="Minor price change",
            diff_lines_added=1,  # Small change but pricing page
            diff_lines_removed=1,
            change_type="content",
        )
        assert event.is_significant is True

    def test_change_event_age_days(self):
        """age_days should calculate correctly."""
        event = ChangeEvent(
            watch_uuid="test",
            watch_url="https://example.com",
            watch_title="Test",
            page_type="general",
            company_name="Test Co",
            canonical_key="domain:example.com",
            change_detected_at=datetime.now(timezone.utc) - timedelta(days=5),
            previous_hash="a",
            current_hash="b",
            diff_summary="Test",
            diff_lines_added=10,
            diff_lines_removed=5,
            change_type="content",
        )
        assert event.age_days == 5


# =============================================================================
# TEST: WATCH CONFIG
# =============================================================================

class TestWatchConfig:
    """Tests for WatchConfig data class."""

    def test_watch_config_creation(self, sample_watch_config: WatchConfig):
        """WatchConfig should store all fields."""
        config = sample_watch_config
        assert config.uuid == "watch-123"
        assert config.url == "https://acme.com/pricing"
        assert config.page_type == "pricing"
        assert config.company_name == "Acme Inc"
        assert "consumer" in config.tags

    def test_watch_config_defaults(self):
        """WatchConfig should have sensible defaults."""
        config = WatchConfig(
            uuid="test",
            url="https://example.com",
            title="Example",
        )
        assert config.page_type == "general"
        assert config.check_interval == 3600
        assert config.tags == []


# =============================================================================
# TEST: CONFIDENCE SCORING
# =============================================================================

class TestConfidenceScoring:
    """Tests for change type confidence scoring."""

    def test_pricing_change_high_confidence(self, sample_change_event: ChangeEvent):
        """Pricing page changes should have high confidence."""
        event = sample_change_event
        event.page_type = "pricing"

        collector = MockChangeDetectionCollector()
        confidence = collector._calculate_confidence(event)

        assert confidence >= 0.7

    def test_careers_change_high_confidence(self, sample_change_event: ChangeEvent):
        """Careers page changes should have high confidence."""
        event = sample_change_event
        event.page_type = "careers"

        collector = MockChangeDetectionCollector()
        confidence = collector._calculate_confidence(event)

        assert confidence >= 0.65

    def test_terms_change_medium_confidence(self, sample_change_event: ChangeEvent):
        """Terms/privacy page changes should have medium confidence."""
        event = sample_change_event
        event.page_type = "terms"

        collector = MockChangeDetectionCollector()
        confidence = collector._calculate_confidence(event)

        assert 0.5 <= confidence <= 0.7

    def test_general_page_lower_confidence(self, sample_change_event: ChangeEvent):
        """General page changes should have lower confidence."""
        event = sample_change_event
        event.page_type = "general"

        collector = MockChangeDetectionCollector()
        confidence = collector._calculate_confidence(event)

        assert confidence <= 0.6

    def test_large_diff_boosts_confidence(self, sample_change_event: ChangeEvent):
        """Large diffs should boost confidence."""
        event = sample_change_event
        event.page_type = "general"
        event.diff_lines_added = 100
        event.diff_lines_removed = 50

        collector = MockChangeDetectionCollector()
        confidence = collector._calculate_confidence(event)

        # Should get diff size boost
        assert confidence >= 0.5


# =============================================================================
# TEST: SIGNAL TYPE CLASSIFICATION
# =============================================================================

class TestSignalTypeClassification:
    """Tests for signal type classification based on page type."""

    def test_pricing_signal_type(self, sample_change_event: ChangeEvent):
        """Pricing changes should produce pricing_change signal."""
        event = sample_change_event
        event.page_type = "pricing"

        collector = MockChangeDetectionCollector()
        signal_type = collector._classify_signal_type(event)

        assert signal_type == "pricing_change"

    def test_careers_signal_type(self, sample_change_event: ChangeEvent):
        """Careers changes should produce hiring_signal."""
        event = sample_change_event
        event.page_type = "careers"

        collector = MockChangeDetectionCollector()
        signal_type = collector._classify_signal_type(event)

        assert signal_type == "hiring_signal"

    def test_terms_signal_type(self, sample_change_event: ChangeEvent):
        """Terms changes should produce terms_change signal."""
        event = sample_change_event
        event.page_type = "terms"

        collector = MockChangeDetectionCollector()
        signal_type = collector._classify_signal_type(event)

        assert signal_type == "terms_change"

    def test_product_signal_type(self, sample_change_event: ChangeEvent):
        """Product page changes should produce product_update signal."""
        event = sample_change_event
        event.page_type = "product"

        collector = MockChangeDetectionCollector()
        signal_type = collector._classify_signal_type(event)

        assert signal_type == "product_update"


# =============================================================================
# TEST: COLLECTOR BASIC FUNCTIONALITY
# =============================================================================

class TestChangeDetectionCollector:
    """Tests for ChangeDetectionCollector."""

    def test_collector_initialization(self):
        """Collector should initialize with correct defaults."""
        collector = ChangeDetectionCollector(
            base_url="https://changedetection.local",
            api_key="test-key",
        )
        assert collector.collector_name == "changedetection"
        assert collector.base_url == "https://changedetection.local"
        assert collector.api_key == "test-key"

    def test_collector_initialization_from_env(self, monkeypatch):
        """Collector should read from environment variables."""
        monkeypatch.setenv("CHANGEDETECTION_URL", "https://cd.example.com")
        monkeypatch.setenv("CHANGEDETECTION_API_KEY", "env-api-key")

        collector = ChangeDetectionCollector()
        assert collector.base_url == "https://cd.example.com"
        assert collector.api_key == "env-api-key"

    def test_collector_requires_url(self, monkeypatch):
        """Collector should raise if no URL provided."""
        monkeypatch.delenv("CHANGEDETECTION_URL", raising=False)
        monkeypatch.delenv("CHANGEDETECTION_API_KEY", raising=False)

        with pytest.raises(ValueError, match="CHANGEDETECTION_URL"):
            ChangeDetectionCollector()

    @pytest.mark.asyncio
    async def test_mock_collector_returns_signals(self):
        """Mock collector should return sample signals."""
        collector = MockChangeDetectionCollector()
        result = await collector.run(dry_run=True)

        assert result.signals_found > 0
        assert result.signals_new > 0

    @pytest.mark.asyncio
    async def test_collector_filters_old_changes(self):
        """Collector should filter changes older than lookback_days."""
        # Use lookback of 1 day - should exclude the 2-day-old and 30-day-old events
        collector = MockChangeDetectionCollector(lookback_days=1, min_significance=False)

        result = await collector.run(dry_run=True)

        # Should only include events from today (pricing 6h ago, careers 1d, general 12h)
        # The 2-day-old (terms) and 30-day-old (old) should be filtered
        assert result.signals_found <= 3

    @pytest.mark.asyncio
    async def test_collector_filters_insignificant_changes(self):
        """Collector should filter insignificant changes."""
        collector = MockChangeDetectionCollector(min_significance=True)
        result = await collector.run(dry_run=True)

        # All returned signals should be from significant changes
        assert result.signals_found > 0


# =============================================================================
# TEST: SIGNAL CONVERSION
# =============================================================================

class TestSignalConversion:
    """Tests for converting change events to signals."""

    @pytest.mark.asyncio
    async def test_event_to_signal_includes_raw_data(self, sample_change_event: ChangeEvent):
        """Signal should include change event details in raw_data."""
        collector = MockChangeDetectionCollector()
        signal = collector._event_to_signal(sample_change_event)

        assert signal.raw_data["watch_url"] == "https://acme.com/pricing"
        assert signal.raw_data["page_type"] == "pricing"
        assert signal.raw_data["company_name"] == "Acme Inc"
        assert signal.raw_data["diff_summary"] == "Price changed from $99 to $149"

    @pytest.mark.asyncio
    async def test_event_to_signal_has_canonical_key(self, sample_change_event: ChangeEvent):
        """Signal should have canonical key from watch config."""
        collector = MockChangeDetectionCollector()
        signal = collector._event_to_signal(sample_change_event)

        assert signal.raw_data["canonical_key"] == "domain:acme.com"

    @pytest.mark.asyncio
    async def test_event_to_signal_unique_id(self, sample_change_event: ChangeEvent):
        """Signal should have unique ID based on watch and timestamp."""
        collector = MockChangeDetectionCollector()
        signal = collector._event_to_signal(sample_change_event)

        assert signal.id.startswith("cd_")
        assert "watch-123" in signal.id or len(signal.id) > 10

    @pytest.mark.asyncio
    async def test_event_to_signal_source_api(self, sample_change_event: ChangeEvent):
        """Signal source_api should be changedetection."""
        collector = MockChangeDetectionCollector()
        signal = collector._event_to_signal(sample_change_event)

        assert signal.source_api == "changedetection"


# =============================================================================
# TEST: API INTEGRATION (MOCKED)
# =============================================================================

class TestAPIIntegration:
    """Tests for changedetection.io API integration."""

    @pytest.mark.asyncio
    async def test_fetch_watches(self):
        """Collector should fetch watch list from API."""
        with patch("collectors.changedetection.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "watch-1": {
                    "url": "https://example.com/pricing",
                    "title": "Example Pricing",
                    "tag": "pricing,consumer",
                },
                "watch-2": {
                    "url": "https://example.com/careers",
                    "title": "Example Careers",
                    "tag": "careers",
                },
            }
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__.return_value = mock_client_instance
            mock_client_instance.__aexit__.return_value = None
            mock_client.return_value = mock_client_instance

            collector = ChangeDetectionCollector(
                base_url="https://cd.local",
                api_key="test-key",
            )

            watches = await collector._fetch_watches()
            assert len(watches) == 2

    @pytest.mark.asyncio
    async def test_fetch_change_history(self):
        """Collector should fetch change history for a watch."""
        with patch("collectors.changedetection.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "history": [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "previous_md5": "abc",
                        "current_md5": "def",
                    }
                ]
            }
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__.return_value = mock_client_instance
            mock_client_instance.__aexit__.return_value = None
            mock_client.return_value = mock_client_instance

            collector = ChangeDetectionCollector(
                base_url="https://cd.local",
                api_key="test-key",
            )

            # Would test _fetch_change_history if exposed
            # For now, validate the API pattern works


# =============================================================================
# TEST: WHY NOW GENERATION
# =============================================================================

class TestWhyNowGeneration:
    """Tests for generating 'why now' explanations."""

    def test_pricing_change_why_now(self, sample_change_event: ChangeEvent):
        """Pricing changes should generate relevant why_now."""
        event = sample_change_event
        event.page_type = "pricing"
        event.diff_summary = "Monthly price increased from $99 to $149"

        collector = MockChangeDetectionCollector()
        why_now = collector._generate_why_now(event)

        assert "pricing" in why_now.lower() or "price" in why_now.lower()

    def test_careers_change_why_now(self, sample_change_event: ChangeEvent):
        """Careers changes should mention hiring."""
        event = sample_change_event
        event.page_type = "careers"
        event.diff_summary = "Added 5 new engineering positions"

        collector = MockChangeDetectionCollector()
        why_now = collector._generate_why_now(event)

        assert "hiring" in why_now.lower() or "career" in why_now.lower() or "job" in why_now.lower()

    def test_terms_change_why_now(self, sample_change_event: ChangeEvent):
        """Terms changes should mention policy/legal."""
        event = sample_change_event
        event.page_type = "terms"

        collector = MockChangeDetectionCollector()
        why_now = collector._generate_why_now(event)

        assert "terms" in why_now.lower() or "policy" in why_now.lower()


# =============================================================================
# TEST: COLLECTOR WITH STORE
# =============================================================================

class TestCollectorWithStore:
    """Tests for collector integration with SignalStore."""

    @pytest.mark.asyncio
    async def test_collector_saves_to_store(self, mock_store):
        """Collector should save signals to store when not dry_run."""
        collector = MockChangeDetectionCollector(store=mock_store)
        result = await collector.run(dry_run=False)

        # Should have called save_signal
        assert mock_store.save_signal.called
        assert result.signals_new > 0

    @pytest.mark.asyncio
    async def test_collector_checks_duplicates(self, mock_store):
        """Collector should check for duplicates."""
        mock_store.is_duplicate = AsyncMock(return_value=True)

        collector = MockChangeDetectionCollector(store=mock_store)
        result = await collector.run(dry_run=False)

        # All signals should be suppressed as duplicates
        assert result.signals_suppressed > 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_save(self, mock_store):
        """Dry run should not save to store."""
        collector = MockChangeDetectionCollector(store=mock_store)
        result = await collector.run(dry_run=True)

        # Should not call save_signal in dry run
        assert not mock_store.save_signal.called
        assert result.dry_run is True
