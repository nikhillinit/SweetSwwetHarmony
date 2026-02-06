"""
Outcome labeling from Notion status events (and/or snapshot status).

Primary use:
- Convert CRM workflow outcomes into TP/FP labels for signals.

Default policy (configurable by caller):
- FP if status becomes "Passed" within N days of the signal being pushed to Notion
- TP if status becomes "Funded" within N days of the signal being pushed to Notion
- Otherwise: leave unlabeled

This is intentionally conservative — avoid labeling 'Source' as TP by default,
since it is usually "in progress" not a success outcome.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from ops.quality.db import loads_json, utc_now_iso
from ops.quality.labels import normalize_label, upsert_resolved_label


@dataclass(frozen=True)
class OutcomeBackfillStats:
    scanned: int
    labeled: int
    fp: int
    tp: int
    skipped_no_events: int


def _parse_iso(ts: str) -> datetime:
    # Support timestamps stored by pipeline (ISO 8601 with timezone)
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iter_pushed_signals(conn: sqlite3.Connection, *, since_days: Optional[int] = None) -> Iterable[sqlite3.Row]:
    where = "WHERE sp.status = 'pushed' AND sp.notion_page_id IS NOT NULL"
    params: List = []
    if since_days is not None:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        where += " AND sp.processed_at >= ?"
        params.append(since)

    query = f"""
        SELECT
            s.id AS signal_id,
            s.canonical_key AS canonical_key,
            s.source_api AS source_api,
            s.detected_at AS detected_at,
            sp.notion_page_id AS notion_page_id,
            sp.processed_at AS pushed_at
        FROM signals s
        JOIN signal_processing sp ON sp.signal_id = s.id
        {where}
        ORDER BY sp.processed_at DESC
    """
    rows = conn.execute(query, params).fetchall()
    for r in rows:
        yield r


def _find_first_status_event_after(
    conn: sqlite3.Connection,
    *,
    canonical_key: str,
    notion_page_id: Optional[str],
    after_iso: str,
    statuses: Tuple[str, ...],
) -> Optional[sqlite3.Row]:
    # Prefer notion_page_id if available, but canonical_key is required anyway.
    # Use both when possible.
    if notion_page_id:
        row = conn.execute(
            """
            SELECT *
            FROM notion_status_events
            WHERE (canonical_key = ? OR notion_page_id = ?)
              AND observed_at >= ?
              AND new_status IN ({})
            ORDER BY observed_at ASC, id ASC
            LIMIT 1
            """.format(",".join(["?"] * len(statuses))),
            (canonical_key, notion_page_id, after_iso, *statuses),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT *
            FROM notion_status_events
            WHERE canonical_key = ?
              AND observed_at >= ?
              AND new_status IN ({})
            ORDER BY observed_at ASC, id ASC
            LIMIT 1
            """.format(",".join(["?"] * len(statuses))),
            (canonical_key, after_iso, *statuses),
        ).fetchone()
    return row


