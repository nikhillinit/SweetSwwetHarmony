"""
SQLite Pragmas Helper for Discovery Engine

Provides consistent SQLite connection configuration across all stores.
Prevents drift by centralizing pragma settings.

Usage:
    from storage.sqlite_pragmas import apply_sqlite_pragmas
    
    db = await aiosqlite.connect(path)
    await apply_sqlite_pragmas(db)
"""

import aiosqlite
import logging

logger = logging.getLogger(__name__)


async def apply_sqlite_pragmas(
    conn: aiosqlite.Connection,
    wal: bool = True,
    busy_timeout_ms: int = 5000,
    foreign_keys: bool = True,
) -> None:
    """
    Apply standard SQLite pragmas for production use.
    
    Args:
        conn: aiosqlite connection
        wal: Enable WAL mode for concurrent read/write (default: True)
        busy_timeout_ms: Timeout in ms when database is locked (default: 5000)
        foreign_keys: Enable foreign key constraints (default: True)
    
    Pragmas applied:
        - journal_mode=WAL: Allows concurrent readers during writes
        - busy_timeout: Prevents immediate "database is locked" errors
        - foreign_keys: Enforces referential integrity
    
    Note: synchronous is left at default (FULL) for maximum durability.
    For derived/rebuildable stores, caller can explicitly set NORMAL if needed.
    """
    if foreign_keys:
        await conn.execute("PRAGMA foreign_keys = ON")
    
    if wal:
        await conn.execute("PRAGMA journal_mode = WAL")
    
    if busy_timeout_ms > 0:
        await conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    
    logger.debug(
        f"Applied SQLite pragmas: WAL={wal}, busy_timeout={busy_timeout_ms}ms, "
        f"foreign_keys={foreign_keys}"
    )
