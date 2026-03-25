"""Tests for --source-api filter threading through process_pending pipeline.

Verifies that source_api parameter threads from:
  process_pending(source_api=X) -> _process_signals_stage(source_api=X) -> get_pending_signals(source_api=X)
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from workflows.pipeline import DiscoveryPipeline, PipelineConfig


@pytest.mark.asyncio
async def test_process_pending_threads_source_api_to_get_pending_signals():
    """process_pending(source_api='hacker_news') should pass source_api to get_pending_signals."""
    config = PipelineConfig()
    pipeline = DiscoveryPipeline(config)

    # Mock initialize to avoid DB setup
    pipeline.initialize = AsyncMock()

    # Mock _begin_run_tracking / _end_run_tracking
    pipeline._begin_run_tracking = AsyncMock()
    pipeline._end_run_tracking = AsyncMock()

    # Mock store with get_pending_signals that returns empty (no signals to process)
    mock_store = AsyncMock()
    mock_store.get_pending_signals = AsyncMock(return_value=[])
    pipeline._store = mock_store

    await pipeline.process_pending(dry_run=True, source_api="hacker_news")

    # Verify source_api was threaded all the way to get_pending_signals
    mock_store.get_pending_signals.assert_called_once_with(
        limit=config.batch_size,
        source_api="hacker_news",
    )


@pytest.mark.asyncio
async def test_process_pending_without_source_api_passes_none():
    """process_pending() without source_api should pass None to get_pending_signals."""
    config = PipelineConfig()
    pipeline = DiscoveryPipeline(config)

    pipeline.initialize = AsyncMock()
    pipeline._begin_run_tracking = AsyncMock()
    pipeline._end_run_tracking = AsyncMock()

    mock_store = AsyncMock()
    mock_store.get_pending_signals = AsyncMock(return_value=[])
    pipeline._store = mock_store

    await pipeline.process_pending(dry_run=True)

    mock_store.get_pending_signals.assert_called_once_with(
        limit=config.batch_size,
        source_api=None,
    )
