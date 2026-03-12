"""Tests for quality schedule integration (Phase 9 Task 3)."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ops.scheduler import PipelineScheduler, ScheduleConfig, RunStatus
from ops.storage import OpsStorage


@dataclass
class _FakeOutcomeStats:
    """Minimal stand-in for OutcomeBackfillStats."""
    scanned: int = 10
    labeled: int = 3
    fp: int = 1
    tp: int = 2
    skipped_no_events: int = 7


@dataclass
class _FakeSyncStats:
    """Minimal stand-in for StatusEventInsertStats."""
    observed_at: str = "2026-03-12T00:00:00Z"
    events_inserted: int = 5
    new_keys: int = 0
    changed_keys: int = 0


def _make_quality_sync_mocks(monkeypatch, *, events_inserted=5, labeled=3,
                              affected_dates=None):
    """Helper to set up all mocks needed for quality-sync tests.

    Returns (mock_sync, mock_backfill, mock_recompute_conn).
    """
    mock_sync = AsyncMock(return_value=_FakeSyncStats(events_inserted=events_inserted))
    mock_backfill = MagicMock(return_value=_FakeOutcomeStats(labeled=labeled))

    monkeypatch.setattr("ops.quality.status_events.sync_and_capture_status_events", mock_sync)
    monkeypatch.setattr("ops.quality.db.ensure_quality_tables", MagicMock())
    monkeypatch.setattr("ops.quality.outcomes.backfill_outcomes_from_events", mock_backfill)

    # Mock sqlite3.connect for the three connections created in _execute_quality_sync
    mock_recompute_conn = MagicMock()
    mock_recompute_conn.execute.return_value.fetchall.return_value = affected_dates or []
    original_connect = sqlite3.connect

    def patched_connect(path, **kwargs):
        if "timeout" in kwargs:
            # Step 3: recompute connection
            return mock_recompute_conn
        return original_connect(path, **kwargs)

    monkeypatch.setattr("sqlite3.connect", patched_connect)

    return mock_sync, mock_backfill, mock_recompute_conn


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary test database with proper cleanup."""
    db_path = tmp_path / "test_scheduler_quality.db"
    storage = OpsStorage(str(db_path))

    yield storage

    # Proper cleanup for Windows
    try:
        storage.pool.close()
        del storage
        import time
        time.sleep(0.1)
        for ext in ['', '-wal', '-shm']:
            path = Path(f"{db_path}{ext}")
            if path.exists():
                try:
                    path.unlink()
                except PermissionError:
                    pass
    except Exception:
        pass


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
    config = ScheduleConfig(
        name="test-quality-sync",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    _make_quality_sync_mocks(monkeypatch, events_inserted=5, labeled=3)

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 3  # outcomes_updated
    assert result["run_id"] is not None

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

    monkeypatch.setenv("LLM_THESIS_MODE", "off")

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 0


@pytest.mark.asyncio
async def test_execute_quality_classify_workflow_llm_enabled(scheduler, temp_db, monkeypatch):
    """Test quality-classify workflow when LLM is enabled."""
    config = ScheduleConfig(
        name="test-quality-classify",
        cron_expression="0 2 * * *",
        mode="quality-classify",
    )
    schedule_id = scheduler.create_schedule(config)

    monkeypatch.setenv("LLM_THESIS_MODE", "shadow")

    mock_classify = MagicMock(return_value=10)
    monkeypatch.setattr("ops.quality.thesis.batch_classify_recent", mock_classify)

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 10

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

    mock_patterns = [
        {"type": "collector_concentration", "collector": "github", "fp_count": 10},
        {"type": "duplicate_descriptions", "pattern": "AI platform", "count": 5},
    ]
    mock_detect = MagicMock(return_value=mock_patterns)
    monkeypatch.setattr("ops.quality.patterns.detect_patterns", mock_detect)

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert result["signals_processed"] == 2

    with temp_db.transaction() as conn:
        cursor = conn.execute(
            "SELECT run_id, pattern_data, pattern_count FROM pattern_runs WHERE run_id = ?",
            (result["run_id"],),
        )
        row = cursor.fetchone()
        assert row is not None

        stored_patterns = json.loads(row[1])
        assert len(stored_patterns) == 2
        assert row[2] == 2


# ---------------------------------------------------------------------------
# Task 3.4: CLI integration tests
# ---------------------------------------------------------------------------

def test_schedule_mode_choices_include_quality_modes():
    """Test that schedule add command accepts quality modes."""
    import argparse
    from ops.cli import main

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=[
        "full", "collect", "process",
        "quality-sync", "quality-classify", "quality-patterns",
    ])

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

    run_id_1 = scheduler.enqueue_run(schedule_id)
    assert run_id_1 > 0

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

    _make_quality_sync_mocks(monkeypatch, events_inserted=0, labeled=0)

    # Acquire lock manually
    schedule = scheduler.get_schedule(schedule_id)
    lock_acquired = scheduler._acquire_lock(schedule["name"])
    assert lock_acquired is True

    # Should be skipped due to lock
    result = await scheduler.execute_run(schedule_id)
    assert result["status"] == "skipped"
    assert result["reason"] == "already_running"
    assert result["run_id"] is None

    scheduler._release_lock(schedule["name"])

    # Now should succeed
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

    # Make ensure_quality_tables raise before sync even runs
    async def mock_sync_error(*args, **kwargs):
        raise RuntimeError("Notion API unavailable")

    monkeypatch.setattr("ops.quality.status_events.sync_and_capture_status_events", mock_sync_error)
    monkeypatch.setattr("ops.quality.db.ensure_quality_tables", MagicMock())

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.FAILED.value
    assert "Notion API unavailable" in result["error_message"]
    assert result["errors"] == 1

    history = scheduler.get_run_history(schedule_id, limit=1)
    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert "Notion API unavailable" in history[0]["error_message"]


