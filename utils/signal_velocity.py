"""
Signal Velocity Tracker for Discovery Engine

Tracks when multiple signals converge in a short time window,
indicating momentum and higher confidence in the company.

Key concepts:
- Signal velocity = rate of new signals over time
- Convergence = multiple different signal types in short window
- Momentum = accelerating signal frequency

Scoring boosts:
- 2 signal types in 48hrs: +0.1 confidence
- 3+ signal types in 7 days: +0.15 confidence
- Accelerating velocity: +0.05 confidence

Usage:
    tracker = SignalVelocityTracker(store)

    # Check velocity for a company
    velocity = await tracker.get_velocity("domain:acme.ai")
    boost = velocity.confidence_boost
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore, StoredSignal

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class VelocityConfig:
    """Configuration for velocity tracking."""
    # Time windows
    burst_window_hours: int = 48  # Window for "burst" detection
    convergence_window_days: int = 7  # Window for convergence detection
    trend_window_days: int = 30  # Window for trend analysis

    # Thresholds
    burst_signal_threshold: int = 2  # Min signals for burst
    convergence_type_threshold: int = 3  # Min signal types for convergence
    acceleration_threshold: float = 1.5  # Velocity ratio for acceleration

    # Confidence boosts
    burst_boost: float = 0.1  # Boost for signal burst
    convergence_boost: float = 0.15  # Boost for type convergence
    acceleration_boost: float = 0.05  # Boost for accelerating velocity
    multi_source_boost: float = 0.1  # Boost for multiple unique sources


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SignalBurst:
    """A burst of signals in a short time window."""
    canonical_key: str
    signal_count: int
    unique_types: Set[str]
    unique_sources: Set[str]
    window_hours: int
    start_time: datetime
    end_time: datetime

    @property
    def is_significant(self) -> bool:
        """True if this burst is significant."""
        return self.signal_count >= 2 or len(self.unique_types) >= 2


@dataclass
class VelocityMetrics:
    """Velocity metrics for a company."""
    canonical_key: str

    # Signal counts
    total_signals: int = 0
    signals_24h: int = 0
    signals_48h: int = 0
    signals_7d: int = 0
    signals_30d: int = 0

    # Type diversity
    unique_signal_types: Set[str] = field(default_factory=set)
    unique_sources: Set[str] = field(default_factory=set)

    # Velocity measurements
    velocity_24h: float = 0.0  # Signals per hour in last 24h
    velocity_7d: float = 0.0  # Signals per day in last 7d
    velocity_30d: float = 0.0  # Signals per day in last 30d

    # Acceleration (change in velocity)
    acceleration: float = 0.0  # Ratio of recent to historical velocity
    is_accelerating: bool = False

    # Bursts
    bursts: List[SignalBurst] = field(default_factory=list)
    has_recent_burst: bool = False

    # Convergence
    has_type_convergence: bool = False  # 3+ types in 7 days
    has_source_convergence: bool = False  # 2+ sources

    # Timing
    first_signal_at: Optional[datetime] = None
    last_signal_at: Optional[datetime] = None
    calculated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def confidence_boost(self) -> float:
        """Calculate total confidence boost from velocity."""
        boost = 0.0

        # Burst boost
        if self.has_recent_burst:
            boost += 0.1

        # Type convergence boost
        if self.has_type_convergence:
            boost += 0.15

        # Source convergence boost
        if self.has_source_convergence:
            boost += 0.1

        # Acceleration boost
        if self.is_accelerating:
            boost += 0.05

        return min(boost, 0.35)  # Cap at 0.35

    @property
    def momentum_score(self) -> float:
        """
        Calculate momentum score (0-1).

        Combines velocity and acceleration into a single metric.
        """
        score = 0.0

        # Recent activity (last 48h)
        if self.signals_48h >= 3:
            score += 0.3
        elif self.signals_48h >= 2:
            score += 0.2
        elif self.signals_48h >= 1:
            score += 0.1

        # Type diversity
        type_count = len(self.unique_signal_types)
        if type_count >= 4:
            score += 0.3
        elif type_count >= 3:
            score += 0.2
        elif type_count >= 2:
            score += 0.1

        # Source diversity
        source_count = len(self.unique_sources)
        if source_count >= 3:
            score += 0.2
        elif source_count >= 2:
            score += 0.1

        # Acceleration bonus
        if self.is_accelerating:
            score += 0.2

        return min(score, 1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/display."""
        return {
            "canonical_key": self.canonical_key,
            "total_signals": self.total_signals,
            "signals_24h": self.signals_24h,
            "signals_48h": self.signals_48h,
            "signals_7d": self.signals_7d,
            "signals_30d": self.signals_30d,
            "unique_signal_types": list(self.unique_signal_types),
            "unique_sources": list(self.unique_sources),
            "velocity_24h": self.velocity_24h,
            "velocity_7d": self.velocity_7d,
            "velocity_30d": self.velocity_30d,
            "acceleration": self.acceleration,
            "is_accelerating": self.is_accelerating,
            "has_recent_burst": self.has_recent_burst,
            "has_type_convergence": self.has_type_convergence,
            "has_source_convergence": self.has_source_convergence,
            "confidence_boost": self.confidence_boost,
            "momentum_score": self.momentum_score,
            "first_signal_at": self.first_signal_at.isoformat() if self.first_signal_at else None,
            "last_signal_at": self.last_signal_at.isoformat() if self.last_signal_at else None,
            "calculated_at": self.calculated_at.isoformat(),
        }


