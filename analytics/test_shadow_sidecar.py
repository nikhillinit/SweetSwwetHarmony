"""Tests for analytics.shadow_sidecar — verifies the safety contract."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from analytics.shadow_sidecar import (
    ReadMode,
    ShadowSidecar,
    ShadowSidecarConfig,
    ShadowSidecarError,
    UnsafeWriteError,
)


def _make_fake_signals_db(path: Path) -> None:
    """Create a tiny fake signals.db with one row for testing."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE signals (
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
        conn.execute(
            "INSERT INTO signals (signal_type, source_api, canonical_key, "
            "company_name, confidence, raw_data, detected_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "domain_registration",
                "domain_whois",
                "domain:test.ai",
                "Test AI",
                0.6,
                "{}",
                "2026-04-06T00:00:00Z",
                "2026-04-06T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def isolated_sidecar(tmp_path: Path) -> ShadowSidecar:
    """Build a sidecar with an isolated production+shadow DB."""
    prod_db = tmp_path / "fake_signals.db"
    shadow_db = tmp_path / "shadow" / "discovery.db"
    snapshot_db = tmp_path / "shadow" / "signals_snapshot.db"
    _make_fake_signals_db(prod_db)
    cfg = ShadowSidecarConfig(
        production_db=prod_db,
        shadow_db=shadow_db,
        snapshot_db=snapshot_db,
        read_mode=ReadMode.IMMUTABLE_URI,
        register_dbtool_lock=False,  # avoid lock interference in tests
    )
    return ShadowSidecar(cfg)


# ---- Initialization --------------------------------------------------------


def test_initialize_creates_shadow_db_and_schema(isolated_sidecar):
    isolated_sidecar.initialize()
    try:
        assert isolated_sidecar.config.shadow_db.exists()
        rows = isolated_sidecar.shadow_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {r["name"] for r in rows}
        assert "shadow_signals" in names
        assert "shadow_episodes" in names
        assert "shadow_runs" in names
    finally:
        isolated_sidecar.close()


def test_double_initialize_is_idempotent(isolated_sidecar):
    isolated_sidecar.initialize()
    isolated_sidecar.initialize()  # must not raise
    isolated_sidecar.close()


def test_context_manager_lifecycle(isolated_sidecar):
    with isolated_sidecar as s:
        assert s.config.shadow_db.exists()
        s.shadow_write(
            "INSERT INTO shadow_signals (shadow_collector, raw_data, "
            "detected_at, created_at) VALUES (?, ?, ?, ?)",
            ("test", "{}", "2026-04-06T00:00:00Z", "2026-04-06T00:00:00Z"),
        )


# ---- Production-DB read safety contract -----------------------------------


def test_production_read_returns_data(isolated_sidecar):
    with isolated_sidecar as s:
        with s.production_read_connection() as conn:
            rows = conn.execute("SELECT id, source_api FROM signals").fetchall()
            assert len(rows) == 1
            assert rows[0]["source_api"] == "domain_whois"


def test_production_read_is_readonly_immutable(isolated_sidecar):
    """Critical: writing to the production read connection MUST raise.

    This is the heart of the P1 safety contract.
    """
    with isolated_sidecar as s:
        with s.production_read_connection() as conn:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO signals (signal_type, source_api, "
                    "canonical_key, confidence, raw_data, detected_at, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "x", "y", "z", 0.0, "{}",
                        "2026-04-06T00:00:00Z",
                        "2026-04-06T00:00:00Z",
                    ),
                )


def test_snapshot_mode_creates_snapshot_file(tmp_path: Path):
    prod = tmp_path / "fake_signals.db"
    shadow = tmp_path / "shadow" / "discovery.db"
    snap = tmp_path / "shadow" / "signals_snapshot.db"
    _make_fake_signals_db(prod)
    cfg = ShadowSidecarConfig(
        production_db=prod,
        shadow_db=shadow,
        snapshot_db=snap,
        read_mode=ReadMode.SNAPSHOT,
        register_dbtool_lock=False,
    )
    with ShadowSidecar(cfg) as s:
        assert snap.exists()
        with s.production_read_connection() as conn:
            rows = conn.execute("SELECT count(*) AS n FROM signals").fetchall()
            assert rows[0]["n"] == 1


def test_snapshot_mode_read_does_not_touch_live_db(tmp_path: Path):
    """In snapshot mode, deleting the live DB after snapshot must not break reads."""
    prod = tmp_path / "fake_signals.db"
    shadow = tmp_path / "shadow" / "discovery.db"
    snap = tmp_path / "shadow" / "signals_snapshot.db"
    _make_fake_signals_db(prod)
    cfg = ShadowSidecarConfig(
        production_db=prod,
        shadow_db=shadow,
        snapshot_db=snap,
        read_mode=ReadMode.SNAPSHOT,
        register_dbtool_lock=False,
    )
    with ShadowSidecar(cfg) as s:
        prod.unlink()  # Live DB gone — snapshot reads should still work
        with s.production_read_connection() as conn:
            rows = conn.execute("SELECT count(*) AS n FROM signals").fetchall()
            assert rows[0]["n"] == 1


# ---- Shadow-write safety contract ------------------------------------------


def test_shadow_write_refuses_sql_referencing_production_db(isolated_sidecar):
    """If a SQL statement contains the production DB path, refuse to execute."""
    with isolated_sidecar as s:
        prod_path_str = str(isolated_sidecar.config.production_db)
        evil_sql = (
            f"INSERT INTO shadow_signals (shadow_collector, raw_data, "
            f"detected_at, created_at) VALUES ('test', "
            f"'-- {prod_path_str}', '2026-04-06T00:00:00Z', '2026-04-06T00:00:00Z')"
        )
        with pytest.raises(UnsafeWriteError):
            s.shadow_write(evil_sql)


def test_shadow_write_persists_in_shadow_db(isolated_sidecar):
    with isolated_sidecar as s:
        s.shadow_write(
            "INSERT INTO shadow_signals (shadow_collector, raw_data, "
            "detected_at, created_at) VALUES (?, ?, ?, ?)",
            ("shadow_ct_log", "{}", "2026-04-06T00:00:00Z", "2026-04-06T00:00:00Z"),
        )
        rows = s.shadow_query(
            "SELECT shadow_collector FROM shadow_signals"
        )
        assert len(rows) == 1
        assert rows[0]["shadow_collector"] == "shadow_ct_log"


def test_begin_and_end_run_lifecycle(isolated_sidecar):
    with isolated_sidecar as s:
        s.begin_run(collector="shadow_ct_log", run_id="run_001")
        s.end_run(run_id="run_001", items_collected=42)
        rows = s.shadow_query(
            "SELECT collector, items_collected, completed_at FROM shadow_runs"
        )
        assert len(rows) == 1
        assert rows[0]["collector"] == "shadow_ct_log"
        assert rows[0]["items_collected"] == 42
        assert rows[0]["completed_at"] is not None


# ---- Lifecycle errors ------------------------------------------------------


def test_query_before_initialize_raises(isolated_sidecar):
    with pytest.raises(ShadowSidecarError):
        isolated_sidecar.shadow_query("SELECT 1")


def test_production_read_before_initialize_raises(isolated_sidecar):
    with pytest.raises(ShadowSidecarError):
        with isolated_sidecar.production_read_connection() as _:
            pass
