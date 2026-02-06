"""Phase 3.1 — Pipeline scheduler tests (TDD RED).

Tests for:
- ScheduleConfig dataclass
- DB table creation (pipeline_schedules, pipeline_run_history)
- CRUD: create, list, get, update, pause, resume, delete
- Enqueue run (idempotent)
- Execute run (mocked pipeline)
- Run history recording
- Cron next-run calculation
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from ops.storage import OpsStorage
from ops.scheduler import (
    ScheduleConfig,
    PipelineScheduler,
    ScheduleStatus,
    RunStatus,
    RunRecord,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ops_db(tmp_path):
    db_path = tmp_path / "test_scheduler.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


@pytest.fixture
def scheduler(ops_db):
    return PipelineScheduler(ops_db)


# ---------------------------------------------------------------------------
# ScheduleConfig dataclass
# ---------------------------------------------------------------------------

class TestScheduleConfig:
    def test_defaults(self):
        cfg = ScheduleConfig(name="nightly", cron_expression="0 2 * * *")
        assert cfg.name == "nightly"
        assert cfg.cron_expression == "0 2 * * *"
        assert cfg.collectors == []
        assert cfg.mode == "full"
        assert cfg.dry_run is False
        assert cfg.enabled is True
        assert cfg.max_retries == 0

    def test_custom_fields(self):
        cfg = ScheduleConfig(
            name="github-only",
            cron_expression="0 */6 * * *",
            collectors=["github", "github_activity"],
            mode="collect",
            dry_run=True,
            enabled=False,
            max_retries=2,
        )
        assert cfg.collectors == ["github", "github_activity"]
        assert cfg.mode == "collect"
        assert cfg.dry_run is True
        assert cfg.enabled is False
        assert cfg.max_retries == 2


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

class TestTableCreation:
    def test_pipeline_schedules_table_exists(self, ops_db):
        """pipeline_schedules table should be created by PipelineScheduler."""
        sched = PipelineScheduler(ops_db)
        with ops_db.pool.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_schedules'"
            ).fetchone()
            assert row is not None

    def test_pipeline_run_history_table_exists(self, ops_db):
        """pipeline_run_history table should be created by PipelineScheduler."""
        sched = PipelineScheduler(ops_db)
        with ops_db.pool.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_run_history'"
            ).fetchone()
            assert row is not None


# ---------------------------------------------------------------------------
# CRUD: create_schedule
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    def test_create_returns_id(self, scheduler):
        cfg = ScheduleConfig(name="nightly", cron_expression="0 2 * * *")
        schedule_id = scheduler.create_schedule(cfg)
        assert isinstance(schedule_id, int)
        assert schedule_id > 0

    def test_create_persists_fields(self, scheduler):
        cfg = ScheduleConfig(
            name="github-hourly",
            cron_expression="0 */1 * * *",
            collectors=["github"],
            mode="collect",
            dry_run=True,
            max_retries=3,
        )
        sid = scheduler.create_schedule(cfg)
        result = scheduler.get_schedule(sid)
        assert result["name"] == "github-hourly"
        assert result["cron_expression"] == "0 */1 * * *"
        assert json.loads(result["collectors"]) == ["github"]
        assert result["mode"] == "collect"
        assert result["dry_run"] == 1  # SQLite boolean
        assert result["enabled"] == 1
        assert result["max_retries"] == 3

    def test_create_duplicate_name_fails(self, scheduler):
        cfg = ScheduleConfig(name="nightly", cron_expression="0 2 * * *")
        scheduler.create_schedule(cfg)
        with pytest.raises(Exception):  # UNIQUE constraint
            scheduler.create_schedule(cfg)


# ---------------------------------------------------------------------------
# CRUD: list_schedules
# ---------------------------------------------------------------------------

class TestListSchedules:
    def test_list_empty(self, scheduler):
        schedules = scheduler.list_schedules()
        assert schedules == []

    def test_list_returns_all(self, scheduler):
        scheduler.create_schedule(ScheduleConfig(name="a", cron_expression="0 1 * * *"))
        scheduler.create_schedule(ScheduleConfig(name="b", cron_expression="0 2 * * *"))
        schedules = scheduler.list_schedules()
        assert len(schedules) == 2
        names = {s["name"] for s in schedules}
        assert names == {"a", "b"}


# ---------------------------------------------------------------------------
# CRUD: get_schedule
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_get_existing(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 3 * * *")
        )
        result = scheduler.get_schedule(sid)
        assert result is not None
        assert result["id"] == sid
        assert result["name"] == "test"

    def test_get_nonexistent_returns_none(self, scheduler):
        result = scheduler.get_schedule(9999)
        assert result is None


# ---------------------------------------------------------------------------
# CRUD: update_schedule
# ---------------------------------------------------------------------------

class TestUpdateSchedule:
    def test_update_cron(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        scheduler.update_schedule(sid, cron_expression="0 4 * * *")
        result = scheduler.get_schedule(sid)
        assert result["cron_expression"] == "0 4 * * *"

    def test_update_collectors(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        scheduler.update_schedule(sid, collectors=["sec_edgar", "domain_whois"])
        result = scheduler.get_schedule(sid)
        assert json.loads(result["collectors"]) == ["sec_edgar", "domain_whois"]

    def test_update_nonexistent_raises(self, scheduler):
        with pytest.raises(ValueError, match="not found"):
            scheduler.update_schedule(9999, cron_expression="0 1 * * *")


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------

class TestPauseResume:
    def test_pause(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        scheduler.pause_schedule(sid)
        result = scheduler.get_schedule(sid)
        assert result["enabled"] == 0

    def test_resume(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        scheduler.pause_schedule(sid)
        scheduler.resume_schedule(sid)
        result = scheduler.get_schedule(sid)
        assert result["enabled"] == 1

    def test_pause_nonexistent_raises(self, scheduler):
        with pytest.raises(ValueError, match="not found"):
            scheduler.pause_schedule(9999)

    def test_resume_nonexistent_raises(self, scheduler):
        with pytest.raises(ValueError, match="not found"):
            scheduler.resume_schedule(9999)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDeleteSchedule:
    def test_delete(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        scheduler.delete_schedule(sid)
        assert scheduler.get_schedule(sid) is None

    def test_delete_nonexistent_raises(self, scheduler):
        with pytest.raises(ValueError, match="not found"):
            scheduler.delete_schedule(9999)


# ---------------------------------------------------------------------------
# Next run calculation (cron)
# ---------------------------------------------------------------------------

class TestNextRun:
    def test_next_run_returns_datetime(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        next_run = scheduler.get_next_run(sid)
        assert isinstance(next_run, datetime)
        assert next_run > datetime.now(timezone.utc)

    def test_next_run_disabled_returns_none(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        scheduler.pause_schedule(sid)
        next_run = scheduler.get_next_run(sid)
        assert next_run is None


# ---------------------------------------------------------------------------
# should_run (check if a schedule is due)
# ---------------------------------------------------------------------------

class TestShouldRun:
    def test_should_run_enabled_schedule(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")  # every minute
        )
        # Should be due (every minute cron)
        assert scheduler.should_run(sid) is True

    def test_should_not_run_disabled(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )
        scheduler.pause_schedule(sid)
        assert scheduler.should_run(sid) is False

    def test_should_not_run_already_ran_this_slot(self, scheduler):
        """If a run was recorded for the current cron slot, don't run again."""
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )
        # Record a run for now
        scheduler.record_run(
            schedule_id=sid,
            status=RunStatus.SUCCESS,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        assert scheduler.should_run(sid) is False


# ---------------------------------------------------------------------------
# Enqueue run (idempotent)
# ---------------------------------------------------------------------------

class TestEnqueueRun:
    def test_enqueue_creates_run(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )
        run_id = scheduler.enqueue_run(sid)
        assert isinstance(run_id, int)
        assert run_id > 0

    def test_enqueue_idempotent(self, scheduler):
        """Second enqueue for same schedule+slot returns same run_id."""
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )
        run1 = scheduler.enqueue_run(sid)
        run2 = scheduler.enqueue_run(sid)
        assert run1 == run2

    def test_enqueue_disabled_raises(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )
        scheduler.pause_schedule(sid)
        with pytest.raises(ValueError, match="disabled"):
            scheduler.enqueue_run(sid)


