"""Tests for scripts/backup_db.py and scripts/restore_db.py."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.backup_db import create_backup, _rotate_backups, BACKUP_PREFIX, BACKUP_SUFFIX
from scripts.restore_db import restore_backup


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

    def test_restore_to_new_path(self, tmp_path):
        """Restore works even if target DB doesn't exist yet."""
        db = _create_test_db(tmp_path / "original.db")
        backup_path = create_backup(db, tmp_path / "backups")
        new_db = tmp_path / "new_signals.db"

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            result = restore_backup(backup_path, new_db)

        assert new_db.exists()
        assert _row_count(new_db) == 5
