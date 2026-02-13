"""Tests for `schedule tick` CLI command (6 tests).

Covers: no-due, runs-due, by-name, name-not-found, composite-exit, disabled-skip.
"""

import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ops.scheduler import PipelineScheduler, ScheduleConfig, RunStatus
from ops.storage import OpsStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_schedule_tick.db"
    storage = OpsStorage(str(db_path))
    yield storage
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
    return PipelineScheduler(temp_db)


def _make_args(db_path, name=None):
    """Create a mock args object for schedule_tick_cmd."""
    class Args:
        pass
    a = Args()
    a.db = db_path
    a.name = name
    return a


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_tick_no_due_schedules(scheduler, temp_db, capsys):
    """Exit 0, 'No schedules due' when nothing is due."""
    from ops.cli import schedule_tick_cmd

    args = _make_args(temp_db.db_path)

    with pytest.raises(SystemExit) as exc_info:
        schedule_tick_cmd(args)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "No schedules due" in captured.out


def test_tick_runs_due_schedule(scheduler, temp_db, capsys, monkeypatch):
    """Executes due schedule and returns success."""
    config = ScheduleConfig(
        name="test-tick-run",
        cron_expression="* * * * *",  # Every minute — always due
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )
    sid = scheduler.create_schedule(config)

    # Mock execute_run to avoid actual subprocess calls
    mock_result = {
        "run_id": 1,
        "status": "success",
        "duration_seconds": 1.5,
    }

    async def mock_execute_run(self, schedule_id):
        return mock_result

    monkeypatch.setattr(PipelineScheduler, "execute_run", mock_execute_run)

    from ops.cli import schedule_tick_cmd
    args = _make_args(temp_db.db_path)

    with pytest.raises(SystemExit) as exc_info:
        schedule_tick_cmd(args)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Executing: test-tick-run" in captured.out
    assert "success" in captured.out


def test_tick_by_name(scheduler, temp_db, capsys, monkeypatch):
    """--name resolves and runs correct schedule."""
    config = ScheduleConfig(
        name="target-sched",
        cron_expression="* * * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )
    scheduler.create_schedule(config)

    # Also create a decoy that should NOT run
    decoy = ScheduleConfig(
        name="decoy-sched",
        cron_expression="* * * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )
    scheduler.create_schedule(decoy)

    async def mock_execute_run(self, schedule_id):
        return {"run_id": 1, "status": "success", "duration_seconds": 0.5}

    monkeypatch.setattr(PipelineScheduler, "execute_run", mock_execute_run)

    from ops.cli import schedule_tick_cmd
    args = _make_args(temp_db.db_path, name="target-sched")

    with pytest.raises(SystemExit) as exc_info:
        schedule_tick_cmd(args)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "target-sched" in captured.out
    assert "decoy-sched" not in captured.out


def test_tick_by_name_not_found(scheduler, temp_db, capsys):
    """Exit 1 with error when --name not found."""
    from ops.cli import schedule_tick_cmd
    args = _make_args(temp_db.db_path, name="nonexistent")

    with pytest.raises(SystemExit) as exc_info:
        schedule_tick_cmd(args)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_tick_composite_exit_any_fail(scheduler, temp_db, capsys, monkeypatch):
    """Any failure → exit 1."""
    config = ScheduleConfig(
        name="fail-sched",
        cron_expression="* * * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )
    scheduler.create_schedule(config)

    async def mock_execute_run(self, schedule_id):
        return {"run_id": 1, "status": "failed", "duration_seconds": 0.5}

    monkeypatch.setattr(PipelineScheduler, "execute_run", mock_execute_run)

    from ops.cli import schedule_tick_cmd
    args = _make_args(temp_db.db_path)

    with pytest.raises(SystemExit) as exc_info:
        schedule_tick_cmd(args)

    assert exc_info.value.code == 1


def test_tick_skips_disabled_schedules(scheduler, temp_db, capsys):
    """Disabled schedules not executed."""
    config = ScheduleConfig(
        name="disabled-sched",
        cron_expression="* * * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=False,
    )
    scheduler.create_schedule(config)

    from ops.cli import schedule_tick_cmd
    args = _make_args(temp_db.db_path)

    with pytest.raises(SystemExit) as exc_info:
        schedule_tick_cmd(args)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "No schedules due" in captured.out
