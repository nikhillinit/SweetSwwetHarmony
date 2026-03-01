"""Tests for canary-monitor regret-check step (ok flag).

Covers: regret-check inline step in _execute_canary_monitor,
ok: true on success, ok: false on failure, non-blocking invariant.
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ops.scheduler import PipelineScheduler, ScheduleConfig
from ops.storage import OpsStorage


# v35 schema subset for audit_events
_AUDIT_EVENTS_DDL = """
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_email TEXT,
    actor_role TEXT,
    before_state TEXT,
    after_state TEXT,
    reason TEXT,
    correlation_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
"""


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_regret.db"
    storage = OpsStorage(str(db_path))
    yield storage
    try:
        storage.pool.close()
    except Exception:
        pass


@pytest.fixture
def scheduler(temp_db):
    return PipelineScheduler(temp_db)


def _make_mock_process(returncode=0, stdout=b"ok\n", stderr=b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _canary_config():
    return ScheduleConfig(
        name="canary-monitor-6h",
        cron_expression="0 */6 * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )


def _setup_canary_run(scheduler):
    """Create schedule + enqueue run, return (sid, run_id, schedule)."""
    config = _canary_config()
    sid = scheduler.create_schedule(config)
    schedule = scheduler.get_schedule(sid)
    run_id = scheduler.enqueue_run(sid)
    return sid, run_id, schedule


def test_regret_check_ok_true_when_table_exists(
    scheduler, tmp_path, monkeypatch
):
    """With audit_events table present, regret-check emits ok: true."""
    sid, run_id, schedule = _setup_canary_run(scheduler)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    # Create a signals.db with audit_events table
    signals_db = os.path.abspath(scheduler.storage.db_path)
    conn = sqlite3.connect(signals_db)
    conn.executescript(_AUDIT_EVENTS_DDL)
    conn.close()

    proc = _make_mock_process(returncode=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    assert "regret-check" in result
    assert result["regret-check"]["returncode"] == 0
    payload = json.loads(result["regret-check"]["stdout"])
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["overdue"] == []


def test_regret_check_ok_false_when_table_missing(
    scheduler, tmp_path, monkeypatch
):
    """Without audit_events table, regret-check emits ok: false with error."""
    sid, run_id, schedule = _setup_canary_run(scheduler)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    # The OpsStorage DB does NOT have audit_events — strict=True will raise
    proc = _make_mock_process(returncode=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    assert "regret-check" in result
    assert result["regret-check"]["returncode"] == 0  # Non-blocking
    payload = json.loads(result["regret-check"]["stdout"])
    assert payload["ok"] is False
    assert "error" in payload


def test_regret_check_non_blocking_on_failure(
    scheduler, tmp_path, monkeypatch
):
    """Regret-check failure never causes _execute_canary_monitor to raise."""
    sid, run_id, schedule = _setup_canary_run(scheduler)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    proc = _make_mock_process(returncode=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    # Patch get_overdue_regret_checks to raise
    def exploding_check(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "monitoring.feature_gate.get_overdue_regret_checks", exploding_check
    )

    # Should NOT raise
    result = asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    payload = json.loads(result["regret-check"]["stdout"])
    assert payload["ok"] is False
    assert "kaboom" in payload["error"]


def test_regret_check_reports_overdue(
    scheduler, tmp_path, monkeypatch
):
    """With an overdue promotion, regret-check still ok: true but count > 0."""
    sid, run_id, schedule = _setup_canary_run(scheduler)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    # Create audit_events with an old promotion (overdue)
    signals_db = os.path.abspath(scheduler.storage.db_path)
    conn = sqlite3.connect(signals_db)
    conn.executescript(_AUDIT_EVENTS_DDL)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    conn.execute(
        "INSERT INTO audit_events "
        "(action_type, entity_type, entity_id, actor_id, created_at) "
        "VALUES ('feature_promote', 'feature_flag', 'DELIVERY_MODE', "
        "'operator:test', ?)",
        (old.isoformat(),),
    )
    conn.commit()
    conn.close()

    proc = _make_mock_process(returncode=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    payload = json.loads(result["regret-check"]["stdout"])
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["overdue"][0]["entity_id"] == "DELIVERY_MODE"


def test_regret_check_in_cadence_artifact(
    scheduler, tmp_path, monkeypatch
):
    """Regret-check result appears in the cadence artifact JSON."""
    sid, run_id, schedule = _setup_canary_run(scheduler)

    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

    proc = _make_mock_process(returncode=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    asyncio.get_event_loop().run_until_complete(
        scheduler._execute_canary_monitor(run_id, schedule)
    )

    summary_path = tmp_path / "artifacts" / "cadence" / "latest_summary.json"
    assert summary_path.exists()
    artifact = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "regret-check" in artifact["steps"]
