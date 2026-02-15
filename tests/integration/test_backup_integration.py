"""
Backup integration tests.

Verifies backup/restore preserves data integrity across pipeline-like operations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.backup_db import create_backup
from scripts.restore_db import restore_backup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_populated_db(path: Path, *, wal: bool = True) -> Path:
    """Create a DB with schema_migrations, signals, and review data."""
    conn = sqlite3.connect(str(path))
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
    conn.execute("INSERT INTO schema_migrations VALUES (41)")

    conn.execute(
        "CREATE TABLE signals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "canonical_key TEXT NOT NULL, "
        "company_name TEXT, "
        "confidence REAL, "
        "source_api TEXT, "
        "created_at TEXT)"
    )

    conn.execute(
        "CREATE TABLE review_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "company_id TEXT, "
        "status TEXT, "
        "created_at TEXT)"
    )

    conn.execute(
        "CREATE TABLE company_files ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "company_id TEXT, "
        "status TEXT, "
        "created_at TEXT)"
    )

    conn.execute(
        "CREATE TABLE canary_runs ("
        "id INTEGER PRIMARY KEY, verdict TEXT, pass_rate REAL, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE canary_drift_alerts ("
        "id INTEGER PRIMARY KEY, severity TEXT, status TEXT)"
    )

    # Populate with test data
    for i in range(10):
        conn.execute(
            "INSERT INTO signals (canonical_key, company_name, confidence, source_api, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"domain:company{i}.com", f"Company {i}", 0.5 + i * 0.05, "github", "2026-01-15T12:00:00Z"),
        )

    for i in range(5):
        conn.execute(
            "INSERT INTO review_items (company_id, status, created_at) VALUES (?, ?, ?)",
            (f"cid-{i:016x}", "pending", "2026-01-15T12:00:00Z"),
        )

    for i in range(3):
        conn.execute(
            "INSERT INTO company_files (company_id, status, created_at) VALUES (?, ?, ?)",
            (f"cid-{i:016x}", "thin", "2026-01-15T12:00:00Z"),
        )

    conn.commit()
    conn.close()
    return path


def _count_rows(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _get_signal_keys(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT canonical_key FROM signals ORDER BY id").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 1: Pipeline run -> backup -> restore -> verify integrity
# ---------------------------------------------------------------------------

class TestBackupRestoreIntegrity:
    def test_round_trip_preserves_all_data(self, tmp_path):
        """Backup + restore round-trip produces identical data."""
        db = _create_populated_db(tmp_path / "signals.db")
        backup_dir = tmp_path / "backups"

        # Record original state
        orig_signals = _count_rows(db, "signals")
        orig_reviews = _count_rows(db, "review_items")
        orig_companies = _count_rows(db, "company_files")
        orig_keys = _get_signal_keys(db)

        # Create backup
        backup_path = create_backup(db, backup_dir)

        # Modify the DB (simulate pipeline activity)
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM signals WHERE id <= 3")
        conn.execute("INSERT INTO signals (canonical_key, company_name, confidence, source_api, created_at) "
                      "VALUES ('domain:new.com', 'New Co', 0.9, 'news', '2026-01-16T00:00:00Z')")
        conn.commit()
        conn.close()

        # Verify DB was modified
        assert _count_rows(db, "signals") != orig_signals

        # Restore
        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            restore_backup(backup_path, db)

        # Verify round-trip integrity
        assert _count_rows(db, "signals") == orig_signals
        assert _count_rows(db, "review_items") == orig_reviews
        assert _count_rows(db, "company_files") == orig_companies
        assert _get_signal_keys(db) == orig_keys

    def test_schema_version_preserved(self, tmp_path):
        """Schema version survives backup/restore."""
        db = _create_populated_db(tmp_path / "signals.db")
        backup_path = create_backup(db, tmp_path / "backups")

        with patch("scripts.restore_db._check_api_reachable", return_value=False):
            restore_backup(backup_path, db)

        conn = sqlite3.connect(str(db))
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        conn.close()
        assert version == 41


# ---------------------------------------------------------------------------
# Test 2: Backup during active shadow resolution -> data consistent
# ---------------------------------------------------------------------------

class TestBackupDuringShadow:
    def test_backup_during_concurrent_writes(self, tmp_path):
        """Backup taken while WAL has pending transactions is consistent."""
        db = _create_populated_db(tmp_path / "signals.db", wal=True)

        # Open a connection and begin writing (simulating shadow resolution)
        writer = sqlite3.connect(str(db))
        writer.execute("INSERT INTO review_items (company_id, status, created_at) "
                       "VALUES ('shadow-001', 'pending', '2026-01-15T13:00:00Z')")
        writer.commit()  # Committed to WAL but not checkpointed

        # Take backup while writer connection is open
        backup_path = create_backup(db, tmp_path / "backups")

        # Verify backup has the WAL data
        assert _count_rows(backup_path, "review_items") == 6  # 5 original + 1 new
        writer.close()

    def test_backup_integrity_after_multiple_writes(self, tmp_path):
        """Multiple writes between backups don't corrupt."""
        db = _create_populated_db(tmp_path / "signals.db", wal=True)

        # First backup (manual name to avoid timestamp collision)
        backup1_dir = tmp_path / "backups1"
        backup1 = create_backup(db, backup1_dir)
        assert _count_rows(backup1, "signals") == 10

        # More writes
        conn = sqlite3.connect(str(db))
        for i in range(20, 30):
            conn.execute(
                "INSERT INTO signals (canonical_key, company_name, confidence, source_api, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"domain:batch{i}.com", f"Batch {i}", 0.6, "sec_edgar", "2026-01-16T00:00:00Z"),
            )
        conn.commit()
        conn.close()

        # Second backup (separate dir to avoid overwrite)
        backup2_dir = tmp_path / "backups2"
        backup2 = create_backup(db, backup2_dir)
        assert _count_rows(backup2, "signals") == 20  # 10 original + 10 new

        # First backup still has original data
        assert _count_rows(backup1, "signals") == 10
