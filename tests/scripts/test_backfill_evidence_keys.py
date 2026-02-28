"""Tests for scripts/backfill_evidence_keys.py.

Covers:
- Row with provenance -> evidence_key populated
- Row without provenance -> stays NULL
- Duplicate group -> lowest id keeps key, others NULL (soft-archive)
- No rows deleted
- Dry-run -> no modifications
- Idempotent -> second run is safe
- Preflight clean/dirty detection
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.backfill_evidence_keys import run, preflight
from utils.evidence_key import compute_evidence_key


def _create_test_db(db_path: str) -> sqlite3.Connection:
    """Create a minimal signals DB for backfill testing."""
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


def _insert_signal(conn, source_api, url, canonical_key="domain:test.com", signal_id=None):
    """Insert a test signal with optional source_url in raw_data."""
    raw = {}
    if url:
        raw["_provenance"] = {"source_url": url}
        raw["url"] = url
    conn.execute(
        """INSERT INTO signals (signal_type, source_api, canonical_key,
           confidence, raw_data, detected_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("news_mention", source_api, canonical_key, 0.5,
         json.dumps(raw), "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()


class TestBackfillRun:
    """Test backfill_evidence_keys.run()."""

    def test_row_with_provenance_gets_key(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "https://example.com/article/1")
        conn.close()

        report = run(db_path, dry_run=False)

        assert report["rows_updated"] == 1
        assert report["rows_no_url"] == 0

        conn = sqlite3.connect(db_path)
        ek = conn.execute("SELECT evidence_key FROM signals WHERE id = 1").fetchone()[0]
        assert ek is not None
        assert len(ek) == 32
        conn.close()

    def test_row_without_url_stays_null(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        conn.execute(
            """INSERT INTO signals (signal_type, source_api, canonical_key,
               confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("funding_event", "sec_edgar", "domain:stealth.com", 0.7,
             json.dumps({"form_type": "D"}), "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        report = run(db_path, dry_run=False)

        assert report["rows_no_url"] == 1

        conn = sqlite3.connect(db_path)
        ek = conn.execute("SELECT evidence_key FROM signals WHERE id = 1").fetchone()[0]
        assert ek is None
        conn.close()

    def test_duplicate_group_soft_archive(self, tmp_path: Path):
        """Duplicate group: lowest id keeps evidence_key, others stay NULL."""
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        url = "https://example.com/article/dup"
        _insert_signal(conn, "news_api", url)  # id=1 (winner)
        _insert_signal(conn, "news_api", url)  # id=2 (loser)
        _insert_signal(conn, "news_api", url)  # id=3 (loser)
        conn.close()

        report = run(db_path, dry_run=False)

        assert report["duplicate_groups"] == 1
        assert report["rows_archived"] == 2

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT id, evidence_key FROM signals ORDER BY id"
        ).fetchall()
        # Winner gets the key
        assert rows[0][1] is not None
        # Losers stay NULL (soft-archived)
        assert rows[1][1] is None
        assert rows[2][1] is None
        conn.close()

    def test_no_rows_deleted(self, tmp_path: Path):
        """Soft-archive never deletes rows."""
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        url = "https://example.com/article/nodelete"
        _insert_signal(conn, "news_api", url)
        _insert_signal(conn, "news_api", url)
        conn.close()

        run(db_path, dry_run=False)

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert count == 2  # No deletion
        conn.close()

    def test_dry_run_no_modifications(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "https://example.com/article/dry")
        conn.close()

        report = run(db_path, dry_run=True)

        assert report["dry_run"] is True
        assert report["rows_updated"] == 1  # would-be

        conn = sqlite3.connect(db_path)
        ek = conn.execute("SELECT evidence_key FROM signals WHERE id = 1").fetchone()[0]
        assert ek is None  # Not modified
        conn.close()

    def test_idempotent(self, tmp_path: Path):
        """Second run on already-backfilled DB is safe."""
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "https://example.com/article/idem")
        conn.close()

        run(db_path, dry_run=False)
        report = run(db_path, dry_run=False)

        assert report["rows_scanned"] == 0  # Already has evidence_key


class TestPreflight:
    """Test preflight duplicate detection."""

    def test_clean_db(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "https://example.com/a")
        _insert_signal(conn, "news_api", "https://example.com/b")
        conn.close()

        report = preflight(db_path)
        assert report["clean"] is True
        assert report["duplicate_groups"] == 0

    def test_dirty_db(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "https://example.com/dup")
        _insert_signal(conn, "news_api", "https://example.com/dup")
        conn.close()

        report = preflight(db_path)
        assert report["clean"] is False
        assert report["duplicate_groups"] == 1
        assert len(report["duplicates"]) == 1
