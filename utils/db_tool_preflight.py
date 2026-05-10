"""Preflight helpers for DB tooling."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def read_sqlite_data_version(db_path: str | Path) -> int:
    """Read SQLite's cheap connection-local data_version counter."""
    if str(db_path) != ":memory:" and not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA data_version").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()
