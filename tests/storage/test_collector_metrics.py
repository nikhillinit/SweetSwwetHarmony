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


class TestSaveCollectorMetrics:
    """Tests for save_collector_metrics() method."""

    @pytest.mark.asyncio
    async def test_save_collector_metrics(self, store):
        """Verify collector metrics are saved to database."""
        metrics = CollectorMetrics(
            collector_name="github",
            started_at=datetime.now(timezone.utc),
            signals_found=42,
            status="success",
            api_calls=15,
            retries=2,
        )
        metrics.complete()

        await store.save_collector_metrics("test-run-123", metrics)

        # Verify saved
        cursor = await store._db.execute(
            "SELECT collector_name, signals_found, api_calls, retries FROM collector_metrics WHERE run_id = ?",
            ("test-run-123",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "github"
        assert row[1] == 42
        assert row[2] == 15
        assert row[3] == 2


class TestGetCollectorMetrics:
    """Tests for get_collector_metrics() method."""

    @pytest.mark.asyncio
    async def test_get_collector_metrics_by_run(self, store):
        """Verify we can query collector metrics by run_id."""
        # Save metrics for two collectors
        for name, signals in [("github", 42), ("sec_edgar", 18)]:
            metrics = CollectorMetrics(
                collector_name=name,
                started_at=datetime.now(timezone.utc),
                signals_found=signals,
                status="success",
            )
            metrics.complete()
            await store.save_collector_metrics("run-abc", metrics)

        # Query
        results = await store.get_collector_metrics(run_id="run-abc")

        assert len(results) == 2
        # Check both collectors are present (order may vary based on timing)
        collector_names = {r["collector_name"] for r in results}
        assert collector_names == {"github", "sec_edgar"}

    @pytest.mark.asyncio
    async def test_get_collector_metrics_by_collector_name(self, store):
        """Verify we can filter by collector name."""
        # Save metrics for multiple collectors and runs
        for run_id, name, signals in [
            ("run-1", "github", 10),
            ("run-1", "sec_edgar", 20),
            ("run-2", "github", 15),
        ]:
            metrics = CollectorMetrics(
                collector_name=name,
                started_at=datetime.now(timezone.utc),
                signals_found=signals,
                status="success",
            )
            metrics.complete()
            await store.save_collector_metrics(run_id, metrics)

        # Query only github
        results = await store.get_collector_metrics(collector_name="github")

        assert len(results) == 2
        assert all(r["collector_name"] == "github" for r in results)

    @pytest.mark.asyncio
    async def test_get_collector_metrics_empty(self, store):
        """Verify empty result when no metrics exist."""
        results = await store.get_collector_metrics(run_id="nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_get_collector_metrics_limit(self, store):
        """Verify limit parameter works."""
        # Save many metrics
        for i in range(10):
            metrics = CollectorMetrics(
                collector_name=f"collector_{i}",
                started_at=datetime.now(timezone.utc),
                signals_found=i,
                status="success",
            )
            metrics.complete()
            await store.save_collector_metrics(f"run-{i}", metrics)

        # Query with limit
        results = await store.get_collector_metrics(limit=3)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_collector_metrics_returns_all_fields(self, store):
        """Verify all expected fields are returned."""
        metrics = CollectorMetrics(
            collector_name="github",
            started_at=datetime.now(timezone.utc),
            signals_found=42,
            status="success",
            api_calls=15,
            rate_limit_hits=3,
            retries=2,
            errors=1,
            error_messages=["Error 1", "Error 2"],
        )
        metrics.complete()
        await store.save_collector_metrics("run-fields", metrics)

        results = await store.get_collector_metrics(run_id="run-fields")

        assert len(results) == 1
        result = results[0]
        assert result["run_id"] == "run-fields"
        assert result["collector_name"] == "github"
        assert result["signals_found"] == 42
        assert result["status"] == "success"
        assert result["api_calls"] == 15
        assert result["rate_limit_hits"] == 3
        assert result["retries"] == 2
        assert result["errors"] == 1
        assert result["error_messages"] == ["Error 1", "Error 2"]
        assert result["started_at"] is not None
        assert result["completed_at"] is not None
        assert result["duration_seconds"] is not None