# =============================================================================
# VELOCITY TRACKER
# =============================================================================

class SignalVelocityTracker:
    """
    Tracks signal velocity and convergence patterns.

    Velocity indicates momentum - companies with multiple signals
    from different sources in a short time are more likely to be
    actively building and worth investigating.
    """

    def __init__(
        self,
        store: SignalStore,
        config: Optional[VelocityConfig] = None,
    ):
        """
        Initialize velocity tracker.

        Args:
            store: SignalStore instance for querying signals
            config: Velocity configuration (uses defaults if not provided)
        """
        self.store = store
        self.config = config or VelocityConfig()

    async def get_velocity(
        self,
        canonical_key: str,
    ) -> VelocityMetrics:
        """
        Calculate velocity metrics for a company.

        Args:
            canonical_key: Company canonical key (e.g., "domain:acme.ai")

        Returns:
            VelocityMetrics with calculated values
        """
        # Get all signals for this company
        signals = await self.store.get_signals_for_company(canonical_key)

        if not signals:
            return VelocityMetrics(canonical_key=canonical_key)

        now = datetime.now(timezone.utc)
        metrics = VelocityMetrics(canonical_key=canonical_key)

        # Calculate time-based counts
        metrics.total_signals = len(signals)

        for sig in signals:
            age = now - sig.detected_at

            if age <= timedelta(hours=24):
                metrics.signals_24h += 1
            if age <= timedelta(hours=48):
                metrics.signals_48h += 1
            if age <= timedelta(days=7):
                metrics.signals_7d += 1
            if age <= timedelta(days=30):
                metrics.signals_30d += 1

            # Track unique types and sources
            metrics.unique_signal_types.add(sig.signal_type)
            metrics.unique_sources.add(sig.source_api)

        # Calculate velocities
        metrics.velocity_24h = metrics.signals_24h / 24.0 if metrics.signals_24h > 0 else 0
        metrics.velocity_7d = metrics.signals_7d / 7.0 if metrics.signals_7d > 0 else 0
        metrics.velocity_30d = metrics.signals_30d / 30.0 if metrics.signals_30d > 0 else 0

        # Calculate acceleration (compare recent to historical)
        if metrics.velocity_30d > 0:
            metrics.acceleration = metrics.velocity_7d / metrics.velocity_30d
            metrics.is_accelerating = metrics.acceleration >= self.config.acceleration_threshold
        else:
            metrics.acceleration = metrics.velocity_7d * 10 if metrics.velocity_7d > 0 else 0
            metrics.is_accelerating = metrics.signals_7d >= 2

        # Detect bursts
        metrics.bursts = self._detect_bursts(signals, now)
        metrics.has_recent_burst = any(
            burst.is_significant and
            (now - burst.end_time) <= timedelta(hours=self.config.burst_window_hours)
            for burst in metrics.bursts
        )

        # Check convergence
        recent_signals = [
            s for s in signals
            if (now - s.detected_at) <= timedelta(days=self.config.convergence_window_days)
        ]
        recent_types = set(s.signal_type for s in recent_signals)
        recent_sources = set(s.source_api for s in recent_signals)

        metrics.has_type_convergence = len(recent_types) >= self.config.convergence_type_threshold
        metrics.has_source_convergence = len(recent_sources) >= 2

        # Timing
        sorted_signals = sorted(signals, key=lambda s: s.detected_at)
        metrics.first_signal_at = sorted_signals[0].detected_at
        metrics.last_signal_at = sorted_signals[-1].detected_at

        return metrics

    def _detect_bursts(
        self,
        signals: List,  # List[StoredSignal]
        now: datetime,
    ) -> List[SignalBurst]:
        """
        Detect signal bursts in the signal history.

        A burst is 2+ signals within the burst window.
        """
        if len(signals) < 2:
            return []

        bursts = []
        window = timedelta(hours=self.config.burst_window_hours)

        # Sort by time
        sorted_signals = sorted(signals, key=lambda s: s.detected_at)

        # Sliding window to find bursts
        i = 0
        while i < len(sorted_signals):
            # Start a potential burst at this signal
            burst_signals = [sorted_signals[i]]

            # Add all signals within the window
            j = i + 1
            while j < len(sorted_signals):
                if sorted_signals[j].detected_at - sorted_signals[i].detected_at <= window:
                    burst_signals.append(sorted_signals[j])
                    j += 1
                else:
                    break

            # Check if this is a significant burst
            if len(burst_signals) >= self.config.burst_signal_threshold:
                burst = SignalBurst(
                    canonical_key=burst_signals[0].canonical_key,
                    signal_count=len(burst_signals),
                    unique_types=set(s.signal_type for s in burst_signals),
                    unique_sources=set(s.source_api for s in burst_signals),
                    window_hours=self.config.burst_window_hours,
                    start_time=burst_signals[0].detected_at,
                    end_time=burst_signals[-1].detected_at,
                )
                bursts.append(burst)

                # Skip past this burst
                i = j
            else:
                i += 1

        return bursts

    async def get_batch_velocity(
        self,
        canonical_keys: List[str],
    ) -> Dict[str, VelocityMetrics]:
        """
        Calculate velocity for multiple companies.

        Args:
            canonical_keys: List of canonical keys

        Returns:
            Dict mapping canonical_key to VelocityMetrics
        """
        results = {}

        for key in canonical_keys:
            try:
                results[key] = await self.get_velocity(key)
            except Exception as e:
                logger.error(f"Error calculating velocity for {key}: {e}")
                results[key] = VelocityMetrics(canonical_key=key)

        return results

    async def get_high_momentum_companies(
        self,
        min_momentum: float = 0.5,
        limit: int = 20,
    ) -> List[VelocityMetrics]:
        """
        Find companies with high momentum scores.

        Args:
            min_momentum: Minimum momentum score (0-1)
            limit: Maximum number of results

        Returns:
            List of VelocityMetrics sorted by momentum score
        """
        # Get all unique canonical keys from pending signals
        pending = await self.store.get_pending_signals(limit=1000)
        canonical_keys = list(set(s.canonical_key for s in pending))

        if not canonical_keys:
            return []

        # Calculate velocity for each
        velocities = await self.get_batch_velocity(canonical_keys)

        # Filter and sort by momentum
        high_momentum = [
            v for v in velocities.values()
            if v.momentum_score >= min_momentum
        ]
        high_momentum.sort(key=lambda v: v.momentum_score, reverse=True)

        return high_momentum[:limit]


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

def calculate_velocity_boost(
    signals_48h: int,
    unique_types: int,
    unique_sources: int,
    is_accelerating: bool = False,
) -> float:
    """
    Quick velocity boost calculation without full metrics.

    Useful for inline confidence adjustments.

    Args:
        signals_48h: Number of signals in last 48 hours
        unique_types: Number of unique signal types
        unique_sources: Number of unique source APIs
        is_accelerating: Whether velocity is accelerating

    Returns:
        Confidence boost (0.0 to 0.35)
    """
    boost = 0.0

    # Burst boost (2+ signals in 48h)
    if signals_48h >= 2:
        boost += 0.1

    # Type convergence (3+ types)
    if unique_types >= 3:
        boost += 0.15
    elif unique_types >= 2:
        boost += 0.05

    # Source convergence (2+ sources)
    if unique_sources >= 2:
        boost += 0.1

    # Acceleration boost
    if is_accelerating:
        boost += 0.05

    return min(boost, 0.35)