def backfill_outcomes_from_events(
    conn: sqlite3.Connection,
    *,
    days_to_count: int = 30,
    fp_statuses: Tuple[str, ...] = ("Passed",),
    tp_statuses: Tuple[str, ...] = ("Funded",),
    since_days: Optional[int] = None,
    labeled_by: str = "quality-backfill",
    override_manual: bool = False,
) -> OutcomeBackfillStats:
    """
    Iterate pushed signals, look for first outcome status event, and label TP/FP.

    Returns stats summary.
    """
    scanned = 0
    labeled = 0
    fp = 0
    tp = 0
    skipped_no_events = 0

    statuses = tuple(dict.fromkeys(fp_statuses + tp_statuses))  # preserve order, unique
    now_iso = utc_now_iso()

    for row in _iter_pushed_signals(conn, since_days=since_days):
        scanned += 1
        signal_id = int(row["signal_id"])
        canonical_key = str(row["canonical_key"])
        notion_page_id = str(row["notion_page_id"]) if row["notion_page_id"] else None
        pushed_at_iso = str(row["pushed_at"]) if row["pushed_at"] else None
        if not pushed_at_iso:
            skipped_no_events += 1
            continue

        # Find first event after push
        event = _find_first_status_event_after(
            conn,
            canonical_key=canonical_key,
            notion_page_id=notion_page_id,
            after_iso=pushed_at_iso,
            statuses=statuses,
        )
        if not event:
            skipped_no_events += 1
            continue

        event_time = _parse_iso(str(event["observed_at"]))
        pushed_time = _parse_iso(pushed_at_iso)
        delta_days = (event_time - pushed_time).total_seconds() / 86400.0

        if delta_days < 0:
            # Clock skew / out-of-order data; ignore
            skipped_no_events += 1
            continue

        if delta_days > float(days_to_count):
            # Outside window; ignore
            skipped_no_events += 1
            continue

        new_status = str(event["new_status"])

        if new_status in fp_statuses:
            label = "FP"
        elif new_status in tp_statuses:
            label = "TP"
        else:
            skipped_no_events += 1
            continue

        upsert_resolved_label(
            conn,
            signal_id=signal_id,
            canonical_key=canonical_key,
            human_label=normalize_label(label),
            label_source="notion_status_event",
            labeled_by=labeled_by,
            labeled_at=now_iso,
            notion_page_id=notion_page_id,
            notion_status=new_status,
            status_event_id=int(event["id"]),
            days_to_outcome=float(delta_days),
            notes=f"Inferred from Notion status '{new_status}' within {days_to_count}d of push",
            metadata={"event": {"id": int(event["id"]), "old_status": event["old_status"], "new_status": new_status}},
            override_manual=override_manual,
        )

        labeled += 1
        if label == "FP":
            fp += 1
        else:
            tp += 1

    return OutcomeBackfillStats(
        scanned=scanned,
        labeled=labeled,
        fp=fp,
        tp=tp,
        skipped_no_events=skipped_no_events,
    )


def backfill_from_snapshot_status(
    conn: sqlite3.Connection,
    *,
    mapping: Dict[str, str],
    since_days: Optional[int] = None,
    labeled_by: str = "quality-backfill-snapshot",
    override_manual: bool = False,
) -> int:
    """
    Fallback backfill: use suppression_cache current status to label signals.

    mapping: notion_status -> label ('FP'|'TP'|'UNSURE')

    This does NOT apply a 30-day window, because snapshot status has no timestamp.
    Use with caution; primarily for bootstrapping a dataset.
    """
    labeled = 0
    now_iso = utc_now_iso()

    # Join pushed signals to suppression cache by canonical_key and notion_page_id if available.
    where = "WHERE sp.status = 'pushed'"
    params: List = []
    if since_days is not None:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        where += " AND sp.processed_at >= ?"
        params.append(since)

    rows = conn.execute(
        f"""
        SELECT
            s.id AS signal_id,
            s.canonical_key AS canonical_key,
            sp.notion_page_id AS notion_page_id,
            sc.status AS notion_status
        FROM signals s
        JOIN signal_processing sp ON sp.signal_id = s.id
        LEFT JOIN suppression_cache sc ON sc.canonical_key = s.canonical_key
        {where}
        """,
        params,
    ).fetchall()

    for r in rows:
        status = str(r["notion_status"]) if r["notion_status"] else None
        if not status:
            continue
        label = mapping.get(status)
        if not label:
            continue

        upsert_resolved_label(
            conn,
            signal_id=int(r["signal_id"]),
            canonical_key=str(r["canonical_key"]),
            human_label=normalize_label(label),
            label_source="notion_snapshot",
            labeled_by=labeled_by,
            labeled_at=now_iso,
            notion_page_id=str(r["notion_page_id"]) if r["notion_page_id"] else None,
            notion_status=status,
            notes=f"Inferred from Notion snapshot status '{status}'",
            metadata={"mapping": mapping},
            override_manual=override_manual,
        )
        labeled += 1

    return labeled
