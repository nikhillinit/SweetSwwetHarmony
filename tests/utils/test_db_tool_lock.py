from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.db_tool_lock import DBToolLock, DBToolLockError


def test_db_tool_lock_acquire_release(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("placeholder", encoding="utf-8")

    lock = DBToolLock(db_path, tool_name="unit-test")
    assert lock.acquire(timeout_seconds=0)
    assert lock.is_locked() is True
    holder = lock.get_holder_info()
    assert holder is not None
    assert "ownerToken" in holder
    assert "acquiredAt" in holder
    assert "heartbeatAt" in holder
    assert holder["ttlSeconds"] == lock.ttl_seconds
    assert holder["tool_name"] == "unit-test"
    assert holder["context"]["kind"] == "db-tool"
    assert holder["context"]["toolName"] == "unit-test"
    assert holder["context"]["dbPath"] == str(db_path)
    assert holder["acquired_at"] == holder["acquiredAt"]
    lock.release()
    assert lock.is_locked() is False


def test_db_tool_lock_exposes_heartbeat_health(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("placeholder", encoding="utf-8")

    lock = DBToolLock(db_path, tool_name="unit-test")
    assert lock.acquire(timeout_seconds=0)
    assert lock.is_healthy() is True
    assert lock.heartbeat_error() is None

    payload = json.loads(lock.lock_path.read_text(encoding="utf-8"))
    payload["ownerToken"] = "different-owner"
    lock.lock_path.write_text(json.dumps(payload), encoding="utf-8")

    assert lock.is_healthy() is False
    assert "ownerToken" in (lock.heartbeat_error() or "")
    with pytest.raises(DBToolLockError, match="ownerToken"):
        lock.assert_healthy()

    lock.release()
    assert lock.lock_path.exists()
    assert lock.force_break() is True


def test_db_tool_lock_stale_break(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("placeholder", encoding="utf-8")

    lock = DBToolLock(db_path, tool_name="stale-test", ttl_seconds=0)
    assert lock.acquire(timeout_seconds=0)
    # A second lock should break the stale lock immediately because ttl_seconds=0.
    second = DBToolLock(db_path, tool_name="second", ttl_seconds=0)
    assert second.acquire(timeout_seconds=0)
    holder = second.get_holder_info()
    assert holder is not None
    assert holder["tool_name"] == "second"
    second.release()


def test_db_tool_lock_release_requires_owner_token(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("placeholder", encoding="utf-8")

    lock = DBToolLock(db_path, tool_name="unit-test")
    assert lock.acquire(timeout_seconds=0)
    payload = json.loads(lock.lock_path.read_text(encoding="utf-8"))
    payload["ownerToken"] = "different-owner"
    lock.lock_path.write_text(json.dumps(payload), encoding="utf-8")

    lock.release()

    assert lock.lock_path.exists()
    assert lock.force_break() is True


def test_db_tool_lock_force_break_removes_malformed_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("placeholder", encoding="utf-8")
    lock = DBToolLock(db_path, tool_name="unit-test")
    lock.lock_path.write_text("{", encoding="utf-8")

    assert lock.acquire(timeout_seconds=0) is False
    assert lock.force_break() is True
    assert lock.acquire(timeout_seconds=0) is True

    lock.release()
