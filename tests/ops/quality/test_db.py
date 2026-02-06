"""Tier 1 Foundation -- Quality Ops db.py helper tests.

Verifies connection setup (WAL, FK, row_factory), table creation via
quality_conn, and JSON serialization utilities.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ops.quality.db import connect, dumps_json, ensure_quality_tables, loads_json, quality_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def _create_signals_table(conn: sqlite3.Connection) -> None:
    """Create a minimal signals parent table so FK constraints can resolve."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT,
            source_api TEXT,
            canonical_key TEXT,
            company_name TEXT,
            confidence REAL,
            raw_data TEXT,
            detected_at TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# quality_conn context manager
# ---------------------------------------------------------------------------

class TestQualityConn:
    """Tests for the quality_conn context manager."""

    def test_quality_conn_enables_wal(self, tmp_path):
        """quality_conn must set journal_mode to WAL."""
        db = str(tmp_path / "wal.db")
        # Pre-create signals so FK references are valid
        raw = sqlite3.connect(db)
        _create_signals_table(raw)
        raw.close()

        with quality_conn(db) as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert mode.lower() == "wal"

    def test_quality_conn_enables_fk(self, tmp_path):
        """quality_conn must enable foreign_keys."""
        db = str(tmp_path / "fk.db")
        raw = sqlite3.connect(db)
        _create_signals_table(raw)
        raw.close()

        with quality_conn(db) as conn:
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            assert fk == 1

    def test_quality_conn_creates_tables(self, tmp_path):
        """quality_conn must create the 3 quality tables on entry."""
        db = str(tmp_path / "tables.db")
        raw = sqlite3.connect(db)
        _create_signals_table(raw)
        raw.close()

        with quality_conn(db) as conn:
            tables = _table_names(conn)

        expected = {"notion_status_events", "quality_feedback", "signal_quality_metrics"}
        assert expected.issubset(tables)

    def test_quality_conn_lifecycle(self, tmp_path):
        """Opening and closing quality_conn must not raise."""
        db = str(tmp_path / "lifecycle.db")
        raw = sqlite3.connect(db)
        _create_signals_table(raw)
        raw.close()

        with quality_conn(db) as conn:
            # Perform a simple operation to verify usability
            conn.execute("SELECT 1;")
        # Connection is closed here -- no exception expected.


# ---------------------------------------------------------------------------
# connect() standalone function
# ---------------------------------------------------------------------------

class TestConnect:
    """Tests for the connect() function."""

    def test_connect_returns_row_factory(self, tmp_path):
        """connect() must set row_factory to sqlite3.Row."""
        db = str(tmp_path / "rowfactory.db")
        conn = connect(db)
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

class TestDumpsJson:
    """Tests for dumps_json."""

    def test_dumps_json_none(self):
        """dumps_json(None) returns None."""
        assert dumps_json(None) is None

    def test_dumps_json_dict(self):
        """dumps_json with a dict returns a valid JSON string."""
        result = dumps_json({"a": 1, "b": "hello"})
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": "hello"}


class TestLoadsJson:
    """Tests for loads_json."""

    def test_loads_json_none(self):
        """loads_json(None) returns None."""
        assert loads_json(None) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
