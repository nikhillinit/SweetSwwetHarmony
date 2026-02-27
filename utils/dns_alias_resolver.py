"""
DNS promotion alias resolver — shared module for PR10b integration points.

Consumed by:
  1. workflows/notion_pusher.py :: _group_by_canonical_key()
  2. collectors/base.py :: _extract_canonical_key()

All functions accept a raw sqlite3 or aiosqlite connection and handle
pre-v44 databases gracefully (table may not exist).
"""

from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

_TABLE = "dns_promotion_aliases"


def _table_exists(conn) -> bool:
    """Check if dns_promotion_aliases table exists (sync connection)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def resolve_alias(key: str, conn) -> str:
    """Single-hop alias lookup. Returns target_key if alias exists and is
    enabled, otherwise returns the original key unchanged.

    Gracefully handles pre-v44 databases where the table doesn't exist.
    """
    if not _table_exists(conn):
        return key
    row = conn.execute(
        "SELECT target_key FROM dns_promotion_aliases "
        "WHERE alias_key = ? AND enabled = 1",
        (key,),
    ).fetchone()
    return row[0] if row else key


def resolve_aliases_batch(keys: List[str], conn) -> Dict[str, str]:
    """Batch alias resolution. Returns dict mapping each input key to its
    resolved target (or itself if no alias).
    """
    if not keys:
        return {}
    if not _table_exists(conn):
        return {k: k for k in keys}

    result = {k: k for k in keys}
    # Use parameterized IN query
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT alias_key, target_key FROM dns_promotion_aliases "
        f"WHERE alias_key IN ({placeholders}) AND enabled = 1",
        keys,
    ).fetchall()
    for alias_key, target_key in rows:
        result[alias_key] = target_key
    return result


async def _async_table_exists(conn) -> bool:
    """Check if dns_promotion_aliases table exists (async connection)."""
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        )
        row = await cursor.fetchone()
        return row is not None
    except Exception:
        return False


async def resolve_aliases_batch_async(keys: List[str], conn) -> Dict[str, str]:
    """Async batch alias resolution for aiosqlite connections."""
    if not keys:
        return {}
    if not await _async_table_exists(conn):
        return {k: k for k in keys}

    result = {k: k for k in keys}
    placeholders = ",".join("?" for _ in keys)
    cursor = await conn.execute(
        f"SELECT alias_key, target_key FROM dns_promotion_aliases "
        f"WHERE alias_key IN ({placeholders}) AND enabled = 1",
        keys,
    )
    rows = await cursor.fetchall()
    for alias_key, target_key in rows:
        result[alias_key] = target_key
    return result


def record_alias(old_key: str, new_key: str, source: str, conn) -> None:
    """UPSERT an alias mapping. Re-enables disabled aliases on re-record."""
    conn.execute(
        """
        INSERT INTO dns_promotion_aliases (alias_key, target_key, source, enabled, updated_at)
        VALUES (?, ?, ?, 1, datetime('now'))
        ON CONFLICT(alias_key) DO UPDATE SET
            target_key = excluded.target_key,
            source = excluded.source,
            enabled = 1,
            updated_at = datetime('now')
        """,
        (old_key, new_key, source),
    )
    conn.commit()


def rollback_aliases(conn) -> int:
    """Disable all enabled aliases. Returns count of aliases disabled.

    Gracefully handles pre-v44 databases where the table doesn't exist.
    """
    if not _table_exists(conn):
        return 0
    cursor = conn.execute(
        "UPDATE dns_promotion_aliases SET enabled = 0, updated_at = datetime('now') "
        "WHERE enabled = 1"
    )
    count = cursor.rowcount
    conn.commit()
    return count
