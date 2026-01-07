"""
Tests for health monitor integration in DiscoveryPipeline.

Tests that the pipeline properly:
1. Initializes SignalHealthMonitor
2. Calls health monitor after collector runs
3. Includes health stats in PipelineStats
4. Handles health monitoring errors gracefully
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from workflows.pipeline import DiscoveryPipeline, PipelineConfig, PipelineStats
from storage.signal_store import SignalStore, StoredSignal
from utils.signal_health import (
    SignalHealthMonitor,
    HealthReport,
    SourceHealth,
    Anomaly,
)
from discovery_engine.mcp_server import CollectorResult, CollectorStatus


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def pipeline_config():
    """Pipeline config with health monitoring enabled."""
    return PipelineConfig(
        db_path=":memory:",
        warmup_suppression_cache=False,  # Don't warm up for tests
        notion_api_key="test_key",
        notion_database_id="test_db",
    )


@pytest_asyncio.fixture
async def signal_store():
    """Create an in-memory signal store."""
    store = SignalStore(db_path=":memory:")
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def mock_health_report():
    """Mock health report with sample data."""
    report = HealthReport(
        overall_status="HEALTHY",
        total_signals=10,
        total_sources=2,
        signals_last_24h=5,
        signals_last_7d=8,
    )

    # Add source health
    report.source_health["github"] = SourceHealth(
        source_name="github",
        signal_count=6,
        signals_last_24h=3,
        signals_last_7d=5,
        avg_confidence=0.75,
        confidence_variance=0.05,
        oldest_signal_days=10,
        newest_signal_days=0,
        status="HEALTHY",
    )

    report.source_health["sec_edgar"] = SourceHealth(
        source_name="sec_edgar",
        signal_count=4,
        signals_last_24h=2,
        signals_last_7d=3,
        avg_confidence=0.80,
        confidence_variance=0.03,
        oldest_signal_days=5,
        newest_signal_days=0,
        status="HEALTHY",
    )

    return report


@pytest.fixture
def mock_degraded_health_report():
    """Mock health report with warnings."""
    report = HealthReport(
        overall_status="DEGRADED",
        total_signals=100,
        total_sources=1,
        signals_last_24h=60,
        signals_last_7d=80,
    )

    # Add source with warning
    report.source_health["github"] = SourceHealth(
        source_name="github",
        signal_count=100,
        signals_last_24h=60,
        signals_last_7d=80,
        avg_confidence=0.75,
        confidence_variance=0.05,
        oldest_signal_days=10,
        newest_signal_days=0,
        status="WARNING",
        warnings=["High volume: 60 signals in 24h"],
    )

    # Add anomaly
    report.anomalies.append(Anomaly(
        anomaly_type="HIGH_VOLUME",
        severity="WARNING",
        source="github",
        description="Source produced 60 signals in 24 hours",
    ))

    return report


# =============================================================================
# INITIALIZATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_pipeline_initializes_health_monitor(pipeline_config):
    """Test that pipeline initializes health monitor during setup."""
    pipeline = DiscoveryPipeline(pipeline_config)

    # Health monitor should not exist before initialization
    assert not hasattr(pipeline, "_health_monitor") or pipeline._health_monitor is None

    await pipeline.initialize()

    try:
        # Health monitor should be initialized
        assert hasattr(pipeline, "_health_monitor")
        assert pipeline._health_monitor is not None
        assert isinstance(pipeline._health_monitor, SignalHealthMonitor)

        # Should have reference to signal store
        assert pipeline._health_monitor.store is pipeline._store

    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_health_monitor_survives_initialization_errors(pipeline_config):
    """Test that health monitor initialization errors don't crash pipeline."""
    pipeline = DiscoveryPipeline(pipeline_config)

    # Mock SignalHealthMonitor to raise during init
    with patch("workflows.pipeline.SignalHealthMonitor") as mock_monitor_class:
        mock_monitor_class.side_effect = RuntimeError("Health monitor init failed")

        # Pipeline initialization should still succeed
        await pipeline.initialize()

        try:
            # Pipeline should be initialized
            assert pipeline._initialized

            # Health monitor should be None (failed to initialize)
            assert pipeline._health_monitor is None

        finally:
            await pipeline.close()


