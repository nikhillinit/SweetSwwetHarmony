"""Tests for v48 shadow_log_metrics migration — extends shadow_log (NOT new table)."""

from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from storage.migrations.v48_shadow_log_metrics import V48_SHADOW_LOG_METRICS_DDL
from storage.signal_store import CURRENT_SCHEMA_VERSION, SignalStore


def _create_shadow_log_table(conn: sqlite3.Connection) -> None:
    """Create the prerequisite shadow_log table (from v23)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shadow_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_name TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            computed_value TEXT,
            signal_id INTEGER,
            logged_at TEXT NOT NULL
        );
    """)


def test_shadow_log_metrics_table_created(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    try:
        _create_shadow_log_table(conn)
        conn.executescript(V48_SHADOW_LOG_METRICS_DDL)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_log_metrics'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_insert_with_fk(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _create_shadow_log_table(conn)
        conn.executescript(V48_SHADOW_LOG_METRICS_DDL)

        conn.execute(
            "INSERT INTO shadow_log (feature_name, canonical_key, computed_value, logged_at) "
            "VALUES ('test_feature', 'domain:acme.com', '{\"v\":1}', '2026-02-28T00:00:00Z')"
        )
        shadow_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO shadow_log_metrics (shadow_log_id, latency_ms, api_calls_made) "
            "VALUES (?, 12.5, 3)",
            (shadow_id,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT shadow_log_id, latency_ms, api_calls_made FROM shadow_log_metrics"
        ).fetchone()
        assert row is not None
        assert row[0] == shadow_id
        assert row[1] == 12.5
        assert row[2] == 3
    finally:
        conn.close()


def test_cascade_delete(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _create_shadow_log_table(conn)
        conn.executescript(V48_SHADOW_LOG_METRICS_DDL)

        conn.execute(
            "INSERT INTO shadow_log (feature_name, canonical_key, computed_value, logged_at) "
            "VALUES ('f', 'k', '{}', '2026-01-01')"
        )
        shadow_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO shadow_log_metrics (shadow_log_id, latency_ms, api_calls_made) "
            "VALUES (?, 5.0, 1)",
            (shadow_id,),
        )
        conn.commit()

        conn.execute("DELETE FROM shadow_log WHERE id = ?", (shadow_id,))
        conn.commit()

        row = conn.execute("SELECT COUNT(*) FROM shadow_log_metrics").fetchone()
        assert row[0] == 0
    finally:
        conn.close()


def test_nullable_columns(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _create_shadow_log_table(conn)
        conn.executescript(V48_SHADOW_LOG_METRICS_DDL)

        conn.execute(
            "INSERT INTO shadow_log (feature_name, canonical_key, computed_value, logged_at) "
            "VALUES ('f', 'k', '{}', '2026-01-01')"
        )
        shadow_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # All nullable columns set to NULL
        conn.execute(
            "INSERT INTO shadow_log_metrics (shadow_log_id, latency_ms, upstream_data_version, "
            "missingness_reason, api_calls_made, error) "
            "VALUES (?, NULL, NULL, NULL, 0, NULL)",
            (shadow_id,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT latency_ms, upstream_data_version, missingness_reason, error "
            "FROM shadow_log_metrics WHERE shadow_log_id = ?",
            (shadow_id,),
        ).fetchone()
        assert row is not None
        assert all(v is None for v in row)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_signal_store_initialization_applies_v48(tmp_path):
    db_path = str(tmp_path / "store.db")
    store = SignalStore(db_path)
    await store.initialize()
    await store.close()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert row[0] == CURRENT_SCHEMA_VERSION
        assert row[0] >= 48
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_log_metrics'"
        ).fetchone()
        assert table is not None
    finally:
        conn.close()
