"""Tests for scripts/backfill_company_extraction.py.

Covers:
- Preflight summary statistics
- Dry-run produces diffs without modifying DB
- Commit mode updates company_name, canonical_key, raw_data
- Backfill flag set in raw_data
- Signals with no improvement are unchanged
- Error handling for malformed raw_data
- Idempotent: re-run on already-backfilled DB is safe
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.backfill_company_extraction import _parse_allowlist_ids, preflight, run


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
    return conn


def _insert_signal(
    conn,
    source_api: str,
    title: str,
    description: str = "",
    company_name: str = None,
    canonical_key: str = "rss_abc123",
):
    """Insert a test signal with title/description in raw_data."""
    raw = {"title": title, "description": description, "url": "https://example.com/article"}
    conn.execute(
        """INSERT INTO signals (signal_type, source_api, canonical_key,
           company_name, confidence, raw_data, detected_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("news_mention", source_api, canonical_key, company_name, 0.5,
         json.dumps(raw), "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()


class TestPreflight:
    """Test preflight summary statistics."""

    def test_preflight_counts(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "Acme raises $5M",
                       canonical_key="name_loc:acme", company_name="Acme")
        _insert_signal(conn, "rss_feeds", "Some article",
                       canonical_key="rss_hash123", company_name="SomeCo")
        _insert_signal(conn, "rss_feeds", "Another one", canonical_key="rss_hash456",
                       company_name=None)
        conn.close()

        report = preflight(db_path)
        assert report["total_news_rss_signals"] == 3
        assert report["hash_canonical_keys"] == 2
        assert report["name_loc_keys"] == 1
        assert report["missing_company_name"] == 1

    def test_preflight_excludes_other_sources(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "Acme raises $5M")
        # github signal should not be counted
        conn.execute(
            """INSERT INTO signals (signal_type, source_api, canonical_key,
               confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("github_trending", "github", "domain:acme.com", 0.5,
             json.dumps({"title": "test"}), "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        report = preflight(db_path)
        assert report["total_news_rss_signals"] == 1


class TestAllowlistParsing:
    def test_none_or_empty_returns_none(self):
        assert _parse_allowlist_ids(None) is None
        assert _parse_allowlist_ids("") is None
        assert _parse_allowlist_ids("   ") is None

    def test_valid_csv_parses_to_set(self):
        ids = _parse_allowlist_ids("41, 95,210,95")
        assert ids == {41, 95, 210}

    def test_invalid_token_raises(self):
        with pytest.raises(ValueError):
            _parse_allowlist_ids("41,abc,95")


class TestBackfillRun:
    """Test backfill run() function."""

    def test_dry_run_no_modifications(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "FreshBowl raises $5M in seed round",
                       canonical_key="rss_abc123", company_name=None)
        conn.close()

        report = run(db_path, dry_run=True)

        assert report["dry_run"] is True
        assert report["updated"] >= 1
        assert len(report["diffs"]) >= 1

        # Verify DB was NOT modified
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT company_name, canonical_key FROM signals WHERE id = 1"
        ).fetchone()
        assert row[0] is None  # company_name unchanged
        assert row[1] == "rss_abc123"  # key unchanged
        conn.close()

    def test_commit_updates_company_name(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "FreshBowl raises $5M in seed round",
                       canonical_key="rss_abc123", company_name=None)
        conn.close()

        report = run(db_path, dry_run=False)

        assert report["updated"] >= 1

        # Verify DB was updated
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT company_name, canonical_key, raw_data FROM signals WHERE id = 1"
        ).fetchone()
        assert row[0] == "FreshBowl"
        assert row[1].startswith("name_loc:")
        raw = json.loads(row[2])
        assert raw.get("_backfill_extraction") is True
        conn.close()

    def test_unchanged_signal_not_updated(self, tmp_path: Path):
        """Signal where extraction already matches should not be updated."""
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "Acme raises $5M Series A",
                       canonical_key="name_loc:acme", company_name="Acme")
        conn.close()

        report = run(db_path, dry_run=True)

        assert report["unchanged"] >= 1

    def test_malformed_raw_data_counted_as_error(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        conn.execute(
            """INSERT INTO signals (signal_type, source_api, canonical_key,
               confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("news_mention", "news_api", "rss_bad", 0.5,
             "NOT_VALID_JSON", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        report = run(db_path, dry_run=True)

        assert report["errors"] == 1

    def test_backfill_old_values_preserved_in_raw_data(self, tmp_path: Path):
        """When company_name changes, old value is saved in raw_data."""
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "FreshBowl raises $5M",
                       canonical_key="rss_oldhash", company_name="WrongName")
        conn.close()

        report = run(db_path, dry_run=False)

        conn = sqlite3.connect(db_path)
        raw = json.loads(
            conn.execute("SELECT raw_data FROM signals WHERE id = 1").fetchone()[0]
        )
        assert raw.get("_backfill_old_company_name") == "WrongName"
        assert raw.get("_backfill_old_canonical_key") == "rss_oldhash"
        conn.close()

    def test_idempotent_second_run(self, tmp_path: Path):
        """Second run on already-backfilled DB is safe."""
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "FreshBowl raises $5M",
                       canonical_key="rss_abc123", company_name=None)
        conn.close()

        run(db_path, dry_run=False)
        report = run(db_path, dry_run=False)

        # Second run should find the already-correct name and not update
        assert report["unchanged"] >= 1

    def test_allowlist_limits_scope_dry_run(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "FreshBowl raises $5M", canonical_key="rss_a")
        _insert_signal(conn, "news_api", "Acme raises $10M", canonical_key="rss_b")
        conn.close()

        report = run(db_path, dry_run=True, allowlist_ids={1})

        assert report["total"] == 1
        assert report["scanned"] == 1
        assert report["updated"] == 1
        assert report["allowlist_count"] == 1
        assert len(report["diffs"]) == 1
        assert report["diffs"][0]["id"] == 1

    def test_allowlist_commit_updates_only_selected_ids(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = _create_test_db(db_path)
        _insert_signal(conn, "news_api", "FreshBowl raises $5M", canonical_key="rss_a")
        _insert_signal(conn, "news_api", "Acme raises $10M", canonical_key="rss_b")
        conn.close()

        report = run(db_path, dry_run=False, allowlist_ids={2})
        assert report["total"] == 1
        assert report["updated"] == 1

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT id, company_name FROM signals ORDER BY id"
        ).fetchall()
        conn.close()

        # ID 1 untouched; ID 2 updated by allowlist-constrained run.
        assert rows == [(1, None), (2, "Acme")]
