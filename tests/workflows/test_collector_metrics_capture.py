"""Tests for collector metrics capture in pipeline."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from workflows.pipeline import DiscoveryPipeline, PipelineConfig, CollectorMetrics
from discovery_engine.mcp_server import CollectorResult, CollectorStatus


@pytest.fixture
def mock_store():
    """Create mock SignalStore."""
    store = AsyncMock()
    store.save_collector_metrics = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_run_single_collector_captures_timing(mock_store):
    """Verify _run_single_collector captures timing metrics."""
    config = PipelineConfig(
        db_path=":memory:",
        github_token="fake-token",
    )
    pipeline = DiscoveryPipeline(config)
    pipeline._store = mock_store
    pipeline._collector_metrics = []

    # Mock the collector
    mock_result = CollectorResult(
        collector="github",
        status=CollectorStatus.SUCCESS,
        signals_found=42,
        dry_run=True,
    )

    with patch("collectors.github.GitHubCollector") as MockCollector:
        mock_collector = AsyncMock()
        mock_collector.run = AsyncMock(return_value=mock_result)
        mock_collector._retry_count = 2
        mock_collector._errors = []
        MockCollector.return_value = mock_collector

        result = await pipeline._run_single_collector("github", dry_run=True)

    assert result.signals_found == 42
    assert len(pipeline._collector_metrics) == 1
    metrics = pipeline._collector_metrics[0]
    assert metrics.collector_name == "github"
    assert metrics.duration_seconds is not None
    assert metrics.duration_seconds >= 0
    assert metrics.signals_found == 42
    assert metrics.retries == 2


@pytest.mark.asyncio
async def test_run_single_collector_captures_timing_on_error(mock_store):
    """Verify timing is captured even when collector fails."""
    config = PipelineConfig(
        db_path=":memory:",
        github_token="fake-token",
    )
    pipeline = DiscoveryPipeline(config)
    pipeline._store = mock_store
    pipeline._collector_metrics = []

    with patch("collectors.github.GitHubCollector") as MockCollector:
        mock_collector = AsyncMock()
        mock_collector.run = AsyncMock(side_effect=Exception("API rate limit exceeded"))
        mock_collector._retry_count = 3
        mock_collector._errors = ["API rate limit exceeded"]
        MockCollector.return_value = mock_collector

        result = await pipeline._run_single_collector("github", dry_run=True)

    # Result should be an error
    assert result.status == CollectorStatus.ERROR
    assert "API rate limit exceeded" in result.error_message

    # But timing should still be captured
    assert len(pipeline._collector_metrics) == 1
    metrics = pipeline._collector_metrics[0]
    assert metrics.collector_name == "github"
    assert metrics.duration_seconds is not None
    assert metrics.duration_seconds >= 0
    assert metrics.status == "error"
    assert metrics.errors == 1
    assert "API rate limit exceeded" in metrics.error_messages


@pytest.mark.asyncio
async def test_run_single_collector_captures_status(mock_store):
    """Verify collector status is captured in metrics."""
    config = PipelineConfig(
        db_path=":memory:",
        github_token="fake-token",
    )
    pipeline = DiscoveryPipeline(config)
    pipeline._store = mock_store
    pipeline._collector_metrics = []

    mock_result = CollectorResult(
        collector="github",
        status=CollectorStatus.SUCCESS,
        signals_found=10,
        dry_run=False,
    )

    with patch("collectors.github.GitHubCollector") as MockCollector:
        mock_collector = AsyncMock()
        mock_collector.run = AsyncMock(return_value=mock_result)
        mock_collector._retry_count = 0
        mock_collector._errors = []
        MockCollector.return_value = mock_collector

        await pipeline._run_single_collector("github", dry_run=False)

    metrics = pipeline._collector_metrics[0]
    assert metrics.status == "success"


@pytest.mark.asyncio
async def test_collector_metrics_initialized_in_pipeline():
    """Verify _collector_metrics list is initialized in __init__."""
    config = PipelineConfig(db_path=":memory:")
    pipeline = DiscoveryPipeline(config)

    # Should have _collector_metrics attribute as an empty list
    assert hasattr(pipeline, "_collector_metrics")
    assert isinstance(pipeline._collector_metrics, list)
    assert len(pipeline._collector_metrics) == 0


@pytest.mark.asyncio
async def test_pipeline_saves_collector_metrics():
    """Verify pipeline saves collector metrics alongside run metrics."""
    from workflows.pipeline import PipelineStats

    mock_store = AsyncMock()
    mock_store.save_pipeline_run = AsyncMock(return_value="run-123")
    mock_store.save_collector_metrics = AsyncMock()

    config = PipelineConfig(db_path=":memory:")
    pipeline = DiscoveryPipeline(config)
    pipeline._store = mock_store
    pipeline._initialized = True

    # Add some metrics
    metrics1 = CollectorMetrics(
        collector_name="github",
        started_at=datetime.now(timezone.utc),
        signals_found=42,
        status="success",
    )
    metrics1.complete()
    pipeline._collector_metrics = [metrics1]

    # Create a minimal PipelineStats
    stats = PipelineStats()
    stats.complete()

    await pipeline._save_pipeline_metrics(stats)

    # Verify pipeline run was saved
    mock_store.save_pipeline_run.assert_called_once()

    # Verify collector metrics were saved
    mock_store.save_collector_metrics.assert_called_once()
    call_args = mock_store.save_collector_metrics.call_args
    assert call_args[0][0] == "run-123"  # run_id
    assert call_args[0][1].collector_name == "github"