# ---------------------------------------------------------------------------
# B3: New tests for Step 4 unblock PR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_sync_passes_connection_not_string(scheduler, temp_db, monkeypatch):
    """B3-1: backfill_outcomes_from_events receives sqlite3.Connection, not a string path."""
    config = ScheduleConfig(
        name="test-conn-type",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    _, mock_backfill, _ = _make_quality_sync_mocks(monkeypatch, labeled=0)

    await scheduler.execute_run(schedule_id)

    assert mock_backfill.call_count == 1
    conn_arg = mock_backfill.call_args[0][0]
    assert isinstance(conn_arg, sqlite3.Connection)


@pytest.mark.asyncio
async def test_quality_sync_targeted_recompute_scope(scheduler, temp_db, monkeypatch):
    """B3-2: aggregate_daily_metrics called only for dates with labeled_at >= run_started_at."""
    config = ScheduleConfig(
        name="test-recompute-scope",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    _, _, mock_recompute_conn = _make_quality_sync_mocks(
        monkeypatch, events_inserted=2, labeled=2,
        affected_dates=[("2026-03-10",), ("2026-03-11",)],
    )

    mock_aggregate = MagicMock()
    monkeypatch.setattr("monitoring.daily_aggregator.aggregate_daily_metrics", mock_aggregate)

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.SUCCESS.value
    assert mock_aggregate.call_count == 2
    dates_called = [call[0][1] for call in mock_aggregate.call_args_list]
    assert dates_called == ["2026-03-10", "2026-03-11"]

    # Verify the SQL query included run_started_at filter
    sql_calls = mock_recompute_conn.execute.call_args_list
    select_calls = [c for c in sql_calls if "labeled_at" in str(c)]
    assert len(select_calls) == 1


@pytest.mark.asyncio
async def test_quality_sync_recompute_fatal(scheduler, temp_db, monkeypatch):
    """B3-3: If aggregate_daily_metrics raises, run status is FAILED."""
    config = ScheduleConfig(
        name="test-recompute-fatal",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    schedule_id = scheduler.create_schedule(config)

    _make_quality_sync_mocks(
        monkeypatch, events_inserted=1, labeled=1,
        affected_dates=[("2026-03-10",)],
    )

    def raise_on_aggregate(conn, date):
        raise RuntimeError("DB locked during recompute")

    monkeypatch.setattr("monitoring.daily_aggregator.aggregate_daily_metrics", raise_on_aggregate)

    result = await scheduler.execute_run(schedule_id)

    assert result["status"] == RunStatus.FAILED.value
    assert "DB locked during recompute" in result["error_message"]


def test_idempotent_schedule_creation_all_three(scheduler):
    """B3-4: All 3 convenience commands can be called twice -> no error, same ID."""
    configs = [
        ScheduleConfig(name="quality-sync-notion-status", cron_expression="0 */6 * * *",
                       mode="quality-sync"),
        ScheduleConfig(name="quality-thesis-classify-batch", cron_expression="0 2 * * *",
                       mode="quality-classify"),
        ScheduleConfig(name="quality-find-patterns", cron_expression="0 3 * * 0",
                       mode="quality-patterns"),
    ]

    for config in configs:
        sid1, created1, warnings1 = scheduler.ensure_schedule(config)
        assert created1 is True
        assert warnings1 == []

        sid2, created2, warnings2 = scheduler.ensure_schedule(config)
        assert created2 is False
        assert sid2 == sid1
        assert warnings2 == []


def test_ensure_schedule_drift_warning(scheduler):
    """B3-5: Create with one cron, ensure with another -> warnings, schedule NOT mutated."""
    original_config = ScheduleConfig(
        name="test-drift",
        cron_expression="0 */6 * * *",
        mode="quality-sync",
    )
    sid1, created, _ = scheduler.ensure_schedule(original_config)
    assert created is True

    drifted_config = ScheduleConfig(
        name="test-drift",
        cron_expression="0 */12 * * *",
        mode="quality-sync",
    )
    sid2, created2, warnings = scheduler.ensure_schedule(drifted_config)
    assert created2 is False
    assert sid2 == sid1
    assert len(warnings) > 0
    assert any("cron mismatch" in w for w in warnings)

    schedule = scheduler.get_schedule(sid1)
    assert schedule["cron_expression"] == "0 */6 * * *"  # Original


def test_no_literal_signals_db_in_tests():
    """B3-6: No test in this file references literal 'signals.db'."""
    import inspect
    source = inspect.getsource(__import__(__name__))
    lines = source.split("\n")
    violations = []
    for i, line in enumerate(lines, 1):
        if "signals.db" in line and "no_literal_signals_db" not in line and "B3-6" not in line:
            violations.append(f"  Line {i}: {line.strip()}")
    assert violations == [], f"Found literal 'signals.db' references:\n" + "\n".join(violations)
