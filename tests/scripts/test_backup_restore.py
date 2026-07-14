"""Tests for scripts/backup_db.py and scripts/restore_db.py."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.backup_db import BACKUP_PREFIX, BACKUP_SUFFIX, _rotate_backups, create_backup
from scripts.restore_db import restore_backup
from utils.db_tool_lock import DBToolLock


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_db(path: Path, *, wal: bool = False, rows: int = 5) -> Path:
    """Create a minimal test database with schema_migrations + data."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
    conn.execute("INSERT INTO schema_migrations VALUES (41)")
    conn.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, value TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO data VALUES (?, ?)", (i, f"row-{i}"))
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    return path


def _row_count(path: Path, table: str = "data") -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _corrupt_file(path: Path) -> None:
    """Overwrite file with garbage to simulate corruption."""
    path.write_bytes(b"CORRUPT" * 100)


def _run_backup_script(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    # Running the script by file path puts scripts/ (not the repo root) on
    # sys.path, so absolute imports like utils.db_path_helper fail without
    # the repo root on PYTHONPATH.
    existing_pythonpath = merged_env.get("PYTHONPATH")
    merged_env["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else str(ROOT) + os.pathsep + existing_pythonpath
    )
    if env:
        merged_env.update(env)
    return subprocess.run(
        [PYTHON, str(ROOT / "scripts" / "backup_db.py"), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=merged_env,
    )


def _read_ledger(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Backup tests
# ---------------------------------------------------------------------------

class TestCreateBackup:
    def test_creates_valid_copy(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        out_dir = tmp_path / "backups"

        backup_path = create_backup(db, out_dir, retain=7)

        assert backup_path.exists()
        assert backup_path.name.startswith(BACKUP_PREFIX)
        assert backup_path.name.endswith(BACKUP_SUFFIX)
        assert _row_count(backup_path) == 5

    def test_source_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            create_backup(tmp_path / "no-such.db", tmp_path / "backups")

    def test_rejects_invalid_retain(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")

        with pytest.raises(ValueError, match="retain must be at least 1"):
            create_backup(db, tmp_path / "backups", retain=0)

    def test_creates_output_dir(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        out_dir = tmp_path / "deeply" / "nested" / "backups"

        backup_path = create_backup(db, out_dir)
        assert out_dir.exists()
        assert backup_path.exists()

    def test_wal_mode_backup(self, tmp_path):
        """Backup works correctly with WAL-mode database."""
        db = _create_test_db(tmp_path / "signals.db", wal=True)

        # Write uncommitted data to WAL
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO data VALUES (100, 'wal-row')")
        conn.commit()

        backup_path = create_backup(db, tmp_path / "backups")

        # Backup should include WAL data
        assert _row_count(backup_path) == 6
        conn.close()

    def test_rotation_removes_oldest(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        out_dir = tmp_path / "backups"
        out_dir.mkdir()

        # Create 7 existing backups
        for i in range(7):
            (out_dir / f"{BACKUP_PREFIX}2026010{i}-120000{BACKUP_SUFFIX}").write_bytes(
                b"placeholder"
            )

        # 8th backup should trigger rotation
        create_backup(db, out_dir, retain=7)

        backups = list(out_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
        assert len(backups) == 7  # oldest removed

    def test_rotation_keeps_all_when_under_limit(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        out_dir = tmp_path / "backups"
        out_dir.mkdir()

        # Pre-create one backup with distinct timestamp
        (out_dir / f"{BACKUP_PREFIX}20260101-120000{BACKUP_SUFFIX}").write_bytes(
            b"placeholder"
        )
        create_backup(db, out_dir, retain=7)

        backups = list(out_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
        assert len(backups) == 2


class TestBackupMain:
    def test_records_success_ledger_with_backup_path_retention_and_integrity_result(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        out_dir = tmp_path / "backups"
        ledger_path = tmp_path / "ledger.jsonl"

        result = _run_backup_script(
            "--db-path",
            str(db),
            "--out-dir",
            str(out_dir),
            "--retain",
            "3",
            env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
        )

        assert result.returncode == 0
        backups = list(out_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
        assert len(backups) == 1

        entries = _read_ledger(ledger_path)
        success = next(
            entry
            for entry in entries
            if entry["tool_name"] == "backup_db" and entry["status"] == "success"
        )
        details = success["details"]
        assert details["backup_path"] == str(backups[0])
        assert details["retain"] == 3
        assert details["retained_count"] == 1
        assert details["integrity_check"] == "ok"

    def test_records_lock_blocked_ledger_when_db_tool_lock_is_held(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        ledger_path = tmp_path / "ledger.jsonl"

        lock = DBToolLock(db, tool_name="test-holder")
        assert lock.acquire(timeout_seconds=0)
        try:
            result = _run_backup_script(
                "--db-path",
                str(db),
                "--out-dir",
                str(tmp_path / "backups"),
                env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
            )
        finally:
            lock.release()

        assert result.returncode == 1
        assert not (tmp_path / "backups").exists()
        entries = _read_ledger(ledger_path)
        assert any(
            entry["tool_name"] == "backup_db" and entry["status"] == "lock_blocked"
            for entry in entries
        )

    def test_records_error_ledger_when_source_database_is_missing(self, tmp_path):
        ledger_path = tmp_path / "ledger.jsonl"
        missing_db = tmp_path / "missing.db"

        result = _run_backup_script(
            "--db-path",
            str(missing_db),
            "--out-dir",
            str(tmp_path / "backups"),
            env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
        )

        assert result.returncode == 1
        assert "Source database not found" in result.stderr
        entries = _read_ledger(ledger_path)
        error = next(
            entry
            for entry in entries
            if entry["tool_name"] == "backup_db" and entry["status"] == "error"
        )
        assert error["details"]["backup_path"] is None
        assert error["details"]["integrity_check"] is None
        assert "Source database not found" in error["details"]["error"]

    def test_records_error_ledger_when_retain_is_invalid(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        ledger_path = tmp_path / "ledger.jsonl"

        result = _run_backup_script(
            "--db-path",
            str(db),
            "--out-dir",
            str(tmp_path / "backups"),
            "--retain",
            "0",
            env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
        )

        assert result.returncode == 1
        assert "retain must be at least 1" in result.stderr
        assert not (tmp_path / "backups").exists()
        entries = _read_ledger(ledger_path)
        error = next(
            entry
            for entry in entries
            if entry["tool_name"] == "backup_db" and entry["status"] == "error"
        )
        assert error["details"]["retain"] == 0
        assert error["details"]["error"] == "retain must be at least 1"


# ---------------------------------------------------------------------------
# Restore tests
# ---------------------------------------------------------------------------

class TestRestoreBackup:
    def test_restore_matches_original(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        # Modify the original
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM data")
        conn.commit()
        conn.close()
        assert _row_count(db) == 0

        # Restore
        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            restore_backup(backup_path, db)

        assert _row_count(db) == 5

    def test_creates_pre_restore_backup(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            pre_restore = restore_backup(backup_path, db)

        assert pre_restore.exists()
        assert "pre-restore-" in pre_restore.name

    def test_corrupt_backup_rejected(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        corrupt = tmp_path / "corrupt.db"
        _corrupt_file(corrupt)

        with pytest.raises(RuntimeError, match="integrity check failed"):
            with patch("scripts.restore_db._check_api_reachable", return_value=False):
                restore_backup(corrupt, db)

    def test_backup_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            restore_backup(tmp_path / "no-such.db", tmp_path / "signals.db")

    def test_refuses_when_api_reachable_no_force(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        with patch("scripts.restore_db._check_api_reachable", return_value=True):
            with pytest.raises(RuntimeError, match="API server is running"):
                restore_backup(backup_path, db)

    def test_proceeds_when_api_unreachable_no_force(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            restore_backup(backup_path, db)

        assert _row_count(db) == 5

    def test_force_when_api_reachable(self, tmp_path, capsys):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        with patch("scripts.restore_db._check_api_reachable", return_value=True):
            restore_backup(backup_path, db, force=True)

        assert _row_count(db) == 5
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_force_when_api_unreachable(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            restore_backup(backup_path, db, force=True)

        assert _row_count(db) == 5

    def test_resolves_target_sidecars_when_checkpointable(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")
        (tmp_path / "signals.db-wal").write_bytes(b"wal")
        (tmp_path / "signals.db-shm").write_bytes(b"shm")

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            restore_backup(backup_path, db)

        assert _row_count(db) == 5
        assert not (tmp_path / "signals.db-wal").exists()
        assert not (tmp_path / "signals.db-shm").exists()

    def test_refuses_when_target_sidecars_owned_by_active_writer(self, tmp_path):
        db = _create_test_db(tmp_path / "signals.db", wal=True)
        backup_path = create_backup(db, tmp_path / "backups")

        writer = sqlite3.connect(str(db), timeout=1)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO data VALUES (100, 'wal-row')")

            with patch("scripts.restore_db._check_api_reachable", return_value=False):
                with pytest.raises(RuntimeError, match="active writer"):
                    restore_backup(backup_path, db)
        finally:
            writer.rollback()
            writer.close()

    def test_restore_to_new_path(self, tmp_path):
        """Restore works even if target DB doesn't exist yet."""
        db = _create_test_db(tmp_path / "original.db")
        backup_path = create_backup(db, tmp_path / "backups")
        new_db = tmp_path / "new_signals.db"

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            result = restore_backup(backup_path, new_db)

        assert new_db.exists()
        assert _row_count(new_db) == 5