# ---------------------------------------------------------------------------
# Record run / get history
# ---------------------------------------------------------------------------

class TestRunHistory:
    def test_record_run(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        now = datetime.now(timezone.utc)
        run_id = scheduler.record_run(
            schedule_id=sid,
            status=RunStatus.SUCCESS,
            started_at=now - timedelta(seconds=30),
            finished_at=now,
            signals_found=10,
            signals_processed=8,
            signals_pushed=5,
            errors=0,
            cost=0.25,
        )
        assert isinstance(run_id, int)

    def test_get_run_history(self, scheduler):
        # Use hourly cron so runs in different hours get unique idempotency keys
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 * * * *")
        )
        now = datetime.now(timezone.utc)
        scheduler.record_run(
            schedule_id=sid,
            status=RunStatus.SUCCESS,
            started_at=now - timedelta(hours=1),
            finished_at=now,
            signals_found=10,
        )
        scheduler.record_run(
            schedule_id=sid,
            status=RunStatus.FAILED,
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, seconds=1),
            error_message="timeout",
        )

        history = scheduler.get_run_history(sid, limit=10)
        assert len(history) == 2
        # Most recent first
        assert history[0]["status"] == RunStatus.SUCCESS.value
        assert history[1]["status"] == RunStatus.FAILED.value

    def test_get_run_history_empty(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        history = scheduler.get_run_history(sid, limit=10)
        assert history == []

    def test_get_run_history_limit(self, scheduler):
        # Use hourly cron so each run lands in a different slot
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 * * * *")
        )
        now = datetime.now(timezone.utc)
        for i in range(5):
            scheduler.record_run(
                schedule_id=sid,
                status=RunStatus.SUCCESS,
                started_at=now - timedelta(hours=i + 1),
                finished_at=now - timedelta(hours=i),
            )
        history = scheduler.get_run_history(sid, limit=3)
        assert len(history) == 3


