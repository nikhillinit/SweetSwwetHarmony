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
