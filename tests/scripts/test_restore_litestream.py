"""Litestream lifecycle position: Mode B (orchestration out of scope).

This file deliberately does NOT prove a working Litestream 0.5.2 lifecycle —
there is none in this deployment. It asserts the explicit Mode B contract:

  * the restore path declares litestream_mode "off" and records it in the ledger
    (the ledger assertions live in tests/scripts/test_restore_db.py);
  * the old controller is quarantined and cannot be driven as if 0.5.2 stop /
    generations / reset commands were supported;
  * the genuinely-safe restore primitives (maintenance lock timeout, backup
    integrity gate) still behave correctly.

S3/R2 cloud-restore durability is proven separately by
.github/workflows/litestream-restore-verify-nightly.yml and its contract test.
"""
import sqlite3

import pytest

import scripts.litestream_ctrl as litestream_ctrl
import scripts.restore_db as restore_db
from scripts.litestream_ctrl import LitestreamCtrl, LitestreamUnsupportedError
from scripts.restore_db import (
    LITESTREAM_MODE,
    MAINTENANCE_LOCK_TIMEOUT_SECONDS,
    RestoreError,
    restore_with_integrity_check,
)


# --- Mode B contract ---------------------------------------------------------


def test_restore_db_litestream_mode_is_off():
    assert LITESTREAM_MODE == "off"
    assert restore_db.SUPPORTED_LITESTREAM_MODES == ("off",)


def test_controller_module_declares_mode_off():
    assert litestream_ctrl.LITESTREAM_MODE == "off"


def test_litestream_ctrl_is_quarantined():
    # Constructing the controller must fail loudly — the 0.5.2 stop/generations
    # commands cannot drive the lifecycle and must not be callable as if they can.
    with pytest.raises(LitestreamUnsupportedError):
        LitestreamCtrl(replica_url="s3://bucket/db", config_path="ls.yml")


def test_restore_helper_rejects_unsupported_litestream_mode(tmp_path):
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    backup.write_bytes(b"x")
    target.write_bytes(b"y")
    with pytest.raises(RestoreError, match="not supported"):
        restore_db.restore_backup_with_lock_and_ledger(
            backup, target, litestream_mode="required"
        )


# --- still-valid restore primitives (not Litestream orchestration) -----------


def test_maintenance_lock_timeout_is_at_least_120s():
    assert MAINTENANCE_LOCK_TIMEOUT_SECONDS >= 120


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
    # original must be untouched (rollback-safe)
    con2 = sqlite3.connect(target)
    count = con2.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    con2.close()
    assert count == 1
