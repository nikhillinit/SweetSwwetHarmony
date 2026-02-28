"""Tests for v46 UNIQUE partial index on evidence_key.

Covers:
- UNIQUE constraint prevents duplicate evidence_key INSERT
- Multiple NULLs allowed (partial index)
- Migration fails if duplicates exist
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _create_v45_db(db_path: str) -> sqlite3.Connection:
    """Create a signals table with v45 schema (non-unique index)."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            company_id TEXT,
            evidence_family TEXT,
            canonical_key_v2 TEXT,
            evidence_key TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_evidence_key
            ON signals(evidence_key)
            WHERE evidence_key IS NOT NULL AND evidence_key != ''
    """)
    return conn


def _apply_v46(conn: sqlite3.Connection):
    """Apply v46 migration DDL."""
    from storage.migrations.v46_evidence_key_unique import V46_EVIDENCE_KEY_UNIQUE_DDL
    conn.executescript(V46_EVIDENCE_KEY_UNIQUE_DDL)


def _insert(conn, evidence_key=None, source_api="news_api"):
    conn.execute(
        """INSERT INTO signals (signal_type, source_api, canonical_key,
           confidence, raw_data, detected_at, created_at, evidence_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("news_mention", source_api, "domain:test.com", 0.5,
         '{"url":"https://example.com"}', "2026-01-01T00:00:00",
         "2026-01-01T00:00:00", evidence_key),
    )
    conn.commit()


class TestV46UniqueConstraint:
    """Test UNIQUE partial index behavior."""

    def test_prevents_duplicate_evidence_key(self, tmp_path: Path):
        """After v46, inserting duplicate evidence_key raises IntegrityError."""
        db_path = str(tmp_path / "test.db")
        conn = _create_v45_db(db_path)
        _apply_v46(conn)

        _insert(conn, evidence_key="aaaa1111bbbb2222cccc3333dddd4444")

        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, evidence_key="aaaa1111bbbb2222cccc3333dddd4444")

        conn.close()

    def test_multiple_nulls_allowed(self, tmp_path: Path):
        """Partial index: multiple NULL evidence_keys are fine."""
        db_path = str(tmp_path / "test.db")
        conn = _create_v45_db(db_path)
        _apply_v46(conn)

        _insert(conn, evidence_key=None)
        _insert(conn, evidence_key=None)
        _insert(conn, evidence_key=None)

        count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert count == 3

        conn.close()

    def test_migration_fails_on_existing_duplicates(self, tmp_path: Path):
        """v46 migration fails if duplicate evidence_keys exist (safety gate)."""
        db_path = str(tmp_path / "test.db")
        conn = _create_v45_db(db_path)

        # Insert duplicates BEFORE v46
        _insert(conn, evidence_key="aaaa1111bbbb2222cccc3333dddd4444")
        _insert(conn, evidence_key="aaaa1111bbbb2222cccc3333dddd4444")

        with pytest.raises(sqlite3.IntegrityError):
            _apply_v46(conn)

        conn.close()
