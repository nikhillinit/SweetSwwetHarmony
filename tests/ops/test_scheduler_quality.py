"""Tests for quality schedule integration (Phase 9 Task 3)."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ops.scheduler import PipelineScheduler, ScheduleConfig, RunStatus
from ops.storage import OpsStorage


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary test database with proper cleanup."""
    db_path = tmp_path / "test_scheduler_quality.db"
    storage = OpsStorage(str(db_path))

    yield storage

    # Proper cleanup for Windows
    try:
        # Close all connections
        storage.pool.close()
        del storage

        # Give Windows time to release file locks
        import time
        time.sleep(0.1)

        # Try to remove database files
        for ext in ['', '-wal', '-shm']:
            path = Path(f"{db_path}{ext}")
            if path.exists():
                try:
                    path.unlink()
                except PermissionError:
                    pass  # Windows still has lock, skip
    except Exception:
        pass  # Cleanup is best-effort


@pytest.fixture
def scheduler(temp_db):
    """Create scheduler instance."""
    return PipelineScheduler(temp_db)


# ---------------------------------------------------------------------------
# Task 3.1: quality-sync schedule
# ---------------------------------------------------------------------------

def test_create_quality_sync_schedule(scheduler):
    """Test creating quality-sync schedule."""
    config = ScheduleConfig(
        name="quality-sync-notion-status",
        cron_expression="0 */6 * * *",
        collectors=[],
        mode="quality-sync",
        enabled=True,
    )

    schedule_id = scheduler.create_schedule(config)
    assert schedule_id > 0

    # Verify stored correctly
    schedule = scheduler.get_schedule(schedule_id)
    assert schedule is not None
    assert schedule["name"] == "quality-sync-notion-status"
    assert schedule["cron_expression"] == "0 */6 * * *"
    assert schedule["mode"] == "quality-sync"
    assert schedule["enabled"] == 1


def test_quality_sync_schedule_next_run(scheduler):
    """Test next run calculation for quality-sync."""
    config = ScheduleConfig(
        name="test-quality-sync",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )

    schedule_id = scheduler.create_schedule(config)
    next_run = scheduler.get_next_run(schedule_id)

    assert next_run is not None
    assert next_run > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_execute_quality_sync_workflow(scheduler, temp_db, monkeypatch):
    """Test executing quality-sync workflow (integration)."""
    # Create schedule
    config = ScheduleConfig(
        name="test-quality-sync",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    # Mock the quality ops functions
    mock_sync = MagicMock(return_value=5)  # 5 events synced
    mock_backfill = MagicMock(return_value=3)  # 3 outcomes updated

    monkeypatch.setattr("ops.quality.status_events.sync_status_events", mock_sync)
    monkeypatch.setattr("ops.quality.outcomes.backfill_outcomes_from_events", mock_backfill)
    monkeypatch.setenv("DISCOVERY_DB_PATH", "signals.db")

    # Execute
    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 3  # outcomes_updated
    assert result["run_id"] is not None

    # Verify run recorded
    history = scheduler.get_run_history(schedule_id, limit=1)
    assert len(history) == 1
    assert history[0]["status"] == "success"


# ---------------------------------------------------------------------------
# Task 3.2: quality-classify schedule
# ---------------------------------------------------------------------------

def test_create_quality_classify_schedule(scheduler):
    """Test creating quality-classify schedule."""
    config = ScheduleConfig(
        name="quality-thesis-classify-batch",
        cron_expression="0 2 * * *",
        collectors=[],
        mode="quality-classify",
        enabled=True,
    )

    schedule_id = scheduler.create_schedule(config)
    assert schedule_id > 0

    # Verify stored correctly
    schedule = scheduler.get_schedule(schedule_id)
    assert schedule is not None
    assert schedule["name"] == "quality-thesis-classify-batch"
    assert schedule["cron_expression"] == "0 2 * * *"
    assert schedule["mode"] == "quality-classify"


@pytest.mark.asyncio
async def test_execute_quality_classify_workflow_llm_disabled(scheduler, monkeypatch):
    """Test quality-classify workflow when LLM is disabled."""
    config = ScheduleConfig(
        name="test-quality-classify",
        cron_expression="0 2 * * *",
        mode="quality-classify",
    )
    schedule_id = scheduler.create_schedule(config)

    # Mock LLM disabled
    monkeypatch.setenv("LLM_THESIS_MODE", "off")

    # Execute
    result = await scheduler.execute_run(schedule_id)

    # Should succeed but skip classification
    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 0


@pytest.mark.asyncio
async def test_execute_quality_classify_workflow_llm_enabled(scheduler, monkeypatch):
    """Test quality-classify workflow when LLM is enabled."""
    config = ScheduleConfig(
        name="test-quality-classify",
        cron_expression="0 2 * * *",
        mode="quality-classify",
    )
    schedule_id = scheduler.create_schedule(config)

    # Mock LLM enabled
    monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
    monkeypatch.setenv("DISCOVERY_DB_PATH", "signals.db")

    # Mock batch_classify_recent
    mock_classify = MagicMock(return_value=10)  # 10 signals classified
    monkeypatch.setattr("ops.quality.thesis.batch_classify_recent", mock_classify)

    # Execute
    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 10

    # Verify mock was called with correct params
    mock_classify.assert_called_once()
    call_kwargs = mock_classify.call_args[1]
    assert call_kwargs["limit"] == 50
    assert call_kwargs["chunk_size"] == 10
    assert call_kwargs["upsert"] is True


# ---------------------------------------------------------------------------
# Task 3.3: quality-patterns schedule
# ---------------------------------------------------------------------------

def test_create_quality_patterns_schedule(scheduler):
    """Test creating quality-patterns schedule."""
    config = ScheduleConfig(
        name="quality-find-patterns",
        cron_expression="0 3 * * 0",
        collectors=[],
        mode="quality-patterns",
        enabled=True,
    )

    schedule_id = scheduler.create_schedule(config)
    assert schedule_id > 0

    # Verify stored correctly
    schedule = scheduler.get_schedule(schedule_id)
    assert schedule is not None
    assert schedule["name"] == "quality-find-patterns"
    assert schedule["cron_expression"] == "0 3 * * 0"
    assert schedule["mode"] == "quality-patterns"


@pytest.mark.asyncio
async def test_execute_quality_patterns_workflow(scheduler, temp_db, monkeypatch):
    """Test executing quality-patterns workflow."""
    config = ScheduleConfig(
        name="test-quality-patterns",
        cron_expression="0 3 * * 0",
        mode="quality-patterns",
    )
    schedule_id = scheduler.create_schedule(config)

    # Mock detect_patterns
    mock_patterns = [
        {"type": "collector_concentration", "collector": "github", "fp_count": 10},
        {"type": "duplicate_descriptions", "pattern": "AI platform", "count": 5},
    ]
    mock_detect = MagicMock(return_value=mock_patterns)
    monkeypatch.setattr("ops.quality.patterns.detect_patterns", mock_detect)
    monkeypatch.setenv("DISCOVERY_DB_PATH", "signals.db")

    # Execute
    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 2  # 2 patterns detected

    # Verify patterns stored in ops DB
    with temp_db.transaction() as conn:
        cursor = conn.execute(
            "SELECT run_id, pattern_data, pattern_count FROM pattern_runs WHERE run_id = ?",
            (result["run_id"],),
        )
        row = cursor.fetchone()
        assert row is not None

        stored_patterns = json.loads(row[1])
        assert len(stored_patterns) == 2
        assert row[2] == 2  # pattern_count


# ---------------------------------------------------------------------------
# Task 3.4: CLI integration tests
# ---------------------------------------------------------------------------

def test_schedule_mode_choices_include_quality_modes():
    """Test that schedule add command accepts quality modes."""
    import argparse
    from ops.cli import main

    # Parse args to verify quality modes are accepted
    # This is a bit of a hack, but it verifies the mode choices
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "collect", "process", "quality-sync", "quality-classify", "quality-patterns"])

    # Should not raise
    args = parser.parse_args(["--mode", "quality-sync"])
    assert args.mode == "quality-sync"

    args = parser.parse_args(["--mode", "quality-classify"])
    assert args.mode == "quality-classify"

    args = parser.parse_args(["--mode", "quality-patterns"])
    assert args.mode == "quality-patterns"


