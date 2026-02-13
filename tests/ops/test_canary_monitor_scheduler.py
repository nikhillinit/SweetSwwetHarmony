"""Tests for canary-monitor scheduler mode (17 tests).

Covers: schedule CRUD, ensure_schedule idempotency + drift warnings,
canary-monitor execution (subprocess mocking), artifact writes,
and defensive parsing of malformed DB data.
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ops.scheduler import (
    PipelineScheduler,
    ScheduleConfig,
    RunStatus,
    _write_cadence_artifact,
)
from ops.storage import OpsStorage


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_scheduler_quality.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Create temporary test database with proper cleanup."""
    db_path = tmp_path / "test_canary_monitor.db"
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
    """Create scheduler instance."""
    return PipelineScheduler(temp_db)


def _canary_config(**overrides):
    """Build a default canary-monitor ScheduleConfig with overrides."""
    defaults = dict(
        name="canary-monitor-6h",
        cron_expression="0 */6 * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )
    defaults.update(overrides)
    return ScheduleConfig(**defaults)


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------

def test_create_canary_monitor_schedule(scheduler):
    """Mode accepted by CHECK constraint."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    assert sid > 0
    row = scheduler.get_schedule(sid)
    assert row["mode"] == "canary-monitor"
    assert row["name"] == "canary-monitor-6h"


def test_get_schedule_by_name(scheduler):
    """Lookup by unique name."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    row = scheduler.get_schedule_by_name("canary-monitor-6h")
    assert row is not None
    assert row["id"] == sid
    assert row["mode"] == "canary-monitor"


def test_get_schedule_by_name_missing(scheduler):
    """Returns None for non-existent name."""
    row = scheduler.get_schedule_by_name("does-not-exist")
    assert row is None


# ---------------------------------------------------------------------------
# ensure_schedule — idempotency + drift warnings
# ---------------------------------------------------------------------------

def test_ensure_schedule_creates_new(scheduler):
    """First call creates; returns (id, True, [])."""
    config = _canary_config()
    sid, created, warnings = scheduler.ensure_schedule(config)
    assert sid > 0
    assert created is True
    assert warnings == []


def test_ensure_schedule_idempotent(scheduler):
    """Second call with same config returns (id, False, [])."""
    config = _canary_config()
    sid1, created1, w1 = scheduler.ensure_schedule(config)
    sid2, created2, w2 = scheduler.ensure_schedule(config)
    assert sid1 == sid2
    assert created1 is True
    assert created2 is False
    assert w1 == []
    assert w2 == []


def test_ensure_schedule_config_drift_warning(scheduler):
    """Returns warnings for mismatched cron/mode/enabled/dry_run/collectors/max_retries."""
    config = _canary_config()
    scheduler.ensure_schedule(config)

    # Request with different config
    different = _canary_config(cron_expression="0 */3 * * *")
    sid, created, warnings = scheduler.ensure_schedule(different)
    assert created is False
    assert any("cron mismatch" in w for w in warnings)


def test_ensure_schedule_malformed_collectors_json(scheduler):
    """Manually corrupt collectors to invalid JSON → emits warning, does not crash."""
    config = _canary_config()
    sid, _, _ = scheduler.ensure_schedule(config)

    # Corrupt the collectors column directly
    with scheduler.storage.transaction() as conn:
        conn.execute(
            "UPDATE pipeline_schedules SET collectors = ? WHERE id = ?",
            ("{not valid json", sid),
        )

    # Re-run ensure_schedule — should not crash
    sid2, created, warnings = scheduler.ensure_schedule(config)
    assert created is False
    assert sid2 == sid
    assert any("collectors malformed" in w for w in warnings)


def test_ensure_schedule_malformed_max_retries(scheduler):
    """Manually corrupt max_retries to non-integer string → emits warning, does not crash."""
    config = _canary_config()
    sid, _, _ = scheduler.ensure_schedule(config)

    # Corrupt the max_retries column directly
    with scheduler.storage.transaction() as conn:
        conn.execute(
            "UPDATE pipeline_schedules SET max_retries = ? WHERE id = ?",
            ("not-a-number", sid),
        )

    # Re-run ensure_schedule — should not crash
    sid2, created, warnings = scheduler.ensure_schedule(config)
    assert created is False
    assert sid2 == sid
    assert any("max_retries malformed" in w for w in warnings)


# ---------------------------------------------------------------------------
# _execute_canary_monitor — subprocess mocking
# ---------------------------------------------------------------------------

def _make_mock_process(returncode=0, stdout=b"ok\n", stderr=b""):
    """Create a mock asyncio.Process."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.fixture
def mock_subprocess_all_pass(monkeypatch):
    """Patch asyncio.create_subprocess_exec to return exit 0 for all steps."""
    proc = _make_mock_process(returncode=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return proc


@pytest.fixture
def artifacts_dir(tmp_path):
    """Temporary artifacts directory."""
    d = tmp_path / "artifacts" / "cadence"
    d.mkdir(parents=True)
    return d


def test_execute_canary_monitor_all_pass(scheduler, mock_subprocess_all_pass, tmp_path, monkeypatch):
    """Mock subprocesses exit 0 → all results have returncode 0."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    # Redirect artifacts to temp
    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    result = asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )
    # 3 required steps (canary, drift, activation)
    assert len(result) == 3
    assert all(v["returncode"] == 0 for v in result.values())


