"""
SQLite helpers for Quality Ops.

Design goals:
- Safe to call repeatedly (idempotent DDL)
- Uses sqlite3 (sync) so it works in ops CLI and small scripts
- Avoids importing large pipeline modules unless needed
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ops.quality.schema import QUALITY_TABLES_DDL


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """
    Create a sqlite3 connection with sensible defaults for ops tooling.
    """
    p = str(db_path)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    # Basic pragmas for safety / concurrency with the async pipeline.
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def ensure_quality_tables(conn: sqlite3.Connection) -> None:
    """
    Ensure all Quality Ops tables exist.
    """
    conn.executescript(QUALITY_TABLES_DDL)
    conn.commit()


@contextmanager
def quality_conn(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        ensure_quality_tables(conn)
        yield conn
    finally:
        conn.close()


def dumps_json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        # Best-effort: stringify unknown types
        return json.dumps(str(obj), ensure_ascii=False)


def loads_json(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
