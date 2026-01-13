"""Tests for CollectorMetrics dataclass and storage."""

from datetime import datetime, timezone

import pytest

from storage.signal_store import SignalStore
from workflows.pipeline import CollectorMetrics


@pytest.fixture
async def store(tmp_path):
    """Create a SignalStore with temp database."""
    db_path = str(tmp_path / "test.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()
    yield store
    await store.close()


class TestCollectorMetricsTable:
    """Tests for collector_metrics table in storage."""

    @pytest.mark.asyncio
    async def test_collector_metrics_table_exists(self, store):
        """Verify collector_metrics table is created."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='collector_metrics'"
        )
        row = await cursor.fetchone()
        assert row is not None, "collector_metrics table should exist"


def test_collector_metrics_dataclass():
    """Verify CollectorMetrics dataclass has expected fields."""
    metrics = CollectorMetrics(
        collector_name="github",
        started_at=datetime.now(timezone.utc),
    )
    assert metrics.collector_name == "github"
    assert metrics.status == "pending"
    assert metrics.api_calls == 0
    assert metrics.retries == 0
    assert metrics.rate_limit_hits == 0
    assert metrics.errors == 0
    assert metrics.error_messages == []


def test_collector_metrics_complete():
    """Verify complete() sets completed_at and calculates duration."""
    start = datetime.now(timezone.utc)
    metrics = CollectorMetrics(collector_name="github", started_at=start)
    metrics.complete()
    assert metrics.completed_at is not None
    assert metrics.duration_seconds >= 0
