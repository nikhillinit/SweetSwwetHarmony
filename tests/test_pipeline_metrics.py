"""
Tests for pipeline metrics persistence.

TDD Phase: RED - These tests should FAIL initially.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from storage.signal_store import SignalStore
from workflows.pipeline import PipelineStats


class TestPipelineRunsTable:
    """Test that pipeline_runs table exists and has correct schema"""

    @pytest.mark.asyncio
    async def test_pipeline_runs_table_exists(self):
        """pipeline_runs table should exist after migration"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Query the table to ensure it exists
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_runs'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "pipeline_runs"

        await store.close()

    @pytest.mark.asyncio
    async def test_pipeline_runs_has_required_columns(self):
        """pipeline_runs table should have all required columns"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Get table info
        cursor = await store._db.execute("PRAGMA table_info(pipeline_runs)")
        columns = {row[1] for row in await cursor.fetchall()}

        required_columns = {
            "id",
            "run_id",
            "started_at",
            "completed_at",
            "duration_seconds",
            "collectors_run",
            "collectors_succeeded",
            "collectors_failed",
            "signals_collected",
            "signals_stored",
            "signals_deduplicated",
            "signals_processed",
            "signals_auto_push",
            "signals_needs_review",
            "signals_held",
            "signals_rejected",
            "prospects_created",
            "prospects_updated",
            "prospects_skipped",
            "errors",
            "health_report",
        }

        assert required_columns.issubset(columns)

        await store.close()


class TestSavePipelineRun:
    """Test save_pipeline_run() method"""

    @pytest.mark.asyncio
    async def test_save_pipeline_run_method_exists(self):
        """SignalStore should have save_pipeline_run method"""
        store = SignalStore(":memory:")
        assert hasattr(store, "save_pipeline_run")
        assert callable(store.save_pipeline_run)

    @pytest.mark.asyncio
    async def test_save_pipeline_run_returns_run_id(self):
        """save_pipeline_run should return a UUID run_id"""
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        assert run_id is not None
        assert isinstance(run_id, str)
        # Verify it's a valid UUID
        uuid.UUID(run_id)

        await store.close()

    @pytest.mark.asyncio
    async def test_save_pipeline_run_stores_all_fields(self):
        """save_pipeline_run should store all PipelineStats fields"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Create stats with specific values
        stats = PipelineStats()
        stats.collectors_run = 5
        stats.collectors_succeeded = 4
        stats.collectors_failed = 1
        stats.signals_collected = 150
        stats.signals_stored = 140
        stats.signals_deduplicated = 10
        stats.signals_processed = 130
        stats.signals_auto_push = 80
        stats.signals_needs_review = 30
        stats.signals_held = 15
        stats.signals_rejected = 5
        stats.prospects_created = 60
        stats.prospects_updated = 20
        stats.prospects_skipped = 10
        stats.errors = ["Test error 1", "Test error 2"]
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        # Retrieve and verify
        cursor = await store._db.execute(
            "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
        )
        row = await cursor.fetchone()

        assert row is not None
        # Column indices based on schema order
        # id=0, run_id=1, started_at=2, completed_at=3, duration_seconds=4...
        assert row[5] == 5  # collectors_run
        assert row[6] == 4  # collectors_succeeded
        assert row[7] == 1  # collectors_failed
        assert row[8] == 150  # signals_collected
        assert row[9] == 140  # signals_stored
        assert row[10] == 10  # signals_deduplicated
        assert row[11] == 130  # signals_processed
        assert row[12] == 80  # signals_auto_push
        assert row[13] == 30  # signals_needs_review
        assert row[14] == 15  # signals_held
        assert row[15] == 5  # signals_rejected
        assert row[16] == 60  # prospects_created
        assert row[17] == 20  # prospects_updated
        assert row[18] == 10  # prospects_skipped

        await store.close()

    @pytest.mark.asyncio
    async def test_save_pipeline_run_stores_timing(self):
        """save_pipeline_run should correctly store timing information"""
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        # Simulate a pipeline that took 5 seconds
        stats.started_at = datetime.now(timezone.utc)
        stats.completed_at = stats.started_at + timedelta(seconds=5)

        run_id = await store.save_pipeline_run(stats)

        # Retrieve and verify
        cursor = await store._db.execute(
            "SELECT started_at, completed_at, duration_seconds FROM pipeline_runs WHERE run_id = ?",
            (run_id,)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == stats.started_at.isoformat()
        assert row[1] == stats.completed_at.isoformat()
        assert abs(row[2] - 5.0) < 0.1  # Allow small floating point difference

        await store.close()

    @pytest.mark.asyncio
    async def test_save_pipeline_run_stores_errors_as_json(self):
        """save_pipeline_run should store errors list as JSON"""
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        stats.errors = ["Error 1: Connection failed", "Error 2: Timeout"]
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        # Retrieve and verify
        cursor = await store._db.execute(
            "SELECT errors FROM pipeline_runs WHERE run_id = ?", (run_id,)
        )
        row = await cursor.fetchone()

        assert row is not None
        import json
        errors = json.loads(row[0])
        assert errors == ["Error 1: Connection failed", "Error 2: Timeout"]

        await store.close()

    @pytest.mark.asyncio
    async def test_save_pipeline_run_handles_health_report(self):
        """save_pipeline_run should store health_report as JSON if present"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Create mock health report
        mock_health = MagicMock()
        mock_health.to_dict.return_value = {
            "overall_status": "HEALTHY",
            "signal_counts": {"github": 10, "sec_edgar": 5},
        }

        stats = PipelineStats()
        stats.health_report = mock_health
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        # Retrieve and verify
        cursor = await store._db.execute(
            "SELECT health_report FROM pipeline_runs WHERE run_id = ?", (run_id,)
        )
        row = await cursor.fetchone()

        assert row is not None
        import json
        health = json.loads(row[0])
        assert health["overall_status"] == "HEALTHY"
        assert health["signal_counts"]["github"] == 10

        await store.close()

    @pytest.mark.asyncio
    async def test_save_pipeline_run_handles_null_health_report(self):
        """save_pipeline_run should handle None health_report"""
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        stats.health_report = None
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        # Retrieve and verify
        cursor = await store._db.execute(
            "SELECT health_report FROM pipeline_runs WHERE run_id = ?", (run_id,)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] is None

        await store.close()


class TestGetPipelineRuns:
    """Test get_pipeline_runs() method"""

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_method_exists(self):
        """SignalStore should have get_pipeline_runs method"""
        store = SignalStore(":memory:")
        assert hasattr(store, "get_pipeline_runs")
        assert callable(store.get_pipeline_runs)

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_returns_list(self):
        """get_pipeline_runs should return a list"""
        store = SignalStore(":memory:")
        await store.initialize()

        runs = await store.get_pipeline_runs()
        assert isinstance(runs, list)

        await store.close()

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_returns_recent_first(self):
        """get_pipeline_runs should return runs in reverse chronological order"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Create 3 runs with different timestamps
        for i in range(3):
            stats = PipelineStats()
            stats.started_at = datetime.now(timezone.utc) + timedelta(hours=i)
            stats.complete()
            await store.save_pipeline_run(stats)

        runs = await store.get_pipeline_runs()

        assert len(runs) >= 3
        # Most recent should be first
        assert runs[0]["started_at"] > runs[1]["started_at"]
        assert runs[1]["started_at"] > runs[2]["started_at"]

        await store.close()

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_respects_limit(self):
        """get_pipeline_runs should respect the limit parameter"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Create 5 runs
        for _ in range(5):
            stats = PipelineStats()
            stats.complete()
            await store.save_pipeline_run(stats)

        # Get only 3
        runs = await store.get_pipeline_runs(limit=3)

        assert len(runs) == 3

        await store.close()

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_default_limit(self):
        """get_pipeline_runs should default to limit=10"""
        store = SignalStore(":memory:")
        await store.initialize()

        # Create 15 runs
        for _ in range(15):
            stats = PipelineStats()
            stats.complete()
            await store.save_pipeline_run(stats)

        # Default should be 10
        runs = await store.get_pipeline_runs()

        assert len(runs) == 10

        await store.close()

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_returns_dict_format(self):
        """get_pipeline_runs should return dicts with all fields"""
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        stats.collectors_run = 5
        stats.signals_collected = 100
        stats.complete()

        await store.save_pipeline_run(stats)

        runs = await store.get_pipeline_runs(limit=1)

        assert len(runs) == 1
        run = runs[0]

        # Check required fields
        assert "run_id" in run
        assert "started_at" in run
        assert "completed_at" in run
        assert "duration_seconds" in run
        assert "collectors_run" in run
        assert run["collectors_run"] == 5
        assert "signals_collected" in run
        assert run["signals_collected"] == 100

        await store.close()


class TestGetPipelineRun:
    """Test get_pipeline_run() method"""

    @pytest.mark.asyncio
    async def test_get_pipeline_run_method_exists(self):
        """SignalStore should have get_pipeline_run method"""
        store = SignalStore(":memory:")
        assert hasattr(store, "get_pipeline_run")
        assert callable(store.get_pipeline_run)

    @pytest.mark.asyncio
    async def test_get_pipeline_run_returns_specific_run(self):
        """get_pipeline_run should return a specific run by ID"""
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        stats.collectors_run = 7
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        # Retrieve by ID
        run = await store.get_pipeline_run(run_id)

        assert run is not None
        assert run["run_id"] == run_id
        assert run["collectors_run"] == 7

        await store.close()

    @pytest.mark.asyncio
    async def test_get_pipeline_run_returns_none_for_missing(self):
        """get_pipeline_run should return None if run_id doesn't exist"""
        store = SignalStore(":memory:")
        await store.initialize()

        run = await store.get_pipeline_run("nonexistent-uuid")

        assert run is None

        await store.close()

    @pytest.mark.asyncio
    async def test_get_pipeline_run_deserializes_json_fields(self):
        """get_pipeline_run should deserialize JSON fields (errors, health_report)"""
        store = SignalStore(":memory:")
        await store.initialize()

        mock_health = MagicMock()
        mock_health.to_dict.return_value = {"status": "HEALTHY"}

        stats = PipelineStats()
        stats.errors = ["Test error"]
        stats.health_report = mock_health
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        run = await store.get_pipeline_run(run_id)

        assert run is not None
        assert isinstance(run["errors"], list)
        assert run["errors"] == ["Test error"]
        assert isinstance(run["health_report"], dict)
        assert run["health_report"]["status"] == "HEALTHY"

        await store.close()


class TestPipelineIntegration:
    """Test integration with DiscoveryPipeline"""

    @pytest.mark.asyncio
    async def test_save_pipeline_run_called_in_pipeline(self):
        """Verify save_pipeline_run is called with the right signature"""
        from workflows.pipeline import PipelineStats
        from storage.signal_store import SignalStore

        # This is a simple signature test - just verify the method exists and works
        store = SignalStore(":memory:")
        await store.initialize()

        stats = PipelineStats()
        stats.complete()

        # Should be callable and return a run_id
        run_id = await store.save_pipeline_run(stats)
        assert isinstance(run_id, str)

        await store.close()

    @pytest.mark.asyncio
    async def test_pipeline_metrics_persistence_flow(self):
        """Test complete flow: save stats, retrieve them"""
        from workflows.pipeline import PipelineStats
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        # Create and save stats
        stats = PipelineStats()
        stats.collectors_run = 3
        stats.signals_collected = 50
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        # Retrieve and verify
        saved_run = await store.get_pipeline_run(run_id)
        assert saved_run is not None
        assert saved_run["run_id"] == run_id
        assert saved_run["collectors_run"] == 3
        assert saved_run["signals_collected"] == 50

        # Verify it appears in list
        runs = await store.get_pipeline_runs(limit=5)
        assert len(runs) >= 1
        assert any(r["run_id"] == run_id for r in runs)

        await store.close()
