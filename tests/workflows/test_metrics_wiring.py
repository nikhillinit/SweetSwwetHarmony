"""Tests for signals_stored / signals_deduplicated wiring in run_full_pipeline().

Verifies the fix that wires CollectorResult.signals_new and
CollectorResult.signals_suppressed into PipelineStats.signals_stored
and PipelineStats.signals_deduplicated.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from workflows.pipeline import DiscoveryPipeline, PipelineConfig, PipelineStats


def _make_result(
    name: str = "test",
    signals_found: int = 0,
    signals_new: int = 0,
    signals_suppressed: int = 0,
    status: CollectorStatus = CollectorStatus.SUCCESS,
) -> CollectorResult:
    return CollectorResult(
        collector=name,
        status=status,
        signals_found=signals_found,
        signals_new=signals_new,
        signals_suppressed=signals_suppressed,
        dry_run=False,
    )


def _process_stats(**overrides):
    base = {
        "processed": 0, "auto_push": 0, "needs_review": 0,
        "held": 0, "rejected": 0, "prospects_created": 0,
        "prospects_updated": 0, "prospects_skipped": 0,
        "schemas_extracted": 0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def pipeline():
    """Pipeline with all external deps mocked out."""
    config = PipelineConfig(db_path=":memory:")
    p = DiscoveryPipeline(config)
    # Prevent real initialization
    p._initialized = True
    p._store = MagicMock()
    p._store.save_pipeline_run = AsyncMock(return_value="run-test-1")
    p._store.save_collector_metrics = AsyncMock()
    p._health_monitor = None
    p._notifier = None
    p._collector_metrics = []
    return p


@pytest.mark.asyncio
async def test_signals_stored_wired(pipeline):
    """Single collector returning signals_new=5 -> stats.signals_stored=5."""
    results = [_make_result(signals_found=8, signals_new=5, signals_suppressed=3)]

    with (
        patch.object(pipeline, "initialize", new_callable=AsyncMock),
        patch.object(pipeline, "_begin_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_end_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_run_collectors_stage", new_callable=AsyncMock, return_value=results),
        patch.object(pipeline, "_process_signals_stage", new_callable=AsyncMock, return_value=_process_stats()),
        patch.object(pipeline, "_drain_notion_outbox", new_callable=AsyncMock, return_value={"processed": 0, "created": 0, "updated": 0, "skipped": 0}),
    ):
        stats = await pipeline.run_full_pipeline(collectors=["test"], dry_run=False)

    assert stats.signals_stored == 5
    assert stats.signals_deduplicated == 3


@pytest.mark.asyncio
async def test_signals_stored_sums_across_collectors(pipeline):
    """Two collector results -> signals_stored is the sum."""
    results = [
        _make_result("github", signals_found=10, signals_new=7, signals_suppressed=3),
        _make_result("rss_feeds", signals_found=5, signals_new=2, signals_suppressed=3),
    ]

    with (
        patch.object(pipeline, "initialize", new_callable=AsyncMock),
        patch.object(pipeline, "_begin_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_end_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_run_collectors_stage", new_callable=AsyncMock, return_value=results),
        patch.object(pipeline, "_process_signals_stage", new_callable=AsyncMock, return_value=_process_stats()),
        patch.object(pipeline, "_drain_notion_outbox", new_callable=AsyncMock, return_value={"processed": 0, "created": 0, "updated": 0, "skipped": 0}),
    ):
        stats = await pipeline.run_full_pipeline(collectors=["github", "rss_feeds"], dry_run=False)

    assert stats.signals_stored == 9
    assert stats.signals_deduplicated == 6


@pytest.mark.asyncio
async def test_signals_stored_zero_when_all_suppressed(pipeline):
    """All signals suppressed -> signals_stored=0, signals_deduplicated=N."""
    results = [_make_result(signals_found=10, signals_new=0, signals_suppressed=10)]

    with (
        patch.object(pipeline, "initialize", new_callable=AsyncMock),
        patch.object(pipeline, "_begin_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_end_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_run_collectors_stage", new_callable=AsyncMock, return_value=results),
        patch.object(pipeline, "_process_signals_stage", new_callable=AsyncMock, return_value=_process_stats()),
        patch.object(pipeline, "_drain_notion_outbox", new_callable=AsyncMock, return_value={"processed": 0, "created": 0, "updated": 0, "skipped": 0}),
    ):
        stats = await pipeline.run_full_pipeline(collectors=["test"], dry_run=False)

    assert stats.signals_stored == 0
    assert stats.signals_deduplicated == 10


@pytest.mark.asyncio
async def test_stats_persisted_to_db(pipeline):
    """save_pipeline_run receives stats with signals_stored > 0."""
    results = [_make_result(signals_found=8, signals_new=5, signals_suppressed=3)]

    with (
        patch.object(pipeline, "initialize", new_callable=AsyncMock),
        patch.object(pipeline, "_begin_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_end_run_tracking", new_callable=AsyncMock),
        patch.object(pipeline, "_run_collectors_stage", new_callable=AsyncMock, return_value=results),
        patch.object(pipeline, "_process_signals_stage", new_callable=AsyncMock, return_value=_process_stats()),
        patch.object(pipeline, "_drain_notion_outbox", new_callable=AsyncMock, return_value={"processed": 0, "created": 0, "updated": 0, "skipped": 0}),
    ):
        await pipeline.run_full_pipeline(collectors=["test"], dry_run=False)

    # save_pipeline_run should have been called with the stats object
    pipeline._store.save_pipeline_run.assert_called_once()
    saved_stats = pipeline._store.save_pipeline_run.call_args[0][0]
    assert isinstance(saved_stats, PipelineStats)
    assert saved_stats.signals_stored == 5
    assert saved_stats.signals_deduplicated == 3
