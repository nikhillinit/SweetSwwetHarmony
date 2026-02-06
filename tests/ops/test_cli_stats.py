"""Tests for ops CLI stats command.

Verifies stats output and no nested-transaction lock issues.
"""

import asyncio
import io
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from ops.storage import OpsStorage


@pytest.fixture
def stats_db(tmp_path):
    """Isolated DB with ops tables for stats testing."""
    db_path = tmp_path / "stats_test.db"

    from storage.signal_store import SignalStore

    store = SignalStore(str(db_path))
    asyncio.get_event_loop().run_until_complete(store.initialize())

    ops = OpsStorage(str(db_path))
    yield {"path": str(db_path), "ops": ops, "signal_store": store}

    asyncio.get_event_loop().run_until_complete(store.close())


def _run_stats(db_path: str) -> str:
    """Run the stats CLI function and capture stdout."""
    from ops.cli import stats

    class Args:
        db = db_path

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        stats(Args())
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


def test_stats_empty_db(stats_db):
    """Stats runs without error on an empty database."""
    output = _run_stats(stats_db["path"])

    assert "FACT STATISTICS" in output
    assert "USAGE STATISTICS" in output
    assert "ACTION STATE STATISTICS" in output
    assert "LAST 7 DAYS EXTRACTION" in output
    assert "No extraction runs in last 7 days" in output
    # Must NOT contain the old nested-transaction error
    assert "database is locked" not in output


def test_stats_with_facts(stats_db):
    """Stats correctly reports fact counts after inserting data."""
    ops = stats_db["ops"]

    with ops.transaction() as conn:
        conn.execute(
            """
            INSERT INTO memory_facts (type, content, confidence, status, used_count)
            VALUES ('constraint', 'B2B SaaS is excluded', 0.95, 'active', 3)
            """
        )
        conn.execute(
            """
            INSERT INTO memory_facts (type, content, confidence, status, used_count)
            VALUES ('nuance', 'Pet food is consumer CPG', 0.80, 'pending', 0)
            """
        )

    output = _run_stats(stats_db["path"])

    assert "ACTIVE" in output
    assert "PENDING" in output
    assert "database is locked" not in output


def test_stats_with_health(stats_db):
    """Stats shows health section when health data exists."""
    ops = stats_db["ops"]

    ops.log_health("github_collector", "healthy", latency_ms=120.5)
    ops.log_health("thesis_matcher", "degraded", latency_ms=500.0, error="slow")

    output = _run_stats(stats_db["path"])

    assert "SYSTEM HEALTH" in output
    assert "github_collector" in output
    assert "database is locked" not in output


def test_stats_with_extraction_runs(stats_db):
    """Stats shows extraction run history."""
    ops = stats_db["ops"]

    with ops.transaction() as conn:
        conn.execute(
            """
            INSERT INTO extraction_runs
            (decisions_processed, facts_created, llm_failures, duration_seconds, estimated_cost)
            VALUES (5, 3, 0, 12.5, 0.0015)
            """
        )

    output = _run_stats(stats_db["path"])

    assert "LAST 7 DAYS EXTRACTION" in output
    assert "3 facts from 5 decisions" in output
    assert "database is locked" not in output