# =============================================================================
# COLLECTOR INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_health_monitor_called_after_collectors(
    pipeline_config,
    signal_store,
    mock_health_report,
):
    """Test that health monitor is called after collector runs."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=5,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Replace health monitor with mock AFTER initialization
            mock_monitor = AsyncMock(spec=SignalHealthMonitor)
            mock_monitor.generate_report = AsyncMock(return_value=mock_health_report)
            pipeline._health_monitor = mock_monitor

            # Run collectors
            await pipeline._run_collectors_stage(["github"], dry_run=True)

            # Health monitor should have been called
            mock_monitor.generate_report.assert_called_once()

        finally:
            await pipeline.close()


@pytest.mark.asyncio
async def test_health_stats_included_in_pipeline_stats(
    pipeline_config,
    signal_store,
    mock_health_report,
):
    """Test that health stats are included in PipelineStats."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=5,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Replace health monitor with mock AFTER initialization
            mock_monitor = AsyncMock(spec=SignalHealthMonitor)
            mock_monitor.generate_report = AsyncMock(return_value=mock_health_report)
            pipeline._health_monitor = mock_monitor

            # Run full pipeline
            stats = await pipeline.run_full_pipeline(
                collectors=["github"],
                dry_run=True,
            )

            # Stats should include health report
            assert hasattr(stats, "health_report")
            assert stats.health_report is not None
            assert stats.health_report.overall_status == "HEALTHY"
            assert stats.health_report.total_signals == 10
            assert stats.health_report.total_sources == 2

            # Stats dict should include health section
            stats_dict = stats.to_dict()
            assert "health" in stats_dict
            assert stats_dict["health"]["overall_status"] == "HEALTHY"
            assert stats_dict["health"]["total_signals"] == 10

        finally:
            await pipeline.close()


@pytest.mark.asyncio
async def test_health_warnings_logged(
    pipeline_config,
    signal_store,
    mock_degraded_health_report,
    caplog,
):
    """Test that health warnings are logged."""
    import logging
    caplog.set_level(logging.WARNING)

    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=60,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Replace health monitor with mock AFTER initialization
            mock_monitor = AsyncMock(spec=SignalHealthMonitor)
            mock_monitor.generate_report = AsyncMock(return_value=mock_degraded_health_report)
            pipeline._health_monitor = mock_monitor

            # Run collectors
            await pipeline._run_collectors_stage(["github"], dry_run=True)

            # Should log warnings about degraded health
            assert any(
                "DEGRADED" in record.message or "WARNING" in record.message
                for record in caplog.records
            )

        finally:
            await pipeline.close()


@pytest.mark.asyncio
async def test_health_monitor_errors_dont_crash_pipeline(pipeline_config, signal_store):
    """Test that health monitor errors don't crash the pipeline."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock health monitor that raises
    mock_monitor = AsyncMock(spec=SignalHealthMonitor)
    mock_monitor.generate_report = AsyncMock(
        side_effect=RuntimeError("Health check failed")
    )
    pipeline._health_monitor = mock_monitor

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=5,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Run collectors - should succeed despite health monitor error
            results = await pipeline._run_collectors_stage(["github"], dry_run=True)

            assert len(results) == 1
            assert results[0].status == CollectorStatus.SUCCESS

        finally:
            await pipeline.close()


# =============================================================================
# HEALTH REPORT INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_health_report_includes_anomalies(
    pipeline_config,
    signal_store,
    mock_degraded_health_report,
):
    """Test that anomalies from health report are accessible."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=60,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Replace health monitor with mock AFTER initialization
            mock_monitor = AsyncMock(spec=SignalHealthMonitor)
            mock_monitor.generate_report = AsyncMock(return_value=mock_degraded_health_report)
            pipeline._health_monitor = mock_monitor

            # Run full pipeline
            stats = await pipeline.run_full_pipeline(
                collectors=["github"],
                dry_run=True,
            )

            # Stats should include anomalies
            assert hasattr(stats, "health_report")
            assert len(stats.health_report.anomalies) > 0
            assert stats.health_report.anomalies[0].anomaly_type == "HIGH_VOLUME"
            assert stats.health_report.anomalies[0].severity == "WARNING"

            # Stats dict should include anomalies
            stats_dict = stats.to_dict()
            assert "health" in stats_dict
            assert "anomalies" in stats_dict["health"]
            assert len(stats_dict["health"]["anomalies"]) > 0

        finally:
            await pipeline.close()


