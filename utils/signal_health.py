"""
Signal Health Monitoring - Track signal quality and detect anomalies.

Monitors:
- Signal volume by source (detect API issues or bot activity)
- Signal freshness (detect stale data)
- Signal quality (detect suspicious patterns)
- Source reliability (track error rates)

Usage:
    from utils.signal_health import SignalHealthMonitor

    monitor = SignalHealthMonitor(signal_store)
    report = await monitor.generate_report()
    print(report.to_dict())
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# THRESHOLDS
# =============================================================================

# Volume thresholds
HIGH_VOLUME_THRESHOLD = 50  # More than this from one source = warning
CRITICAL_VOLUME_THRESHOLD = 200  # More than this = critical

# Freshness thresholds (days)
STALE_SIGNAL_DAYS = 30  # Signals older than this are stale
CRITICAL_STALE_DAYS = 90  # Signals older than this are very stale

# Quality thresholds
SUSPICIOUS_CONFIDENCE_VALUES = {0.0, 0.5, 1.0}  # Suspiciously round values
MIN_CONFIDENCE_VARIANCE = 0.1  # If all confidences are same, suspicious


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SourceHealth:
    """Health metrics for a single source."""
    source_name: str
    signal_count: int = 0
    signals_last_24h: int = 0
    signals_last_7d: int = 0
    avg_confidence: float = 0.0
    confidence_variance: float = 0.0
    oldest_signal_days: int = 0
    newest_signal_days: int = 0
    error_count: int = 0

    # Computed status
    status: str = "HEALTHY"  # HEALTHY, WARNING, CRITICAL
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "source_name": self.source_name,
            "signal_count": self.signal_count,
            "signals_last_24h": self.signals_last_24h,
            "signals_last_7d": self.signals_last_7d,
            "avg_confidence": round(self.avg_confidence, 3),
            "confidence_variance": round(self.confidence_variance, 3),
            "oldest_signal_days": self.oldest_signal_days,
            "newest_signal_days": self.newest_signal_days,
            "error_count": self.error_count,
            "status": self.status,
            "warnings": self.warnings,
        }


@dataclass
class Anomaly:
    """A detected anomaly in signal data."""
    anomaly_type: str
    severity: str  # WARNING, CRITICAL
    source: Optional[str]
    description: str
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signal_ids: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "source": self.source,
            "description": self.description,
            "detected_at": self.detected_at.isoformat(),
            "signal_count": len(self.signal_ids),
        }


@dataclass
class HealthReport:
    """Complete health report for signal data."""
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Overall status
    overall_status: str = "HEALTHY"  # HEALTHY, DEGRADED, CRITICAL

    # Metrics
    total_signals: int = 0
    total_sources: int = 0
    signals_last_24h: int = 0
    signals_last_7d: int = 0

    # Per-source health
    source_health: Dict[str, SourceHealth] = field(default_factory=dict)

    # Detected anomalies
    anomalies: List[Anomaly] = field(default_factory=list)

    # Freshness
    stale_signals: int = 0
    critically_stale_signals: int = 0

    # Quality
    suspicious_signals: int = 0

    def to_dict(self) -> Dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "overall_status": self.overall_status,
            "total_signals": self.total_signals,
            "total_sources": self.total_sources,
            "signals_last_24h": self.signals_last_24h,
            "signals_last_7d": self.signals_last_7d,
            "source_health": {
                k: v.to_dict() for k, v in self.source_health.items()
            },
            "anomalies": [a.to_dict() for a in self.anomalies],
            "stale_signals": self.stale_signals,
            "critically_stale_signals": self.critically_stale_signals,
            "suspicious_signals": self.suspicious_signals,
        }

    def __str__(self) -> str:
        """Human-readable report."""
        lines = [
            "=" * 60,
            "SIGNAL HEALTH REPORT",
            f"Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "=" * 60,
            "",
            f"Overall Status: {self.overall_status}",
            f"Total Signals: {self.total_signals}",
            f"Total Sources: {self.total_sources}",
            f"Last 24h: {self.signals_last_24h}",
            f"Last 7d: {self.signals_last_7d}",
            "",
        ]

        if self.anomalies:
            lines.append("ANOMALIES DETECTED:")
            lines.append("-" * 60)
            for anomaly in self.anomalies:
                lines.append(f"  [{anomaly.severity}] {anomaly.anomaly_type}")
                lines.append(f"    {anomaly.description}")
                if anomaly.source:
                    lines.append(f"    Source: {anomaly.source}")
            lines.append("")

        lines.append("SOURCE HEALTH:")
        lines.append("-" * 60)
        for name, health in self.source_health.items():
            status_emoji = {"HEALTHY": "✓", "WARNING": "⚠", "CRITICAL": "✗"}.get(
                health.status, "?"
            )
            lines.append(f"  {status_emoji} {name}: {health.signal_count} signals")
            if health.warnings:
                for warning in health.warnings:
                    lines.append(f"      → {warning}")

        if self.stale_signals > 0:
            lines.append("")
            lines.append("FRESHNESS:")
            lines.append("-" * 60)
            lines.append(f"  Stale signals (>{STALE_SIGNAL_DAYS}d): {self.stale_signals}")
            lines.append(f"  Critically stale (>{CRITICAL_STALE_DAYS}d): {self.critically_stale_signals}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


# =============================================================================
# MONITOR
# =============================================================================

class SignalHealthMonitor:
    """
    Monitors signal quality and detects anomalies.

    Checks for:
    - High volume from single source (API issue or bot)
    - Stale signals (data not refreshing)
    - Suspicious confidence patterns (gaming)
    - Source reliability issues
    """

    def __init__(self, signal_store: Any):
        """
        Args:
            signal_store: SignalStore instance
        """
        self.store = signal_store

    async def generate_report(self, lookback_days: int = 30) -> HealthReport:
        """
        Generate a complete health report.

        Args:
            lookback_days: How far back to analyze

        Returns:
            HealthReport with all metrics
        """
        report = HealthReport()

        # Get signals from store
        signals = await self._get_signals(lookback_days)

        if not signals:
            logger.info("No signals found for health report")
            return report

        report.total_signals = len(signals)

        # Analyze by source
        await self._analyze_sources(signals, report)

        # Check freshness
        await self._check_freshness(signals, report)

        # Check quality
        await self._check_quality(signals, report)

        # Detect anomalies
        await self._detect_anomalies(signals, report)

        # Compute overall status
        self._compute_overall_status(report)

        return report

    async def _get_signals(self, lookback_days: int) -> List[Dict]:
        """Get signals from store for analysis."""
        if not self.store or not self.store._conn:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        cursor = self.store._conn.execute(
            """
            SELECT
                id, signal_type, source_api, canonical_key,
                confidence, detected_at, created_at
            FROM signals
            WHERE datetime(created_at) > datetime(?)
            ORDER BY created_at DESC
            """,
            (cutoff.isoformat(),)
        )

        return [
            {
                "id": row[0],
                "signal_type": row[1],
                "source_api": row[2],
                "canonical_key": row[3],
                "confidence": row[4],
                "detected_at": datetime.fromisoformat(row[5]),
                "created_at": datetime.fromisoformat(row[6]),
            }
            for row in cursor.fetchall()
        ]

    async def _analyze_sources(
        self,
        signals: List[Dict],
        report: HealthReport,
    ) -> None:
        """Analyze signals by source."""
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        by_source: Dict[str, List[Dict]] = defaultdict(list)
        for sig in signals:
            by_source[sig["source_api"]].append(sig)

        report.total_sources = len(by_source)

        for source_name, source_signals in by_source.items():
            health = SourceHealth(source_name=source_name)
            health.signal_count = len(source_signals)

            # Time-based counts
            for sig in source_signals:
                created = sig["created_at"]
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)

                if created > day_ago:
                    health.signals_last_24h += 1
                    report.signals_last_24h += 1
                if created > week_ago:
                    health.signals_last_7d += 1
                    report.signals_last_7d += 1

            # Confidence stats
            confidences = [s["confidence"] for s in source_signals]
            if confidences:
                health.avg_confidence = sum(confidences) / len(confidences)
                if len(confidences) > 1:
                    mean = health.avg_confidence
                    health.confidence_variance = sum(
                        (c - mean) ** 2 for c in confidences
                    ) / len(confidences)

            # Age stats
            ages = []
            for sig in source_signals:
                detected = sig["detected_at"]
                if detected.tzinfo is None:
                    detected = detected.replace(tzinfo=timezone.utc)
                age = (now - detected).days
                ages.append(age)

            if ages:
                health.oldest_signal_days = max(ages)
                health.newest_signal_days = min(ages)

            # Check for warnings
            if health.signals_last_24h > HIGH_VOLUME_THRESHOLD:
                health.warnings.append(
                    f"High volume: {health.signals_last_24h} signals in 24h"
                )
                health.status = "WARNING"

            if health.signals_last_24h > CRITICAL_VOLUME_THRESHOLD:
                health.warnings.append(
                    f"Critical volume: {health.signals_last_24h} signals in 24h"
                )
                health.status = "CRITICAL"

            if health.confidence_variance < MIN_CONFIDENCE_VARIANCE and len(confidences) > 10:
                health.warnings.append(
                    f"Low confidence variance: {health.confidence_variance:.3f}"
                )
                health.status = max(health.status, "WARNING", key=lambda x: ["HEALTHY", "WARNING", "CRITICAL"].index(x))

            if health.newest_signal_days > 7:
                health.warnings.append(
                    f"No new signals in {health.newest_signal_days} days"
                )
                health.status = "WARNING"

            report.source_health[source_name] = health

    async def _check_freshness(
        self,
        signals: List[Dict],
        report: HealthReport,
    ) -> None:
        """Check signal freshness."""
        now = datetime.now(timezone.utc)

        for sig in signals:
            detected = sig["detected_at"]
            if detected.tzinfo is None:
                detected = detected.replace(tzinfo=timezone.utc)

            age_days = (now - detected).days

            if age_days > CRITICAL_STALE_DAYS:
                report.critically_stale_signals += 1
            elif age_days > STALE_SIGNAL_DAYS:
                report.stale_signals += 1

    async def _check_quality(
        self,
        signals: List[Dict],
        report: HealthReport,
    ) -> None:
        """Check signal quality."""
        for sig in signals:
            confidence = sig["confidence"]

            # Check for suspiciously round values
            if confidence in SUSPICIOUS_CONFIDENCE_VALUES:
                report.suspicious_signals += 1

    async def _detect_anomalies(
        self,
        signals: List[Dict],
        report: HealthReport,
    ) -> None:
        """Detect anomalies in signal data."""
        # High volume anomaly
        for source_name, health in report.source_health.items():
            if health.signals_last_24h > CRITICAL_VOLUME_THRESHOLD:
                report.anomalies.append(Anomaly(
                    anomaly_type="HIGH_VOLUME",
                    severity="CRITICAL",
                    source=source_name,
                    description=f"Source produced {health.signals_last_24h} signals in 24 hours",
                ))
            elif health.signals_last_24h > HIGH_VOLUME_THRESHOLD:
                report.anomalies.append(Anomaly(
                    anomaly_type="HIGH_VOLUME",
                    severity="WARNING",
                    source=source_name,
                    description=f"Source produced {health.signals_last_24h} signals in 24 hours",
                ))

        # Duplicate anomaly
        canonical_keys = [s["canonical_key"] for s in signals]
        key_counts = defaultdict(int)
        for key in canonical_keys:
            key_counts[key] += 1

        high_dupe_keys = [k for k, v in key_counts.items() if v > 10]
        if high_dupe_keys:
            report.anomalies.append(Anomaly(
                anomaly_type="HIGH_DUPLICATES",
                severity="WARNING",
                source=None,
                description=f"Found {len(high_dupe_keys)} canonical keys with 10+ signals each",
            ))

        # Stale data anomaly
        if report.critically_stale_signals > 10:
            report.anomalies.append(Anomaly(
                anomaly_type="STALE_DATA",
                severity="CRITICAL",
                source=None,
                description=f"Found {report.critically_stale_signals} signals older than {CRITICAL_STALE_DAYS} days",
            ))
        elif report.stale_signals > 50:
            report.anomalies.append(Anomaly(
                anomaly_type="STALE_DATA",
                severity="WARNING",
                source=None,
                description=f"Found {report.stale_signals} signals older than {STALE_SIGNAL_DAYS} days",
            ))

        # Quality anomaly
        if report.suspicious_signals > len(signals) * 0.3:
            report.anomalies.append(Anomaly(
                anomaly_type="SUSPICIOUS_QUALITY",
                severity="WARNING",
                source=None,
                description=f"Found {report.suspicious_signals} signals with suspicious confidence values",
            ))

    def _compute_overall_status(self, report: HealthReport) -> None:
        """Compute overall status from all metrics."""
        has_critical = any(
            a.severity == "CRITICAL" for a in report.anomalies
        ) or any(
            h.status == "CRITICAL" for h in report.source_health.values()
        )

        has_warning = any(
            a.severity == "WARNING" for a in report.anomalies
        ) or any(
            h.status == "WARNING" for h in report.source_health.values()
        )

        if has_critical:
            report.overall_status = "CRITICAL"
        elif has_warning:
            report.overall_status = "DEGRADED"
        else:
            report.overall_status = "HEALTHY"


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def detect_anomalies(
    signals: List[Dict],
    lookback_days: int = 7,
) -> List[str]:
    """
    Quick anomaly detection for a list of signals.

    Returns list of warning strings.

    Usage:
        warnings = detect_anomalies(signals)
        if warnings:
            for w in warnings:
                print(f"Warning: {w}")
    """
    warnings = []

    # Group by source
    by_source = defaultdict(list)
    for s in signals:
        source = s.get("source_api", "unknown")
        by_source[source].append(s)

    for source, source_signals in by_source.items():
        if len(source_signals) > HIGH_VOLUME_THRESHOLD:
            warnings.append(
                f"High volume from {source}: {len(source_signals)} signals"
            )

    # Check duplicates
    keys = [s.get("canonical_key", "") for s in signals]
    key_counts = defaultdict(int)
    for k in keys:
        key_counts[k] += 1

    dupes = sum(1 for c in key_counts.values() if c > 5)
    if dupes > 10:
        warnings.append(f"High duplication: {dupes} keys with 5+ signals")

    return warnings


# =============================================================================
# CLI
# =============================================================================

async def main():
    """CLI for testing health monitor."""
    import sys
    sys.path.insert(0, ".")

    from storage.signal_store import SignalStore

    db_path = sys.argv[1] if len(sys.argv) > 1 else "signals.db"

    print(f"Analyzing signals from: {db_path}")
    print()

    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        monitor = SignalHealthMonitor(store)
        report = await monitor.generate_report(lookback_days=30)
        print(report)
    finally:
        await store.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