def test_execute_canary_monitor_required_fail(scheduler, tmp_path, monkeypatch):
    """Mock canary exit 1 → raises RuntimeError."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    fail_proc = _make_mock_process(returncode=1, stderr=b"canary failed")

    async def fake_exec(*args, **kwargs):
        return fail_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="canary-monitor required"):
        asyncio.get_event_loop().run_until_complete(
            scheduler._execute_canary_monitor(run_id, schedule)
        )


def test_execute_canary_monitor_optional_fail(scheduler, tmp_path, monkeypatch):
    """Mock shadow-export exit 1 → still succeeds (optional step)."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_THESIS_MODE", "shadow")

    call_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Steps 1-3 pass, step 4 (shadow-export) fails
        if call_count <= 3:
            return _make_mock_process(returncode=0)
        else:
            return _make_mock_process(returncode=1, stderr=b"shadow fail")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )
    # All 4 steps ran, but shadow-export failed (optional)
    assert len(result) == 4
    assert result["shadow-export"]["returncode"] == 1
    # The 3 required steps all passed
    assert result["canary"]["returncode"] == 0


def test_canary_monitor_uses_store_results(scheduler, tmp_path, monkeypatch):
    """--store-results present in canary cmd args."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    captured_args = []

    async def fake_exec(*args, **kwargs):
        captured_args.append(args)
        return _make_mock_process(returncode=0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    # First call is the canary step — check for --store-results
    canary_args = captured_args[0]
    assert "--store-results" in canary_args


def test_canary_monitor_step_timeout(scheduler, tmp_path, monkeypatch):
    """Mock hang → TimeoutError → required step fails."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("CANARY_STEP_TIMEOUT_SECONDS", "1")
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    hang_proc = AsyncMock()
    hang_proc.returncode = None
    hang_proc.kill = MagicMock()

    async def slow_communicate():
        await asyncio.sleep(10)  # Will be cancelled by timeout
        return (b"", b"")

    hang_proc.communicate = slow_communicate

    async def fake_exec(*args, **kwargs):
        return hang_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.get_event_loop().run_until_complete(
            scheduler._execute_canary_monitor(run_id, schedule)
        )


def test_canary_monitor_shadow_gated_on_llm_mode(scheduler, tmp_path, monkeypatch):
    """Shadow step present when LLM_THESIS_MODE in (shadow, active), absent when off."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))

    captured_args = []

    async def fake_exec(*args, **kwargs):
        captured_args.append(args)
        return _make_mock_process(returncode=0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    # With LLM off — no shadow step
    monkeypatch.setenv("LLM_THESIS_MODE", "off")
    captured_args.clear()
    asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )
    assert len(captured_args) == 3  # canary, drift, activation only

    # With LLM shadow — shadow step added
    monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
    captured_args.clear()
    # Need a new run_id to avoid idempotency issues
    run_id2 = run_id + 100
    asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id2, schedule)
    )
    assert len(captured_args) == 4  # canary, drift, activation, shadow-export


def test_required_fail_still_writes_ledger(scheduler, tmp_path, monkeypatch):
    """(MO-6) Required fail → ledger entry with error field."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    fail_proc = _make_mock_process(returncode=1, stderr=b"boom")

    async def fake_exec(*args, **kwargs):
        return fail_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError):
        asyncio.get_event_loop().run_until_complete(
            scheduler._execute_canary_monitor(run_id, schedule)
        )

    # Ledger should still be written (finally block)
    ledger_path = tmp_path / "artifacts" / "cadence" / "cadence_ledger.jsonl"
    assert ledger_path.exists()
    entry = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    assert entry["error"] is not None
    assert entry["run_id"] == run_id


def test_db_path_propagates_to_subprocesses(scheduler, tmp_path, monkeypatch):
    """(R4-5) Subprocess args + env use storage.db_path."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    captured_kwargs = []

    async def fake_exec(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return _make_mock_process(returncode=0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    expected_db = os.path.abspath(scheduler.storage.db_path)

    # All subprocess calls should have DISCOVERY_DB_PATH in env
    for kw in captured_kwargs:
        assert kw["env"]["DISCOVERY_DB_PATH"] == expected_db


def test_spawn_failure_writes_ledger(scheduler, tmp_path, monkeypatch):
    """(R5-4) OSError on exec → returncode=-2, ledger has error.

    Unit: _execute_canary_monitor raises RuntimeError.
    Integration via execute_run: returns status=failed.
    """
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    async def fail_exec(*args, **kwargs):
        raise OSError("No such file or directory")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fail_exec)

    # Unit level: _execute_canary_monitor raises RuntimeError
    with pytest.raises(RuntimeError, match="spawn failed"):
        asyncio.get_event_loop().run_until_complete(
            scheduler._execute_canary_monitor(run_id, schedule)
        )

    # Ledger should be written with error
    ledger_path = tmp_path / "artifacts" / "cadence" / "cadence_ledger.jsonl"
    assert ledger_path.exists()
    entry = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    assert entry["steps"]["canary"]["returncode"] == -2
    assert entry["error"] is not None

    # Integration level: execute_run catches and returns status=failed
    run_id2 = scheduler.enqueue_run(sid)
    result = asyncio.get_event_loop().run_until_complete(
        scheduler.execute_run(sid)
    )
    assert result["status"] == "failed"