# ---------------------------------------------------------------------------
# Execute run (integration with pipeline - mocked)
# ---------------------------------------------------------------------------

class TestExecuteRun:
    @pytest.mark.asyncio
    async def test_execute_run_success(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(
                name="test",
                cron_expression="* * * * *",
                collectors=["github"],
                mode="full",
            )
        )

        # Mock the pipeline
        mock_stats = MagicMock()
        mock_stats.signals_collected = 10
        mock_stats.signals_processed = 8
        mock_stats.prospects_created = 5
        mock_stats.collectors_failed = 0

        with patch("workflows.pipeline.DiscoveryPipeline") as MockPipeline:
            mock_pipeline = AsyncMock()
            mock_pipeline.run_full_pipeline.return_value = mock_stats
            MockPipeline.return_value = mock_pipeline

            result = await scheduler.execute_run(sid)

        assert result["status"] == RunStatus.SUCCESS.value
        assert result["signals_found"] == 10
        assert result["signals_processed"] == 8
        assert result["signals_pushed"] == 5

    @pytest.mark.asyncio
    async def test_execute_run_records_failure(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )

        with patch("workflows.pipeline.DiscoveryPipeline") as MockPipeline:
            mock_pipeline = AsyncMock()
            mock_pipeline.run_full_pipeline.side_effect = RuntimeError("boom")
            MockPipeline.return_value = mock_pipeline

            result = await scheduler.execute_run(sid)

        assert result["status"] == RunStatus.FAILED.value
        assert "boom" in result["error_message"]

        # Verify recorded in history
        history = scheduler.get_run_history(sid, limit=1)
        assert len(history) == 1
        assert history[0]["status"] == RunStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_execute_run_disabled_raises(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="* * * * *")
        )
        scheduler.pause_schedule(sid)

        with pytest.raises(ValueError, match="disabled"):
            await scheduler.execute_run(sid)


# ---------------------------------------------------------------------------
# get_schedule_status (summary with next_run + last_run)
# ---------------------------------------------------------------------------

class TestGetScheduleStatus:
    def test_status_no_runs(self, scheduler):
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 2 * * *")
        )
        status = scheduler.get_schedule_status(sid)
        assert status["name"] == "test"
        assert status["enabled"] is True
        assert status["last_run"] is None
        assert status["next_run"] is not None
        assert status["total_runs"] == 0
        assert status["success_rate"] == 0.0

    def test_status_with_runs(self, scheduler):
        # Use hourly cron so runs in different hours get unique slots
        sid = scheduler.create_schedule(
            ScheduleConfig(name="test", cron_expression="0 * * * *")
        )
        now = datetime.now(timezone.utc)
        scheduler.record_run(
            schedule_id=sid,
            status=RunStatus.SUCCESS,
            started_at=now - timedelta(hours=1),
            finished_at=now,
            signals_found=10,
        )
        scheduler.record_run(
            schedule_id=sid,
            status=RunStatus.FAILED,
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, seconds=1),
        )

        status = scheduler.get_schedule_status(sid)
        assert status["total_runs"] == 2
        assert status["success_rate"] == 50.0
        assert status["last_run"] is not None
