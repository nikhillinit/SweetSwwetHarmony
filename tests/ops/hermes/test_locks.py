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
    assert "ownerToken" in payload
    assert payload["pid"] == os.getpid()
    assert "acquiredAt" in payload
    assert "heartbeatAt" in payload
    assert payload["ttlSeconds"] == lock.ttl_seconds
    assert payload["acquired_at"] == payload["acquiredAt"]
    assert payload["mode"] == "dry-run"
    assert payload["runId"] == "run-1"
    assert payload["context"]["kind"] == "hermes"
    assert payload["context"]["mode"] == "dry-run"
    assert payload["context"]["runId"] == "run-1"

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

    audit_path = tmp_path / "forced_unlocks.jsonl"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["reason"] == "operator confirmed stale"
    assert audit["pid"] == os.getpid()
    assert audit["lockHolderInfoSnapshot"]["runId"] == "run-1"


def test_context_manager_raises_when_lock_is_held(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    first = HermesLock(lock_path, mode="dry-run", run_id="run-1")
    assert first.acquire(timeout_seconds=0) is True

    with pytest.raises(HermesLockError, match="Could not acquire"):
        with HermesLock(lock_path, mode="dry-run", run_id="run-2"):
            pass

    first.release()


def test_lock_release_requires_owner_token(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    lock = HermesLock(lock_path, mode="dry-run", run_id="run-1")
    assert lock.acquire(timeout_seconds=0) is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    payload["ownerToken"] = "different-owner"
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    lock.release()

    assert lock_path.exists()
    assert HermesLock(lock_path).force_unlock("cleanup after owner token mismatch") is True


def test_malformed_lock_is_not_reclaimed_without_force_unlock(tmp_path: Path) -> None:
    lock_path = tmp_path / "hermes.lock"
    lock_path.write_text("{", encoding="utf-8")

    assert HermesLock(lock_path, ttl_seconds=0).acquire(timeout_seconds=0) is False
    assert lock_path.exists()
    assert HermesLock(lock_path).force_unlock("operator confirmed malformed lock") is True
    lock = HermesLock(lock_path)
    assert lock.acquire(timeout_seconds=0) is True
    lock.release()