@pytest.mark.asyncio
async def test_health_report_includes_source_health(
    pipeline_config,
    signal_store,
    mock_health_report,
):
    """Test that source health metrics are included."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=5,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Replace health monitor with mock AFTER initialization
            mock_monitor = AsyncMock(spec=SignalHealthMonitor)
            mock_monitor.generate_report = AsyncMock(return_value=mock_health_report)
            pipeline._health_monitor = mock_monitor

            # Run full pipeline
            stats = await pipeline.run_full_pipeline(
                collectors=["github"],
                dry_run=True,
            )

            # Stats should include source health
            assert hasattr(stats, "health_report")
            assert "github" in stats.health_report.source_health
            assert "sec_edgar" in stats.health_report.source_health

            github_health = stats.health_report.source_health["github"]
            assert github_health.signal_count == 6
            assert github_health.status == "HEALTHY"

            # Stats dict should include source health
            stats_dict = stats.to_dict()
            assert "health" in stats_dict
            assert "source_health" in stats_dict["health"]
            assert "github" in stats_dict["health"]["source_health"]

        finally:
            await pipeline.close()


@pytest.mark.asyncio
async def test_no_health_report_when_monitor_disabled(pipeline_config, signal_store):
    """Test that health report is None when monitor is not initialized."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Mock collector run
    with patch.object(pipeline, "_run_single_collector") as mock_run:
        mock_run.return_value = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=5,
            dry_run=True,
        )

        await pipeline.initialize()

        try:
            # Disable health monitor AFTER initialization
            pipeline._health_monitor = None

            # Run full pipeline
            stats = await pipeline.run_full_pipeline(
                collectors=["github"],
                dry_run=True,
            )

            # Stats should have None health report
            assert hasattr(stats, "health_report")
            assert stats.health_report is None

            # Stats dict should indicate health monitoring disabled
            stats_dict = stats.to_dict()
            assert "health" in stats_dict
            assert stats_dict["health"] is None

        finally:
            await pipeline.close()


# =============================================================================
# EDGE CASES
# =============================================================================

@pytest.mark.asyncio
async def test_health_monitor_with_empty_database(pipeline_config, signal_store):
    """Test health monitor with no signals in database."""
    pipeline = DiscoveryPipeline(pipeline_config)
    pipeline._store = signal_store

    # Create actual health monitor (will generate empty report)
    pipeline._health_monitor = SignalHealthMonitor(signal_store)

    await pipeline.initialize()

    try:
        # Generate health report
        report = await pipeline._health_monitor.generate_report()

        # Should return empty but valid report
        assert report is not None
        assert report.total_signals == 0
        assert report.total_sources == 0
        assert report.overall_status == "HEALTHY"

    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_health_monitor_with_real_signals(signal_store):
    """Integration test with actual signals in database."""
    # Add some test signals
    now = datetime.now(timezone.utc)

    await signal_store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:acme.ai",
        company_name="Acme Inc",
        confidence=0.85,
        raw_data={"repo": "acme/ml", "stars": 1000},
        detected_at=now - timedelta(days=1),
    )

    await signal_store.save_signal(
        signal_type="sec_form_d",
        source_api="sec_edgar",
        canonical_key="domain:acme.ai",
        company_name="Acme Inc",
        confidence=0.90,
        raw_data={"filing_type": "D", "amount": 5000000},
        detected_at=now - timedelta(hours=12),
    )

    # Create health monitor
    monitor = SignalHealthMonitor(signal_store)

    # Generate report
    report = await monitor.generate_report(lookback_days=7)

    # Should have signals
    assert report.total_signals == 2
    assert report.total_sources == 2
    assert report.signals_last_24h == 2

    # Should have source health
    assert "github" in report.source_health
    assert "sec_edgar" in report.source_health

    assert report.source_health["github"].signal_count == 1
    assert report.source_health["sec_edgar"].signal_count == 1
