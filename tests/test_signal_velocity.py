"""
Tests for SignalVelocityTracker - momentum and convergence detection.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass

from utils.signal_velocity import (
    SignalVelocityTracker,
    VelocityConfig,
    VelocityMetrics,
    SignalBurst,
    calculate_velocity_boost,
)


@dataclass
class MockSignal:
    """Mock signal for testing."""
    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    detected_at: datetime


class MockSignalStore:
    """Mock SignalStore for testing."""

    def __init__(self, signals=None):
        self.signals = signals or []

    async def get_signals_for_company(self, canonical_key):
        return [s for s in self.signals if s.canonical_key == canonical_key]

    async def get_pending_signals(self, limit=None):
        return self.signals[:limit] if limit else self.signals


class TestVelocityMetrics:
    """Tests for VelocityMetrics data class."""

    def test_confidence_boost_burst(self):
        """Test confidence boost from signal burst."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            has_recent_burst=True,
        )
        assert metrics.confidence_boost >= 0.1

    def test_confidence_boost_convergence(self):
        """Test confidence boost from type convergence."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            has_type_convergence=True,
        )
        assert metrics.confidence_boost >= 0.15

    def test_confidence_boost_sources(self):
        """Test confidence boost from source convergence."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            has_source_convergence=True,
        )
        assert metrics.confidence_boost >= 0.1

    def test_confidence_boost_acceleration(self):
        """Test confidence boost from acceleration."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            is_accelerating=True,
        )
        assert metrics.confidence_boost >= 0.05

    def test_confidence_boost_capped(self):
        """Test that total boost is capped at 0.35."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            has_recent_burst=True,
            has_type_convergence=True,
            has_source_convergence=True,
            is_accelerating=True,
        )
        assert metrics.confidence_boost == 0.35

    def test_momentum_score_empty(self):
        """Test momentum score with no signals."""
        metrics = VelocityMetrics(canonical_key="domain:test.io")
        assert metrics.momentum_score == 0.0

    def test_momentum_score_high_activity(self):
        """Test momentum score with high activity."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            signals_48h=3,
            unique_signal_types={"github_spike", "hiring", "incorporation"},
            unique_sources={"github", "companies_house", "job_boards"},
            is_accelerating=True,
        )
        assert metrics.momentum_score >= 0.8

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = VelocityMetrics(
            canonical_key="domain:test.io",
            total_signals=10,
            signals_48h=3,
        )
        d = metrics.to_dict()
        assert d["canonical_key"] == "domain:test.io"
        assert d["total_signals"] == 10
        assert d["signals_48h"] == 3
        assert "confidence_boost" in d
        assert "momentum_score" in d


class TestSignalBurst:
    """Tests for SignalBurst data class."""

    def test_is_significant_by_count(self):
        """Test burst significance by signal count."""
        burst = SignalBurst(
            canonical_key="domain:test.io",
            signal_count=3,
            unique_types={"github_spike"},
            unique_sources={"github"},
            window_hours=48,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
        )
        assert burst.is_significant is True

    def test_is_significant_by_types(self):
        """Test burst significance by type diversity."""
        burst = SignalBurst(
            canonical_key="domain:test.io",
            signal_count=2,
            unique_types={"github_spike", "hiring"},
            unique_sources={"github"},
            window_hours=48,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
        )
        assert burst.is_significant is True

    def test_not_significant(self):
        """Test non-significant burst."""
        burst = SignalBurst(
            canonical_key="domain:test.io",
            signal_count=1,
            unique_types={"github_spike"},
            unique_sources={"github"},
            window_hours=48,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
        )
        assert burst.is_significant is False


class TestSignalVelocityTracker:
    """Tests for SignalVelocityTracker."""

    @pytest.mark.asyncio
    async def test_get_velocity_no_signals(self):
        """Test velocity calculation with no signals."""
        store = MockSignalStore()
        tracker = SignalVelocityTracker(store)

        metrics = await tracker.get_velocity("domain:empty.io")
        assert metrics.total_signals == 0
        assert metrics.confidence_boost == 0.0
        assert metrics.momentum_score == 0.0

    @pytest.mark.asyncio
    async def test_get_velocity_single_signal(self):
        """Test velocity with a single signal."""
        now = datetime.now(timezone.utc)
        signals = [
            MockSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:single.io",
                detected_at=now,
            )
        ]
        store = MockSignalStore(signals)
        tracker = SignalVelocityTracker(store)

        metrics = await tracker.get_velocity("domain:single.io")
        assert metrics.total_signals == 1
        assert metrics.signals_24h == 1
        assert len(metrics.unique_signal_types) == 1

    @pytest.mark.asyncio
    async def test_get_velocity_burst_detection(self):
        """Test burst detection with multiple signals in short window."""
        now = datetime.now(timezone.utc)
        signals = [
            MockSignal(
                id=i,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:burst.io",
                detected_at=now - timedelta(hours=i),
            )
            for i in range(5)
        ]
        store = MockSignalStore(signals)
        tracker = SignalVelocityTracker(store)

        metrics = await tracker.get_velocity("domain:burst.io")
        assert metrics.has_recent_burst is True
        assert len(metrics.bursts) >= 1
        assert metrics.confidence_boost > 0

    @pytest.mark.asyncio
    async def test_get_velocity_type_convergence(self):
        """Test type convergence detection."""
        now = datetime.now(timezone.utc)
        signal_types = ["github_spike", "hiring_signal", "incorporation", "domain_registration"]
        signals = [
            MockSignal(
                id=i,
                signal_type=signal_types[i],
                source_api="api",
                canonical_key="domain:converge.io",
                detected_at=now - timedelta(days=i),
            )
            for i in range(4)
        ]
        store = MockSignalStore(signals)
        tracker = SignalVelocityTracker(store)

        metrics = await tracker.get_velocity("domain:converge.io")
        assert len(metrics.unique_signal_types) == 4
        assert metrics.has_type_convergence is True

    @pytest.mark.asyncio
    async def test_get_velocity_source_convergence(self):
        """Test source convergence detection."""
        now = datetime.now(timezone.utc)
        sources = ["github", "companies_house", "job_boards"]
        signals = [
            MockSignal(
                id=i,
                signal_type="signal",
                source_api=sources[i],
                canonical_key="domain:multisource.io",
                detected_at=now - timedelta(days=i),
            )
            for i in range(3)
        ]
        store = MockSignalStore(signals)
        tracker = SignalVelocityTracker(store)

        metrics = await tracker.get_velocity("domain:multisource.io")
        assert len(metrics.unique_sources) == 3
        assert metrics.has_source_convergence is True

    @pytest.mark.asyncio
    async def test_get_velocity_acceleration(self):
        """Test acceleration detection."""
        now = datetime.now(timezone.utc)
        # More signals in recent 7 days than older period
        signals = []
        # 5 signals in last 3 days
        for i in range(5):
            signals.append(
                MockSignal(
                    id=i,
                    signal_type="signal",
                    source_api="api",
                    canonical_key="domain:accel.io",
                    detected_at=now - timedelta(days=i),
                )
            )
        # 2 signals in days 20-30
        for i in range(2):
            signals.append(
                MockSignal(
                    id=10 + i,
                    signal_type="signal",
                    source_api="api",
                    canonical_key="domain:accel.io",
                    detected_at=now - timedelta(days=20 + i),
                )
            )

        store = MockSignalStore(signals)
        config = VelocityConfig(acceleration_threshold=1.2)
        tracker = SignalVelocityTracker(store, config)

        metrics = await tracker.get_velocity("domain:accel.io")
        assert metrics.is_accelerating is True

    @pytest.mark.asyncio
    async def test_get_batch_velocity(self):
        """Test batch velocity calculation."""
        now = datetime.now(timezone.utc)
        signals = [
            MockSignal(
                id=1,
                signal_type="signal",
                source_api="api",
                canonical_key="domain:a.io",
                detected_at=now,
            ),
            MockSignal(
                id=2,
                signal_type="signal",
                source_api="api",
                canonical_key="domain:b.io",
                detected_at=now,
            ),
        ]
        store = MockSignalStore(signals)
        tracker = SignalVelocityTracker(store)

        results = await tracker.get_batch_velocity(["domain:a.io", "domain:b.io"])
        assert len(results) == 2
        assert "domain:a.io" in results
        assert "domain:b.io" in results


class TestCalculateVelocityBoost:
    """Tests for quick velocity boost calculation helper."""

    def test_boost_burst(self):
        """Test boost for signal burst."""
        boost = calculate_velocity_boost(
            signals_48h=3,
            unique_types=1,
            unique_sources=1,
        )
        assert boost >= 0.1

    def test_boost_type_convergence(self):
        """Test boost for type convergence."""
        boost = calculate_velocity_boost(
            signals_48h=0,
            unique_types=3,
            unique_sources=1,
        )
        assert boost >= 0.15

    def test_boost_source_convergence(self):
        """Test boost for source convergence."""
        boost = calculate_velocity_boost(
            signals_48h=0,
            unique_types=1,
            unique_sources=2,
        )
        assert boost >= 0.1

    def test_boost_acceleration(self):
        """Test boost for acceleration."""
        boost = calculate_velocity_boost(
            signals_48h=0,
            unique_types=1,
            unique_sources=1,
            is_accelerating=True,
        )
        assert boost >= 0.05

    def test_boost_combined(self):
        """Test combined boost."""
        boost = calculate_velocity_boost(
            signals_48h=3,
            unique_types=4,
            unique_sources=3,
            is_accelerating=True,
        )
        # All bonuses active
        assert boost == 0.35  # Capped

    def test_boost_minimal(self):
        """Test minimal boost."""
        boost = calculate_velocity_boost(
            signals_48h=0,
            unique_types=1,
            unique_sources=1,
            is_accelerating=False,
        )
        assert boost == 0.0


class TestVelocityConfig:
    """Tests for VelocityConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = VelocityConfig()
        assert config.burst_window_hours == 48
        assert config.convergence_window_days == 7
        assert config.burst_signal_threshold == 2
        assert config.convergence_type_threshold == 3
        assert config.acceleration_threshold == 1.5

    def test_custom_values(self):
        """Test custom configuration values."""
        config = VelocityConfig(
            burst_window_hours=24,
            convergence_type_threshold=2,
        )
        assert config.burst_window_hours == 24
        assert config.convergence_type_threshold == 2
