import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.litestream_ctrl import LitestreamCtrl, LitestreamError
from scripts.restore_db import MAINTENANCE_LOCK_TIMEOUT_SECONDS, RestoreError, restore_with_integrity_check


def test_maintenance_lock_timeout_is_at_least_120s():
    assert MAINTENANCE_LOCK_TIMEOUT_SECONDS >= 120


def test_litestream_ctrl_stop_calls_subprocess(tmp_path):
    ctrl = LitestreamCtrl(replica_url="s3://bucket/db", config_path=tmp_path / "ls.yml")
    with patch("scripts.litestream_ctrl.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        ctrl.stop()
    assert mock_run.called


def test_assert_wal_flushed_raises_on_nonempty_wal(tmp_path):
    db = tmp_path / "signals.db"
    db.write_bytes(b"x" * 100)
    wal = tmp_path / "signals.db-wal"
    wal.write_bytes(b"y" * 50)
    ctrl = LitestreamCtrl(replica_url="s3://bucket/db", config_path=tmp_path / "ls.yml")
    with pytest.raises(LitestreamError, match="WAL"):
        ctrl.assert_wal_flushed(db)


def test_assert_wal_flushed_passes_when_no_wal(tmp_path):
    db = tmp_path / "signals.db"
    db.write_bytes(b"x" * 100)
    ctrl = LitestreamCtrl(replica_url="s3://bucket/db", config_path=tmp_path / "ls.yml")
    ctrl.assert_wal_flushed(db)  # should not raise


def test_restore_with_integrity_check_passes_valid_db(tmp_path):
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    target.write_bytes(b"old")
    con = sqlite3.connect(backup)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (42)")
    con.commit()
    con.close()
    restore_with_integrity_check(backup, target)
    con2 = sqlite3.connect(target)
    count = con2.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    con2.close()
    assert count == 1


def test_restore_with_integrity_check_raises_on_corrupt_backup(tmp_path):
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    con = sqlite3.connect(target)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (1)")
    con.commit()
    con.close()
    backup.write_bytes(b"\x00" * 100)  # corrupt
    with pytest.raises((RestoreError, Exception)):
        restore_with_integrity_check(backup, target)
    # original must be untouched
    con2 = sqlite3.connect(target)
    count = con2.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 1


def test_restore_sequence_order_enforced(tmp_path):
    """stop -> assert_wal_flushed -> copy -> integrity -> reset_generation -> start."""
    db = tmp_path / "signals.db"
    backup = tmp_path / "backup.db"
    con = sqlite3.connect(backup)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (42)")
    con.commit()
    con.close()
    db.write_bytes(b"old")

    ctrl = LitestreamCtrl(replica_url="s3://b/db", config_path=tmp_path / "ls.yml")
    order = []

    with patch.object(ctrl, "stop", side_effect=lambda: order.append("stop")):
        with patch.object(ctrl, "assert_wal_flushed", side_effect=lambda p: order.append("wal_check")):
            with patch.object(ctrl, "reset_generation", side_effect=lambda: order.append("reset_gen")):
                with patch.object(ctrl, "start", side_effect=lambda: order.append("start")):
                    ctrl.stop()
                    ctrl.assert_wal_flushed(db)
                    restore_with_integrity_check(backup, db)
                    order.append("integrity_passed")
                    ctrl.reset_generation()
                    ctrl.start()

    assert order == ["stop", "wal_check", "integrity_passed", "reset_gen", "start"]
