from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from integrations.hermes.locks import HermesLock, HermesLockError


def test_lock_acquire_writes_metadata_and_release_removes_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    lock = HermesLock(lock_path, mode="dry-run", run_id="run-1")

    assert lock.acquire(timeout_seconds=0) is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["mode"] == "dry-run"
    assert payload["runId"] == "run-1"

    lock.release()

    assert not lock_path.exists()


def test_lock_context_manager_releases_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"

    with pytest.raises(RuntimeError, match="boom"):
        with HermesLock(lock_path, mode="execute", run_id="run-2"):
            assert lock_path.exists()
            raise RuntimeError("boom")

    assert not lock_path.exists()


def test_active_lock_blocks_second_acquire(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    first = HermesLock(lock_path, mode="dry-run", run_id="run-1")
    second = HermesLock(lock_path, mode="dry-run", run_id="run-2")

    assert first.acquire(timeout_seconds=0) is True
    assert second.acquire(timeout_seconds=0) is False
    assert second.get_holder_info()["runId"] == "run-1"

    first.release()


def test_stale_lock_is_replaced(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    old_time = datetime.now(timezone.utc) - timedelta(hours=3)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999999,
                "hostname": "old",
                "acquired_at": old_time.isoformat(),
                "mode": "dry-run",
                "runId": "old-run",
            }
        ),
        encoding="utf-8",
    )

    lock = HermesLock(lock_path, ttl_seconds=1, mode="dry-run", run_id="new-run")

    assert lock.acquire(timeout_seconds=0) is True
    assert json.loads(lock_path.read_text(encoding="utf-8"))["runId"] == "new-run"

    lock.release()


def test_force_unlock_requires_reason(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    lock = HermesLock(lock_path, mode="dry-run", run_id="run-1")
    assert lock.acquire(timeout_seconds=0) is True

    with pytest.raises(ValueError, match="reason"):
        HermesLock(lock_path).force_unlock("")

    assert lock_path.exists()
    assert HermesLock(lock_path).force_unlock("operator confirmed stale") is True
    assert not lock_path.exists()


def test_context_manager_raises_when_lock_is_held(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    first = HermesLock(lock_path, mode="dry-run", run_id="run-1")
    assert first.acquire(timeout_seconds=0) is True

    with pytest.raises(HermesLockError, match="Could not acquire"):
        with HermesLock(lock_path, mode="dry-run", run_id="run-2"):
            pass

    first.release()