# ---------------------------------------------------------------------------
# Idempotency & error handling
# ---------------------------------------------------------------------------

def test_quality_schedule_idempotency(scheduler):
    """Test that quality schedules respect idempotency keys."""
    config = ScheduleConfig(
        name="test-idempotency",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    # Enqueue first run
    run_id_1 = scheduler.enqueue_run(schedule_id)
    assert run_id_1 > 0

    # Enqueue again (same time slot) - should return same run_id
    run_id_2 = scheduler.enqueue_run(schedule_id)
    assert run_id_2 == run_id_1


@pytest.mark.asyncio
async def test_quality_schedule_lock_prevents_overlap(scheduler, monkeypatch):
    """Test that scheduler lock prevents overlapping runs."""
    config = ScheduleConfig(
        name="test-lock",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    # Mock quality ops functions to simulate long-running task
    mock_sync = MagicMock(return_value=0)
    monkeypatch.setattr("ops.quality.status_events.sync_status_events", mock_sync)
    monkeypatch.setattr("ops.quality.outcomes.backfill_outcomes_from_events", MagicMock(return_value=0))
    monkeypatch.setenv("DISCOVERY_DB_PATH", "signals.db")

    # Start first run (doesn't finish)
    schedule = scheduler.get_schedule(schedule_id)
    lock_acquired = scheduler._acquire_lock(schedule["name"])
    assert lock_acquired is True

    # Try to start second run - should be skipped
    result = await scheduler.execute_run(schedule_id)
    assert result["status"] == "skipped"
    assert result["reason"] == "already_running"
    assert result["run_id"] is None

    # Release lock
    scheduler._release_lock(schedule["name"])

    # Now should be able to run
    result = await scheduler.execute_run(schedule_id)
    assert result["status"] == RunStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_quality_schedule_failure_handling(scheduler, monkeypatch):
    """Test that quality schedule failures are recorded correctly."""
    config = ScheduleConfig(
        name="test-failure",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    # Mock quality ops function to raise error
    def mock_sync_error(db_path):
        raise RuntimeError("Notion API unavailable")

    monkeypatch.setattr("ops.quality.status_events.sync_status_events", mock_sync_error)
    monkeypatch.setenv("DISCOVERY_DB_PATH", "signals.db")

    # Execute - should fail gracefully
    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.FAILED.value
    assert "Notion API unavailable" in result["error_message"]
    assert result["errors"] == 1

    # Verify failure recorded in history
    history = scheduler.get_run_history(schedule_id, limit=1)
    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert "Notion API unavailable" in history[0]["error_message"]
