from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.advisory_file_lock import AdvisoryFileLock


def _iso(seconds_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _write_lock(
    lock_path: Path,
    *,
    owner_token: str = "stale-owner",
    pid: int = 999999,
    seconds_ago: int = 120,
    ttl_seconds: int = 1,
) -> None:
    lock_path.write_text(
        json.dumps(
            {
                "ownerToken": owner_token,
                "pid": pid,
                "hostname": os.environ.get("HOSTNAME", os.environ.get("COMPUTERNAME", "unknown")),
                "acquiredAt": _iso(seconds_ago),
                "heartbeatAt": _iso(seconds_ago),
                "ttlSeconds": ttl_seconds,
                "context": {"kind": "unit"},
            }
        ),
        encoding="utf-8",
    )


def test_acquire_writes_owner_token_and_release_verifies_token(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"

    lock = AdvisoryFileLock(
        lock_path,
        ttl_seconds=60,
        context={"kind": "unit"},
        legacy_metadata={"tool_name": "unit-test"},
    )

    assert lock.acquire(timeout_seconds=0) is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["ownerToken"] == lock.owner_token
    assert payload["tool_name"] == "unit-test"

    lock.release()
    assert not lock_path.exists()

    assert lock.acquire(timeout_seconds=0) is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    payload["ownerToken"] = "someone-else"
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    lock.release()

    assert lock_path.exists()
    assert json.loads(lock_path.read_text(encoding="utf-8"))["ownerToken"] == "someone-else"


def test_two_contenders_cannot_both_win_stale_reclaim(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"
    _write_lock(lock_path)

    def acquire_once() -> bool:
        contender = AdvisoryFileLock(
            lock_path,
            ttl_seconds=1,
            context={"kind": "unit"},
            break_lock_ttl_seconds=30,
        )
        return contender.acquire(timeout_seconds=0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: acquire_once(), range(2)))

    assert results.count(True) == 1
    assert results.count(False) == 1

    AdvisoryFileLock(lock_path).force_break()


def test_crashed_break_lock_is_reclaimable(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"
    _write_lock(lock_path)
    _write_lock(lock_path.with_name("resource.lock.break"), owner_token="dead-breaker")

    lock = AdvisoryFileLock(
        lock_path,
        ttl_seconds=1,
        context={"kind": "unit"},
        break_lock_ttl_seconds=1,
    )

    assert lock.acquire(timeout_seconds=0) is True
    assert json.loads(lock_path.read_text(encoding="utf-8"))["ownerToken"] == lock.owner_token

    lock.release()


def test_live_break_lock_blocks_reclaim(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"
    _write_lock(lock_path)
    _write_lock(
        lock_path.with_name("resource.lock.break"),
        owner_token="live-breaker",
        seconds_ago=0,
        ttl_seconds=30,
    )

    lock = AdvisoryFileLock(
        lock_path,
        ttl_seconds=1,
        context={"kind": "unit"},
        break_lock_ttl_seconds=30,
    )

    assert lock.acquire(timeout_seconds=0) is False
    assert json.loads(lock_path.read_text(encoding="utf-8"))["ownerToken"] == "stale-owner"


def test_live_target_holder_blocks_reclaim(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"
    holder = AdvisoryFileLock(lock_path, ttl_seconds=60, context={"kind": "unit"})
    contender = AdvisoryFileLock(lock_path, ttl_seconds=60, context={"kind": "unit"})

    assert holder.acquire(timeout_seconds=0) is True
    assert contender.acquire(timeout_seconds=0) is False
    assert json.loads(lock_path.read_text(encoding="utf-8"))["ownerToken"] == holder.owner_token

    holder.release()


def test_dead_target_holder_permits_reclaim(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"
    _write_lock(lock_path, owner_token="dead-target", pid=999999, seconds_ago=120, ttl_seconds=1)

    lock = AdvisoryFileLock(lock_path, ttl_seconds=1, context={"kind": "unit"})

    assert lock.acquire(timeout_seconds=0) is True
    assert json.loads(lock_path.read_text(encoding="utf-8"))["ownerToken"] == lock.owner_token

    lock.release()


def test_malformed_json_requires_grace_or_explicit_force_break(tmp_path: Path) -> None:
    lock_path = tmp_path / "resource.lock"
    lock_path.write_text("{", encoding="utf-8")

    lock = AdvisoryFileLock(
        lock_path,
        ttl_seconds=1,
        context={"kind": "unit"},
        malformed_grace_seconds=60,
    )

    assert lock.acquire(timeout_seconds=0) is False
    assert lock_path.exists()
    assert lock.force_break() is True
    assert lock.acquire(timeout_seconds=0) is True

    lock.release()
