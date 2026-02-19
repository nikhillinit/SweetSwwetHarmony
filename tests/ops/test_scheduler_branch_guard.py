"""Tests for scheduler branch-safety guardrail (10 tests).

Covers: warning on non-main branch, strict block for canary-monitor,
detached HEAD and no-git states, artifact git fields, ledger events,
and lenient env parsing.
"""

import asyncio
import json
import logging
import os
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
from utils.git_utils import DETACHED


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_branch_guard.db"
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


def _canary_config(**overrides):
    defaults = dict(
        name="canary-monitor-6h",
        cron_expression="0 */6 * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=True,
    )
    defaults.update(overrides)
    return ScheduleConfig(**defaults)


def _full_config(**overrides):
    defaults = dict(
        name="nightly-full",
        cron_expression="0 2 * * *",
        collectors=[],
        mode="full",
        enabled=True,
    )
    defaults.update(overrides)
    return ScheduleConfig(**defaults)


# ---------------------------------------------------------------------------
# 1. Non-main warning emitted (full mode, default env)
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=("feature/x", "abc1234"))
def test_non_main_warning_emitted(mock_git, scheduler, caplog):
    """Non-main branch emits WARNING but does not block full-mode schedules."""
    config = _full_config()
    sid = scheduler.create_schedule(config)

    with caplog.at_level(logging.WARNING, logger="ops.scheduler"):
        # execute_run will try to run the pipeline, mock it to avoid real execution
        with patch.object(scheduler, "_acquire_lock", return_value=True), \
             patch.object(scheduler, "_release_lock"), \
             patch.object(scheduler, "enqueue_run", return_value=1), \
             patch.object(scheduler, "record_run"), \
             patch("ops.scheduler.PipelineScheduler.execute_run",
                   wraps=scheduler.execute_run):
            # The pipeline import inside execute_run will fail but
            # we just need to verify the warning is logged before that.
            # Use a more targeted approach:
            pass

    # Simpler approach: call execute_run and catch whatever error comes from
    # the actual pipeline not being available — the WARNING should be logged
    # before any pipeline code runs.
    with caplog.at_level(logging.WARNING, logger="ops.scheduler"):
        try:
            asyncio.get_event_loop().run_until_complete(
                scheduler.execute_run(sid)
            )
        except Exception:
            pass  # Pipeline import or execution may fail; that's fine

    assert any("feature/x" in r.message for r in caplog.records)
    assert any("Code may differ from production" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. Main branch — no warning
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=("main", "def5678"))
def test_main_no_warning(mock_git, scheduler, caplog):
    """On main branch, no branch warning is emitted."""
    config = _full_config()
    sid = scheduler.create_schedule(config)

    with caplog.at_level(logging.WARNING, logger="ops.scheduler"):
        try:
            asyncio.get_event_loop().run_until_complete(
                scheduler.execute_run(sid)
            )
        except Exception:
            pass

    branch_warnings = [r for r in caplog.records
                       if "Code may differ from production" in r.message]
    assert len(branch_warnings) == 0


# ---------------------------------------------------------------------------
# 3. Strict blocks canary on non-main
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=("feature/x", "abc1234"))
def test_strict_blocks_canary_non_main(mock_git, scheduler, monkeypatch, tmp_path):
    """REQUIRE_MAIN_FOR_CANARY=true blocks canary-monitor on non-main branch."""
    monkeypatch.setenv("REQUIRE_MAIN_FOR_CANARY", "true")
    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    config = _canary_config()
    sid = scheduler.create_schedule(config)

    with pytest.raises(RuntimeError, match="Canary monitor blocked"):
        asyncio.get_event_loop().run_until_complete(
            scheduler.execute_run(sid)
        )


# ---------------------------------------------------------------------------
# 4. Detached HEAD warning
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=(DETACHED, "abc1234"))
def test_detached_head_warning(mock_git, scheduler, caplog):
    """Detached HEAD emits warning with SHA."""
    config = _full_config()
    sid = scheduler.create_schedule(config)

    with caplog.at_level(logging.WARNING, logger="ops.scheduler"):
        try:
            asyncio.get_event_loop().run_until_complete(
                scheduler.execute_run(sid)
            )
        except Exception:
            pass

    assert any("detached HEAD (abc1234)" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 5. Strict blocks canary on detached HEAD
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=(DETACHED, "abc1234"))
def test_strict_blocks_canary_detached(mock_git, scheduler, monkeypatch, tmp_path):
    """REQUIRE_MAIN_FOR_CANARY=true blocks canary-monitor on detached HEAD."""
    monkeypatch.setenv("REQUIRE_MAIN_FOR_CANARY", "true")
    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    config = _canary_config()
    sid = scheduler.create_schedule(config)

    with pytest.raises(RuntimeError, match="Canary monitor blocked"):
        asyncio.get_event_loop().run_until_complete(
            scheduler.execute_run(sid)
        )


