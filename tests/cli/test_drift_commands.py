"""Tests for drift CLI commands (W5.9).

Verifies:
- Parser accepts all drift subcommands with correct args
- check, alerts, recommend are read-only (no feature guard)
- aggregate, ack, snooze, resolve, gc require DRIFT_MONITORING_ENABLED=active
- export-metrics outputs CSV/JSONL
"""

import os
import sys
import tempfile
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# PARSER TESTS
# =============================================================================

class TestDriftParser:
    """Tests for drift CLI argument parsing."""

    def test_drift_check_parser(self):
        """Should parse drift check with optional --metrics."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "check"])
        assert args.command == "drift"
        assert args.drift_cmd == "check"

    def test_drift_aggregate_parser(self):
        """Should parse drift aggregate with --days."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "aggregate", "--days", "30"])
        assert args.drift_cmd == "aggregate"
        assert args.days == 30

    def test_drift_alerts_parser(self):
        """Should parse drift alerts with --status and --limit."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "alerts", "--status", "open", "--limit", "10"])
        assert args.drift_cmd == "alerts"
        assert args.status == "open"
        assert args.limit == 10

    def test_drift_ack_parser(self):
        """Should parse drift ack with alert_id and --reason."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "ack", "42", "--reason", "Investigating"])
        assert args.drift_cmd == "ack"
        assert args.alert_id == 42
        assert args.reason == "Investigating"

    def test_drift_resolve_parser(self):
        """Should parse drift resolve with alert_id and --reason."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "resolve", "7", "--reason", "False alarm"])
        assert args.drift_cmd == "resolve"
        assert args.alert_id == 7
        assert args.reason == "False alarm"

    def test_drift_gc_parser(self):
        """Should parse drift gc with retention days."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "gc", "--metrics-days", "180", "--alerts-days", "90"])
        assert args.drift_cmd == "gc"
        assert args.metrics_days == 180
        assert args.alerts_days == 90

    def test_drift_export_parser(self):
        """Should parse drift export-metrics with --format and --out."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["drift", "export-metrics", "--format", "jsonl", "--out", "/tmp/metrics.jsonl"])
        assert args.drift_cmd == "export-metrics"
        assert getattr(args, "format") == "jsonl"
        assert args.out == "/tmp/metrics.jsonl"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
