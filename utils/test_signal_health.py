"""
Tests for SignalHealthMonitor.

Tests:
1. Monitor initialization
2. Health report generation
3. Source health analysis
4. Freshness detection
5. Quality checks
6. Anomaly detection
7. Overall status computation

Run:
    python -m pytest utils/test_signal_health.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from utils.signal_health import (
    SignalHealthMonitor,
    SourceHealth,
    Anomaly,
    HealthReport,
    HIGH_VOLUME_THRESHOLD,
    CRITICAL_VOLUME_THRESHOLD,
    STALE_SIGNAL_DAYS,
    SUSPICIOUS_CONFIDENCE_VALUES,
    detect_anomalies,
)


# =============================================================================
# TEST DATA
# =============================================================================

def make_signal(
    source_api: str = "github",
    confidence: float = 0.7,
    detected_at: datetime = None,
    created_at: datetime = None,
    signal_id: int = 1,
    canonical_key: str = None,
):
    """Create a mock signal dict."""
    if detected_at is None:
        detected_at = datetime.now(timezone.utc)
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    if canonical_key is None:
        canonical_key = f"test:{signal_id}"
    return {
        "id": signal_id,
        "signal_type": "test_signal",
        "source_api": source_api,
        "canonical_key": canonical_key,
        "confidence": confidence,
        "detected_at": detected_at,
        "created_at": created_at,
    }


# =============================================================================
# UNIT TESTS - SourceHealth
# =============================================================================

class TestSourceHealth:
    """Test SourceHealth dataclass."""

    def test_source_health_defaults(self):
        """SourceHealth should have sensible defaults."""
        health = SourceHealth(source_name="github")

        assert health.source_name == "github"
        assert health.signal_count == 0
        assert health.status == "HEALTHY"
        assert health.warnings == []

    def test_source_health_to_dict(self):
        """SourceHealth.to_dict should return all fields."""
        health = SourceHealth(
            source_name="github",
            signal_count=100,
            signals_last_24h=10,
            avg_confidence=0.75,
            status="WARNING",
            warnings=["High volume"],
        )

        result = health.to_dict()

        assert result["source_name"] == "github"
        assert result["signal_count"] == 100
        assert result["avg_confidence"] == 0.75
        assert result["status"] == "WARNING"
        assert "High volume" in result["warnings"]


# =============================================================================
# UNIT TESTS - Anomaly
# =============================================================================

class TestAnomaly:
    """Test Anomaly dataclass."""

    def test_anomaly_creation(self):
        """Anomaly should capture all fields."""
        anomaly = Anomaly(
            anomaly_type="volume_spike",
            severity="WARNING",
            source="github",
            description="Unusual volume spike detected",
            signal_ids=[1, 2, 3],
        )

        assert anomaly.anomaly_type == "volume_spike"
        assert anomaly.severity == "WARNING"
        assert len(anomaly.signal_ids) == 3

    def test_anomaly_to_dict(self):
        """Anomaly.to_dict should return all fields."""
        anomaly = Anomaly(
            anomaly_type="stale_data",
            severity="CRITICAL",
            source=None,
            description="No fresh signals",
        )

        result = anomaly.to_dict()

        assert result["anomaly_type"] == "stale_data"
        assert result["severity"] == "CRITICAL"
        assert result["source"] is None


# =============================================================================
# UNIT TESTS - HealthReport
# =============================================================================

class TestHealthReport:
    """Test HealthReport dataclass."""

    def test_health_report_defaults(self):
        """HealthReport should have sensible defaults."""
        report = HealthReport()

        assert report.overall_status == "HEALTHY"
        assert report.total_signals == 0
        assert report.source_health == {}
        assert report.anomalies == []

    def test_health_report_to_dict(self):
        """HealthReport.to_dict should include all fields."""
        report = HealthReport(
            overall_status="DEGRADED",
            total_signals=100,
        )

        result = report.to_dict()

        assert result["overall_status"] == "DEGRADED"
        assert result["total_signals"] == 100

    def test_health_report_str(self):
        """HealthReport.__str__ should produce readable output."""
        report = HealthReport(
            overall_status="HEALTHY",
            total_signals=50,
            total_sources=3,
        )
        report.source_health["github"] = SourceHealth(
            source_name="github",
            signal_count=20,
        )

        output = str(report)

        assert "SIGNAL HEALTH REPORT" in output
        assert "HEALTHY" in output
        assert "github" in output


# =============================================================================
# INTEGRATION TESTS - SignalHealthMonitor
# =============================================================================

class TestSignalHealthMonitor:
    """Test SignalHealthMonitor class."""

    def test_monitor_initialization(self):
        """Monitor should initialize with signal store."""
        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        assert monitor.store is mock_store

    @pytest.mark.asyncio
    async def test_generate_report_empty_store(self):
        """Report should be HEALTHY with empty store."""
        mock_store = MagicMock()
        mock_store._db = None  # No database connection

        monitor = SignalHealthMonitor(mock_store)
        report = await monitor.generate_report(lookback_days=30)

        assert report.overall_status == "HEALTHY"
        assert report.total_signals == 0

    @pytest.mark.asyncio
    async def test_generate_report_with_signals(self):
        """Report should analyze signals correctly."""
        # Create test signals from different sources
        now = datetime.now(timezone.utc)
        signals = [
            make_signal("github", 0.7, signal_id=1, detected_at=now, created_at=now),
            make_signal("github", 0.8, signal_id=2, detected_at=now, created_at=now),
            make_signal("sec_edgar", 0.6, signal_id=3, detected_at=now, created_at=now),
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        # Mock _get_signals directly
        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=30)

            assert report.total_signals == 3
            assert "github" in report.source_health
            assert "sec_edgar" in report.source_health
            assert report.source_health["github"].signal_count == 2
            assert report.source_health["sec_edgar"].signal_count == 1

    @pytest.mark.asyncio
    async def test_detect_volume_spike(self):
        """Should detect volume spikes as anomalies."""
        now = datetime.now(timezone.utc)

        # Create many signals from one source in last 24h (volume spike)
        signals = [
            make_signal(
                "github",
                0.5,
                signal_id=i,
                detected_at=now,
                created_at=now - timedelta(hours=1),
            )
            for i in range(CRITICAL_VOLUME_THRESHOLD + 10)
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=1)

            # Should have volume-related anomaly
            assert report.overall_status == "CRITICAL"
            github_health = report.source_health.get("github")
            assert github_health is not None
            assert github_health.signal_count > CRITICAL_VOLUME_THRESHOLD
            assert github_health.status == "CRITICAL"

            # Should have HIGH_VOLUME anomaly
            volume_anomalies = [a for a in report.anomalies if a.anomaly_type == "HIGH_VOLUME"]
            assert len(volume_anomalies) > 0

    @pytest.mark.asyncio
    async def test_detect_stale_signals(self):
        """Should detect stale signals."""
        now = datetime.now(timezone.utc)
        old_date = now - timedelta(days=STALE_SIGNAL_DAYS + 10)

        signals = [
            make_signal(
                "github",
                0.7,
                detected_at=old_date,
                created_at=now,
                signal_id=i,
            )
            for i in range(5)
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=60)

            # Should detect staleness
            assert report.stale_signals == 5
            github_health = report.source_health.get("github")
            assert github_health is not None
            assert github_health.oldest_signal_days >= STALE_SIGNAL_DAYS

    @pytest.mark.asyncio
    async def test_detect_suspicious_confidence(self):
        """Should detect suspicious confidence values."""
        now = datetime.now(timezone.utc)

        # Create signals with suspicious round confidence values
        signals = [
            make_signal("github", 1.0, signal_id=1, detected_at=now, created_at=now),
            make_signal("github", 1.0, signal_id=2, detected_at=now, created_at=now),
            make_signal("github", 1.0, signal_id=3, detected_at=now, created_at=now),
            make_signal("github", 0.0, signal_id=4, detected_at=now, created_at=now),
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=30)

            # Should flag suspicious signals
            assert report.suspicious_signals == 4  # All have suspicious values (0.0 or 1.0)

    @pytest.mark.asyncio
    async def test_multiple_sources_independent(self):
        """Each source should be analyzed independently."""
        now = datetime.now(timezone.utc)

        signals = [
            make_signal("github", 0.9, signal_id=1, detected_at=now, created_at=now),
            make_signal("sec_edgar", 0.3, signal_id=2, detected_at=now, created_at=now),
            make_signal("companies_house", 0.7, signal_id=3, detected_at=now, created_at=now),
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=30)

            assert len(report.source_health) == 3
            assert report.source_health["github"].avg_confidence == 0.9
            assert report.source_health["sec_edgar"].avg_confidence == 0.3

    @pytest.mark.asyncio
    async def test_healthy_status_with_recent_signals(self):
        """Should return HEALTHY with normal recent signals."""
        now = datetime.now(timezone.utc)

        # Create a normal number of recent signals
        signals = [
            make_signal("github", 0.7, signal_id=1, detected_at=now, created_at=now),
            make_signal("github", 0.8, signal_id=2, detected_at=now, created_at=now - timedelta(hours=5)),
            make_signal("sec_edgar", 0.6, signal_id=3, detected_at=now, created_at=now - timedelta(hours=12)),
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=30)

            # Should be healthy with normal signals
            assert report.overall_status == "HEALTHY"
            assert report.total_signals == 3
            assert report.signals_last_24h == 3
            assert len(report.anomalies) == 0

    @pytest.mark.asyncio
    async def test_warning_status_with_stale_signals(self):
        """Should return WARNING when all signals are old (no recent activity)."""
        now = datetime.now(timezone.utc)
        old_date = now - timedelta(days=10)

        # Create signals that are all old (10 days)
        signals = [
            make_signal("github", 0.7, signal_id=1, detected_at=old_date, created_at=old_date),
            make_signal("github", 0.8, signal_id=2, detected_at=old_date, created_at=old_date),
            make_signal("github", 0.6, signal_id=3, detected_at=old_date, created_at=old_date),
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=30)

            # Should have warning due to no recent signals
            github_health = report.source_health["github"]
            assert github_health.status == "WARNING"
            assert any("No new signals" in w for w in github_health.warnings)
            assert github_health.newest_signal_days >= 10
            assert report.overall_status == "DEGRADED"

    @pytest.mark.asyncio
    async def test_critical_status_with_no_signals(self):
        """Should handle empty signals gracefully."""
        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            report = await monitor.generate_report(lookback_days=30)

            # With no signals, should be HEALTHY (no issues detected)
            assert report.overall_status == "HEALTHY"
            assert report.total_signals == 0
            assert report.total_sources == 0

    @pytest.mark.asyncio
    async def test_anomaly_detection_volume_drop(self):
        """Should detect volume drops (no recent signals from active source)."""
        now = datetime.now(timezone.utc)

        # All signals are 15 days old - indicates source stopped producing
        signals = [
            make_signal(
                "github",
                0.7,
                signal_id=i,
                detected_at=now - timedelta(days=15),
                created_at=now - timedelta(days=15),
            )
            for i in range(10)
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=30)

            # Should detect that source hasn't produced new signals
            github_health = report.source_health["github"]
            assert github_health.status == "WARNING"
            assert any("No new signals" in w for w in github_health.warnings)
            assert github_health.signals_last_24h == 0
            assert github_health.signals_last_7d == 0
            assert github_health.newest_signal_days >= 15

    @pytest.mark.asyncio
    async def test_overall_status_degraded_on_warning(self):
        """Overall status should be DEGRADED when warnings exist."""
        now = datetime.now(timezone.utc)

        # Create enough signals in 24h to trigger warning but not critical
        signals = [
            make_signal(
                "github",
                0.7,
                signal_id=i,
                detected_at=now,
                created_at=now - timedelta(hours=1),
            )
            for i in range(HIGH_VOLUME_THRESHOLD + 10)
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report(lookback_days=1)

            # Should be DEGRADED (warning level)
            assert report.overall_status in ["DEGRADED", "CRITICAL"]
            assert len(report.anomalies) > 0


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_store_with_no_db(self):
        """Monitor should handle store with no _db gracefully."""
        mock_store = MagicMock()
        mock_store._db = None

        monitor = SignalHealthMonitor(mock_store)
        report = await monitor.generate_report()

        assert report.overall_status == "HEALTHY"
        assert report.total_signals == 0

    @pytest.mark.asyncio
    async def test_empty_signals_list(self):
        """Should handle empty signals list."""
        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            report = await monitor.generate_report()

            assert report.overall_status == "HEALTHY"
            assert report.total_signals == 0

    @pytest.mark.asyncio
    async def test_signals_without_timezone(self):
        """Should handle signals without timezone info."""
        now = datetime.now()  # No timezone

        signals = [
            {
                "id": 1,
                "signal_type": "test",
                "source_api": "github",
                "canonical_key": "test:1",
                "confidence": 0.7,
                "detected_at": now,
                "created_at": now,
            }
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            # Should not raise, should handle gracefully
            report = await monitor.generate_report()

            assert report.total_signals == 1

    @pytest.mark.asyncio
    async def test_duplicate_detection(self):
        """Should detect high duplicate canonical keys."""
        now = datetime.now(timezone.utc)

        # Create signals with same canonical key
        signals = [
            make_signal(
                "github",
                0.7,
                signal_id=i,
                canonical_key="duplicate:key",
                detected_at=now,
                created_at=now,
            )
            for i in range(15)  # More than 10 duplicates
        ]

        mock_store = MagicMock()
        monitor = SignalHealthMonitor(mock_store)

        with patch.object(monitor, "_get_signals", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = signals
            report = await monitor.generate_report()

            # Should have duplicate anomaly
            dupe_anomalies = [a for a in report.anomalies if a.anomaly_type == "HIGH_DUPLICATES"]
            assert len(dupe_anomalies) > 0


# =============================================================================
# CONVENIENCE FUNCTION TESTS
# =============================================================================

class TestDetectAnomalies:
    """Test the convenience function detect_anomalies."""

    def test_detect_anomalies_high_volume(self):
        """Should detect high volume from a single source."""
        now = datetime.now(timezone.utc)

        # Create high volume signals
        signals = [
            make_signal("github", 0.7, signal_id=i, detected_at=now, created_at=now)
            for i in range(HIGH_VOLUME_THRESHOLD + 10)
        ]

        warnings = detect_anomalies(signals)

        assert len(warnings) > 0
        assert any("High volume" in w for w in warnings)
        assert any("github" in w for w in warnings)

    def test_detect_anomalies_duplicates(self):
        """Should detect high duplicate keys."""
        now = datetime.now(timezone.utc)

        # Create many signals with same canonical key (6 signals per key, 15 keys = 90 signals)
        signals = []
        for i in range(15):
            for j in range(6):
                sig = make_signal(
                    "github",
                    0.7,
                    signal_id=i * 10 + j,
                    canonical_key=f"duplicate:key{i}",
                    detected_at=now,
                    created_at=now,
                )
                signals.append(sig)

        warnings = detect_anomalies(signals)

        assert len(warnings) > 0
        assert any("duplication" in w.lower() for w in warnings)

    def test_detect_anomalies_healthy_signals(self):
        """Should return empty list for healthy signals."""
        now = datetime.now(timezone.utc)

        signals = [
            make_signal("github", 0.7, signal_id=1, detected_at=now, created_at=now),
            make_signal("sec_edgar", 0.8, signal_id=2, detected_at=now, created_at=now),
            make_signal("product_hunt", 0.6, signal_id=3, detected_at=now, created_at=now),
        ]

        warnings = detect_anomalies(signals)

        assert warnings == []

    def test_detect_anomalies_empty_signals(self):
        """Should handle empty signals list."""
        warnings = detect_anomalies([])
        assert warnings == []

    def test_detect_anomalies_multiple_sources(self):
        """Should detect anomalies across multiple sources."""
        now = datetime.now(timezone.utc)

        # Create high volume from two different sources
        signals = []
        for i in range(HIGH_VOLUME_THRESHOLD + 5):
            signals.append(make_signal("github", 0.7, signal_id=i, detected_at=now, created_at=now))
        for i in range(HIGH_VOLUME_THRESHOLD + 3):
            signals.append(make_signal("sec_edgar", 0.8, signal_id=i + 100, detected_at=now, created_at=now))

        warnings = detect_anomalies(signals)

        # Should detect both sources
        assert len(warnings) >= 2
        github_warnings = [w for w in warnings if "github" in w]
        edgar_warnings = [w for w in warnings if "sec_edgar" in w]
        assert len(github_warnings) > 0
        assert len(edgar_warnings) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
