"""
Notion status event capture.

We use the existing "sync_suppression" pipeline step to pull current Notion statuses
into suppression_cache. We then compute diffs (before vs after) and record them as
events in notion_status_events.

This gives us a time-series of status changes without needing Notion change history APIs.
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from ops.quality.db import dumps_json, utc_now_iso


@dataclass(frozen=True)
class StatusEventInsertStats:
    observed_at: str
    events_inserted: int
    new_keys: int
    changed_keys: int


def _load_suppression_statuses(conn: sqlite3.Connection) -> Dict[str, Tuple[str, Optional[str]]]:
    """
    Returns dict canonical_key -> (status, notion_page_id)
    """
    rows = conn.execute(
        "SELECT canonical_key, status, notion_page_id FROM suppression_cache"
    ).fetchall()

    out: Dict[str, Tuple[str, Optional[str]]] = {}
    for r in rows:
        out[str(r["canonical_key"])] = (str(r["status"]), str(r["notion_page_id"]) if r["notion_page_id"] else None)
    return out


def insert_status_event(
    conn: sqlite3.Connection,
    *,
    canonical_key: str,
    notion_page_id: Optional[str],
    old_status: Optional[str],
    new_status: str,
    observed_at: str,
    source: str,
    metadata: Optional[dict] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO notion_status_events (
            canonical_key, notion_page_id, old_status, new_status, observed_at, source, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (canonical_key, notion_page_id, old_status, new_status, observed_at, source, dumps_json(metadata)),
    )
    conn.commit()
    return int(cur.lastrowid)


async def sync_and_capture_status_events(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    baseline_new_keys: bool = True,
    source: str = "sync_suppression",
) -> StatusEventInsertStats:
    """
    Run pipeline.sync_suppression(), then record diffs as events.

    Args:
        conn: sqlite3 connection (quality tables ensured)
        db_path: path passed to pipeline config
        baseline_new_keys: if True, insert an event when we first see a canonical_key
        source: event source string

    Returns:
        counts inserted
    """
    # Snapshot before
    before = _load_suppression_statuses(conn)

    # Run suppression sync using the pipeline to ensure consistent Notion behavior.
    from workflows.pipeline import DiscoveryPipeline, PipelineConfig

    config = PipelineConfig.from_env()
    config.db_path = db_path

    pipeline = DiscoveryPipeline(config)
    await pipeline.initialize()
    try:
        await pipeline.sync_suppression()
    finally:
        await pipeline.close()

    # Snapshot after
    after = _load_suppression_statuses(conn)

    observed_at = utc_now_iso()
    events_inserted = 0
    new_keys = 0
    changed_keys = 0

    # Insert events for new keys and changed statuses
    for key, (new_status, page_id) in after.items():
        old_tuple = before.get(key)
        old_status = old_tuple[0] if old_tuple else None

        if old_tuple is None:
            new_keys += 1
            if baseline_new_keys:
                insert_status_event(
                    conn,
                    canonical_key=key,
                    notion_page_id=page_id,
                    old_status=None,
                    new_status=new_status,
                    observed_at=observed_at,
                    source=source,
                    metadata={"baseline": True},
                )
                events_inserted += 1
            continue

        if old_status != new_status:
            changed_keys += 1
            insert_status_event(
                conn,
                canonical_key=key,
                notion_page_id=page_id,
                old_status=old_status,
                new_status=new_status,
                observed_at=observed_at,
                source=source,
                metadata={"baseline": False},
            )
            events_inserted += 1

    return StatusEventInsertStats(
        observed_at=observed_at,
        events_inserted=events_inserted,
        new_keys=new_keys,
        changed_keys=changed_keys,
    )