# ---------------------------------------------------------------------------
# 6. No git — warning
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=(None, None))
def test_no_git_warning(mock_git, scheduler, caplog):
    """When git is unavailable, warning mentions that fact."""
    config = _full_config()
    sid = scheduler.create_schedule(config)

    with caplog.at_level(logging.WARNING, logger="ops.scheduler"):
        try:
            asyncio.get_event_loop().run_until_complete(
                scheduler.execute_run(sid)
            )
        except Exception:
            pass

    assert any("git unavailable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. Strict blocks canary when no git
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=(None, None))
def test_strict_blocks_canary_no_git(mock_git, scheduler, monkeypatch, tmp_path):
    """REQUIRE_MAIN_FOR_CANARY=true blocks canary-monitor when git unavailable."""
    monkeypatch.setenv("REQUIRE_MAIN_FOR_CANARY", "true")
    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    config = _canary_config()
    sid = scheduler.create_schedule(config)

    with pytest.raises(RuntimeError, match="Canary monitor blocked"):
        asyncio.get_event_loop().run_until_complete(
            scheduler.execute_run(sid)
        )


# ---------------------------------------------------------------------------
# 8. Artifact contains git fields
# ---------------------------------------------------------------------------

def test_artifact_contains_git_fields(tmp_path):
    """_write_cadence_artifact includes git_branch and git_sha in output."""
    artifacts_dir = str(tmp_path / "cadence")
    os.makedirs(artifacts_dir, exist_ok=True)

    results = {"canary": {"returncode": 0, "stdout": "ok", "stderr": ""}}
    _write_cadence_artifact(artifacts_dir, 42, results,
                            git_branch="feature/x", git_sha="abc1234")

    summary_path = os.path.join(artifacts_dir, "latest_summary.json")
    with open(summary_path, "r") as f:
        data = json.load(f)

    assert data["git_branch"] == "feature/x"
    assert data["git_sha"] == "abc1234"
    assert data["run_id"] == 42

    # Also check ledger
    ledger_path = os.path.join(artifacts_dir, "cadence_ledger.jsonl")
    with open(ledger_path, "r") as f:
        line = json.loads(f.readline())
    assert line["git_branch"] == "feature/x"
    assert line["git_sha"] == "abc1234"


# ---------------------------------------------------------------------------
# 9. Lenient env parsing (YES, On, 1, etc.)
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=("feature/x", "abc1234"))
def test_lenient_env_parsing(mock_git, scheduler, monkeypatch, tmp_path):
    """REQUIRE_MAIN_FOR_CANARY=YES (uppercase) still triggers block."""
    monkeypatch.setenv("REQUIRE_MAIN_FOR_CANARY", "YES")
    monkeypatch.setattr("ops.scheduler._REPO_ROOT", str(tmp_path))
    config = _canary_config()
    sid = scheduler.create_schedule(config)

    with pytest.raises(RuntimeError, match="Canary monitor blocked"):
        asyncio.get_event_loop().run_until_complete(
            scheduler.execute_run(sid)
        )


# ---------------------------------------------------------------------------
# 10. Strict block records ledger event
# ---------------------------------------------------------------------------

@patch("ops.scheduler.get_git_info", return_value=("feature/x", "abc1234"))
def test_strict_block_records_ledger_event(mock_git, scheduler, monkeypatch, tmp_path):
    """Blocked canary writes a ledger event with run_id=null and branch info."""
    monkeypatch.setenv("REQUIRE_MAIN_FOR_CANARY", "true")

    # Redirect ledger writes to tmp_path
    ledger_dir = str(tmp_path / "artifacts" / "cadence")
    os.makedirs(ledger_dir, exist_ok=True)
    ledger_path = os.path.join(ledger_dir, "cadence_ledger.jsonl")

    config = _canary_config()
    sid = scheduler.create_schedule(config)

    with patch("ops.scheduler._REPO_ROOT", str(tmp_path)):
        with pytest.raises(RuntimeError, match="Canary monitor blocked"):
            asyncio.get_event_loop().run_until_complete(
                scheduler.execute_run(sid)
            )

    # Read the ledger
    assert os.path.exists(ledger_path), f"Ledger not found at {ledger_path}"
    with open(ledger_path, "r") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) >= 1
    entry = lines[-1]
    assert entry["event"] == "blocked"
    assert entry["run_id"] is None
    assert entry["git_branch"] == "feature/x"
    assert entry["git_sha"] == "abc1234"
    assert "canary-monitor-6h" in entry["schedule_name"]
    assert "Branch guardrail" in entry["reason"]
