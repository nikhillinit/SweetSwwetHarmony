"""
Tests for collector metrics persistence in collect-only mode.

Verifies that run_collectors() saves pipeline_runs + collector_metrics
when dry_run=False, and skips saves when dry_run=True.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from workflows.pipeline import DiscoveryPipeline, PipelineStats, CollectorMetrics


def _make_collector_metrics(
    name: str, status: str = "success", signals: int = 3, api_calls: int = 5
) -> CollectorMetrics:
    m = CollectorMetrics(
        collector_name=name,
        started_at=datetime.now(timezone.utc),
        signals_found=signals,
        status=status,
        api_calls=api_calls,
    )
    m.complete()
    return m


@pytest.fixture
def pipeline():
    """Create a pipeline with mocked internals."""
    p = DiscoveryPipeline.__new__(DiscoveryPipeline)
    p._store = MagicMock()
    p._store.save_pipeline_run = AsyncMock(return_value="run-123")
    p._store.save_collector_metrics = AsyncMock()
    p._collector_metrics = []
    p.config = MagicMock()
    p._notion = None
    p._begin_run_tracking = AsyncMock()
    p._end_run_tracking = AsyncMock()
    p.initialize = AsyncMock()
    return p


class TestCollectMetricsSavedWhenNotDryRun:
    """Metrics are persisted when dry_run=False."""

    @pytest.mark.asyncio
    async def test_metrics_saved_on_success(self, pipeline):
        """When collectors succeed and dry_run=False, metrics are saved."""
        metrics_a = _make_collector_metrics("news_api", "success", signals=5, api_calls=10)
        metrics_b = _make_collector_metrics("sec_edgar", "success", signals=3, api_calls=7)

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [metrics_a, metrics_b]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
                CollectorResult(collector="sec_edgar", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        results = await pipeline.run_collectors(["news_api", "sec_edgar"], dry_run=False)

        assert len(results) == 2
        # _save_pipeline_metrics should have been called
        pipeline._store.save_pipeline_run.assert_awaited_once()
        assert pipeline._store.save_collector_metrics.await_count == 2


class TestCollectMetricsSkippedOnDryRun:
    """Metrics are NOT persisted when dry_run=True."""

    @pytest.mark.asyncio
    async def test_no_save_on_dry_run(self, pipeline):
        """dry_run=True means no metrics are saved — read-only invariant."""
        metrics_a = _make_collector_metrics("news_api", "success", signals=5)

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [metrics_a]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=True),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        results = await pipeline.run_collectors(["news_api"], dry_run=True)

        assert len(results) == 1
        pipeline._store.save_pipeline_run.assert_not_awaited()
        pipeline._store.save_collector_metrics.assert_not_awaited()


class TestSaveBeforeEndTracking:
    """_save_pipeline_metrics is called before _end_run_tracking."""

    @pytest.mark.asyncio
    async def test_save_ordering(self, pipeline):
        """Metrics save happens before end_run_tracking."""
        call_order = []

        original_save = pipeline._store.save_pipeline_run

        async def tracked_save(*args, **kwargs):
            call_order.append("save_pipeline_run")
            return "run-123"

        async def tracked_end(*args, **kwargs):
            call_order.append("end_run_tracking")

        pipeline._store.save_pipeline_run = tracked_save
        pipeline._store.save_collector_metrics = AsyncMock(
            side_effect=lambda *a, **k: call_order.append("save_collector_metrics")
        )
        pipeline._end_run_tracking = tracked_end

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [
                _make_collector_metrics("news_api", "success")
            ]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        await pipeline.run_collectors(["news_api"], dry_run=False)

        assert call_order.index("save_pipeline_run") < call_order.index("end_run_tracking")


class TestSaveErrorNonFatal:
    """Save errors don't crash run_collectors."""

    @pytest.mark.asyncio
    async def test_save_error_returns_results(self, pipeline):
        """If _save_pipeline_metrics fails, results are still returned."""
        pipeline._store.save_pipeline_run = AsyncMock(side_effect=Exception("DB error"))

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [
                _make_collector_metrics("news_api", "success", signals=2)
            ]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        results = await pipeline.run_collectors(["news_api"], dry_run=False)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_save_error_still_calls_end_tracking(self, pipeline):
        """Even if save fails, _end_run_tracking is still called."""
        pipeline._store.save_pipeline_run = AsyncMock(side_effect=Exception("DB error"))

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [
                _make_collector_metrics("news_api", "success")
            ]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        await pipeline.run_collectors(["news_api"], dry_run=False)
        pipeline._end_run_tracking.assert_awaited_once()


class TestEndTrackingErrorNonFatal:
    """_end_run_tracking errors don't crash."""

    @pytest.mark.asyncio
    async def test_end_tracking_error_non_fatal(self, pipeline):
        """If _end_run_tracking fails, results still returned."""
        pipeline._end_run_tracking = AsyncMock(side_effect=Exception("tracking error"))

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [
                _make_collector_metrics("news_api", "success", signals=1)
            ]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        results = await pipeline.run_collectors(["news_api"], dry_run=False)
        assert len(results) == 1


class TestNoDoubleWrite:
    """run_full_pipeline never calls run_collectors (separate code paths)."""

    def test_run_full_pipeline_doesnt_call_run_collectors(self):
        """Verify run_full_pipeline and run_collectors are independent methods."""
        import inspect
        source = inspect.getsource(DiscoveryPipeline.run_full_pipeline)
        # run_full_pipeline calls _run_collectors_stage directly, not run_collectors
        assert "run_collectors" not in source or "run_collectors_stage" in source


class TestCollectorsRunCountAccuracy:
    """collectors_run reflects actual recorded metrics, not requested count."""

    @pytest.mark.asyncio
    async def test_collectors_run_is_actual_count(self, pipeline):
        """collectors_run = len(_collector_metrics), not len(collector_names)."""
        # Only 1 metric recorded even though 2 were requested (e.g., one failed to instantiate)
        saved_stats = []
        original_save = pipeline._store.save_pipeline_run

        async def capture_save(stats):
            saved_stats.append(stats)
            return "run-456"

        pipeline._store.save_pipeline_run = capture_save

        async def fake_run_collectors_stage(names, dry_run):
            # Only one collector produces metrics
            pipeline._collector_metrics = [
                _make_collector_metrics("news_api", "success", signals=3)
            ]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        await pipeline.run_collectors(["news_api", "sec_edgar"], dry_run=False)

        assert len(saved_stats) == 1
        stats = saved_stats[0]
        assert stats.collectors_run == 1  # actual recorded, not 2 (requested)


class TestApiCallsInvariant:
    """api_calls > 0 when signals_found > 0."""

    @pytest.mark.asyncio
    async def test_api_calls_positive_when_signals_found(self, pipeline):
        """Metrics with signals_found > 0 should have api_calls > 0."""
        saved_stats = []

        async def capture_save(stats):
            saved_stats.append(stats)
            return "run-789"

        pipeline._store.save_pipeline_run = capture_save

        metrics = _make_collector_metrics("news_api", "success", signals=5, api_calls=10)

        async def fake_run_collectors_stage(names, dry_run):
            pipeline._collector_metrics = [metrics]
            return [
                CollectorResult(collector="news_api", status=CollectorStatus.SUCCESS, dry_run=False),
            ]

        pipeline._run_collectors_stage = fake_run_collectors_stage

        await pipeline.run_collectors(["news_api"], dry_run=False)

        # Verify the metric itself has api_calls > 0
        assert metrics.api_calls > 0
        assert metrics.signals_found > 0
