from __future__ import annotations

from pathlib import Path

from utils.db_tool_lock import DBToolLock


def test_db_tool_lock_acquire_release(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("placeholder", encoding="utf-8")

    lock = DBToolLock(db_path, tool_name="unit-test")
    assert lock.acquire(timeout_seconds=0)
    assert lock.is_locked() is True
    holder = lock.get_holder_info()
    assert holder is not None
    assert holder["tool_name"] == "unit-test"
    lock.release()
    assert lock.is_locked() is False


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
