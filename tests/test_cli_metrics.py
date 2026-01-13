"""Tests for CLI metrics command."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def test_metrics_command_exists():
    """Verify metrics command is registered."""
    import run_pipeline

    # Get parser
    import argparse
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    # Manually check run_pipeline has metrics in its argument setup
    # by parsing the args
    import sys
    old_argv = sys.argv
    sys.argv = ["run_pipeline.py", "metrics"]
    try:
        args = run_pipeline.create_parser().parse_args()
        assert args.command == "metrics"
    finally:
        sys.argv = old_argv


def test_metrics_command_has_limit_option():
    """Verify metrics command accepts --limit option."""
    import run_pipeline
    import sys

    old_argv = sys.argv
    sys.argv = ["run_pipeline.py", "metrics", "--limit", "10"]
    try:
        args = run_pipeline.create_parser().parse_args()
        assert args.limit == 10
    finally:
        sys.argv = old_argv


def test_metrics_command_has_collector_option():
    """Verify metrics command accepts --collector option."""
    import run_pipeline
    import sys

    old_argv = sys.argv
    sys.argv = ["run_pipeline.py", "metrics", "--collector", "github"]
    try:
        args = run_pipeline.create_parser().parse_args()
        assert args.collector == "github"
    finally:
        sys.argv = old_argv


@pytest.mark.asyncio
async def test_cmd_metrics_displays_runs():
    """Verify cmd_metrics displays pipeline runs with collector breakdown."""
    import run_pipeline
    from io import StringIO

    # Mock the pipeline and store
    mock_store = AsyncMock()
    mock_pipeline = AsyncMock()
    mock_pipeline._store = mock_store
    mock_pipeline.initialize = AsyncMock()
    mock_pipeline.close = AsyncMock()

    # Mock data
    mock_store.get_pipeline_runs = AsyncMock(return_value=[
        {
            "run_id": "run-123",
            "started_at": "2026-01-13T14:32:01",
            "duration_seconds": 45.2,
            "collectors_run": 3,
            "signals_collected": 66,
        }
    ])
    mock_store.get_collector_metrics = AsyncMock(return_value=[
        {
            "collector_name": "github",
            "duration_seconds": 12.3,
            "signals_found": 42,
            "status": "success",
            "api_calls": 15,
            "retries": 0,
            "rate_limit_hits": 0,
        },
        {
            "collector_name": "sec_edgar",
            "duration_seconds": 28.1,
            "signals_found": 18,
            "status": "success",
            "api_calls": 8,
            "retries": 2,
            "rate_limit_hits": 0,
        },
    ])

    with patch.object(run_pipeline, 'DiscoveryPipeline') as MockPipeline:
        MockPipeline.return_value = mock_pipeline

        # Capture stdout
        captured = StringIO()
        with patch('sys.stdout', captured):
            args = MagicMock()
            args.limit = 5
            args.collector = None
            args.db_path = None
            await run_pipeline.cmd_metrics(args)

        output = captured.getvalue()
        assert "github" in output
        assert "sec_edgar" in output
        assert "42" in output  # signals
