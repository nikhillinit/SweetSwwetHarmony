import sqlite3
from pathlib import Path

import pytest

from scripts.run_migration import MigrationError, MigrationRunner


def make_v51_db(tmp_path: Path) -> Path:
    db = tmp_path / "signals.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, collector TEXT)")
    con.execute("CREATE TABLE schema_version (version INTEGER)")
    con.execute("INSERT INTO schema_version VALUES (51)")
    con.commit()
    con.close()
    return db


def test_migration_adds_v52_columns(tmp_path):
    db = make_v51_db(tmp_path)
    MigrationRunner(db, target_version=52).run()
    con = sqlite3.connect(db)
    cols = [row[1] for row in con.execute("PRAGMA table_info(signals)").fetchall()]
    con.close()
    assert "rows_returned_this_iter" in cols
    assert "rows_after_filter_this_iter" in cols
    assert "last_failure_mode" in cols


def test_migration_bumps_schema_version(tmp_path):
    db = make_v51_db(tmp_path)
    MigrationRunner(db, target_version=52).run()
    con = sqlite3.connect(db)
    v = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    assert v == 52


def test_migration_fails_if_writer_detected(tmp_path):
    db = make_v51_db(tmp_path)
    writer_con = sqlite3.connect(db)
    writer_con.execute("BEGIN EXCLUSIVE")
    runner = MigrationRunner(db, target_version=52, writer_check_timeout=0.1)
    with pytest.raises(MigrationError, match="active writer"):
        runner.run()
    writer_con.rollback()
    writer_con.close()


def test_migration_is_idempotent(tmp_path):
    db = make_v51_db(tmp_path)
    MigrationRunner(db, target_version=52).run()
    MigrationRunner(db, target_version=52).run()
    con = sqlite3.connect(db)
    v = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    assert v == 52
