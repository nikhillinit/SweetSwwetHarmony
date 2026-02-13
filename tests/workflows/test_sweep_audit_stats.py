"""Tests for sweep audit_log entries and PipelineStats identity counters.

Covers:
  - _run_promotion_sweep populates PipelineStats.sweep_* fields
  - _write_sweep_audit writes audit_log entry on success
  - _write_sweep_audit writes audit_log entry on failure
  - save_pipeline_run persists identity_stats JSON
  - v30 migration adds identity_stats column
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest import mock

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.entity_identity_store import EntityIdentityStore
from workflows.pipeline import PipelineStats


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with all migrations (including v30)."""
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


@pytest_asyncio.fixture
async def wired_store():
    """SignalStore with identity_store and use_thin_files enabled."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    s = SignalStore(db_path=path, use_thin_files=True)
    await s.initialize()
    id_store = EntityIdentityStore(s)
    s._identity_store = id_store

    yield s

    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# V30 MIGRATION
# =============================================================================

class TestV30Migration:
    """v30 migration adds identity_stats column to pipeline_runs."""

    @pytest.mark.asyncio
    async def test_identity_stats_column_exists(self, store):
        """After migrations, pipeline_runs has identity_stats column."""
        cursor = await store._db.execute("PRAGMA table_info(pipeline_runs)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "identity_stats" in columns

    @pytest.mark.asyncio
    async def test_schema_version_is_30(self, store):
        """Schema version is >= 30 with v30 migration applied."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION, MIGRATIONS
        assert CURRENT_SCHEMA_VERSION >= 30, f"v30 migration requires schema >= 30, got {CURRENT_SCHEMA_VERSION}"
        assert 30 in MIGRATIONS, "v30 migration missing from MIGRATIONS dict"


# =============================================================================
# PIPELINE STATS FIELDS
# =============================================================================

class TestPipelineStatsIdentityFields:
    """PipelineStats has identity/sweep fields with correct defaults."""

    def test_sweep_fields_default_zero(self):
        stats = PipelineStats()
        assert stats.sweep_promoted == 0
        assert stats.sweep_evaluated == 0
        assert stats.sweep_pages == 0
        assert stats.sweep_error is None


# =============================================================================
# SAVE_PIPELINE_RUN WITH IDENTITY_STATS
# =============================================================================

class TestSavePipelineRunIdentityStats:
    """save_pipeline_run persists identity_stats JSON."""

    @pytest.mark.asyncio
    async def test_save_includes_identity_stats(self, store):
        """save_pipeline_run stores sweep counters in identity_stats column."""
        stats = PipelineStats()
        stats.sweep_promoted = 5
        stats.sweep_pages = 2
        stats.sweep_error = None
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        cursor = await store._db.execute(
            "SELECT identity_stats FROM pipeline_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        data = json.loads(row[0])
        assert data["sweep_promoted"] == 5
        assert data["sweep_pages"] == 2
        assert data["sweep_error"] is None

    @pytest.mark.asyncio
    async def test_save_includes_sweep_error(self, store):
        """save_pipeline_run records sweep_error when present."""
        stats = PipelineStats()
        stats.sweep_error = "DB locked"
        stats.complete()

        run_id = await store.save_pipeline_run(stats)

        cursor = await store._db.execute(
            "SELECT identity_stats FROM pipeline_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        data = json.loads(row[0])
        assert data["sweep_error"] == "DB locked"


# =============================================================================
# SWEEP AUDIT LOG ENTRY
# =============================================================================

class TestSweepAuditLog:
    """_write_sweep_audit writes audit_log entries."""

    @pytest.mark.asyncio
    async def test_sweep_writes_audit_on_success(self, wired_store):
        """Successful promotion sweep writes audit_log entry."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        # Insert two signals from different sources to trigger promotion
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:audit-test.com",
            confidence=0.8,
            raw_data={},
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await wired_store.save_signal(
            signal_type="test",
            source_api="sec_edgar",
            canonical_key="domain:audit-test.com",
            confidence=0.7,
            raw_data={},
            detected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        # Create pipeline with our store pre-wired
        config = PipelineConfig(use_thin_files=True)
        pipeline = DiscoveryPipeline(config)
        pipeline._store = wired_store

        stats = PipelineStats()
        await pipeline._run_promotion_sweep(stats)

        # Verify audit_log entry
        cursor = await wired_store._db.execute(
            "SELECT action_type, entity_type, actor, details "
            "FROM audit_log WHERE action_type = 'promotion_sweep'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "promotion_sweep"
        assert row[1] == "pipeline"
        assert row[2] == "pipeline"
        details = json.loads(row[3])
        assert details["promoted"] == 1
        assert details["pages"] >= 1
        assert details["error"] is None

        # Verify stats were populated
        assert stats.sweep_promoted == 1
        assert stats.sweep_pages >= 1

    @pytest.mark.asyncio
    async def test_sweep_writes_audit_on_failure(self, wired_store):
        """Failed promotion sweep writes audit_log with error."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from workflows import thin_file_manager

        config = PipelineConfig(use_thin_files=True)
        pipeline = DiscoveryPipeline(config)
        pipeline._store = wired_store

        # Force sweep to fail
        original = thin_file_manager.run_promotion_sweep

        async def failing_sweep(*args, **kwargs):
            raise RuntimeError("test DB error")

        stats = PipelineStats()
        with mock.patch(
            "workflows.pipeline.run_promotion_sweep", failing_sweep
        ):
            await pipeline._run_promotion_sweep(stats)

        # Verify error audit entry
        cursor = await wired_store._db.execute(
            "SELECT details FROM audit_log WHERE action_type = 'promotion_sweep'"
        )
        row = await cursor.fetchone()
        assert row is not None
        details = json.loads(row[0])
        assert details["error"] == "test DB error"
        assert details["promoted"] == 0

        # Verify stats
        assert stats.sweep_error == "test DB error"

    @pytest.mark.asyncio
    async def test_sweep_no_promotions_still_writes_audit(self, wired_store):
        """Sweep with zero promotions still writes an audit entry."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(use_thin_files=True)
        pipeline = DiscoveryPipeline(config)
        pipeline._store = wired_store

        stats = PipelineStats()
        await pipeline._run_promotion_sweep(stats)

        cursor = await wired_store._db.execute(
            "SELECT details FROM audit_log WHERE action_type = 'promotion_sweep'"
        )
        row = await cursor.fetchone()
        assert row is not None
        details = json.loads(row[0])
        assert details["promoted"] == 0
        assert details["error"] is None
