"""
Manual labeling and label storage helpers.

We store:
- quality_feedback: append-only audit trail (each label event)
- signal_quality_metrics: latest resolved label per signal (UPSERT)

Resolved label policy (simple):
- Manual labels overwrite any existing resolved label for that signal_id.
- Inferred labels (from Notion) overwrite only if there is no manual label yet,
  unless override=True is explicitly set.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from ops.quality.db import dumps_json, utc_now_iso


Label = str  # 'TP'|'FP'|'UNSURE'|'ADJ'


def normalize_label(label: str) -> Label:
    l = (label or "").strip().upper()
    if l in {"TP", "FP", "UNSURE", "ADJ"}:
        return l
    raise ValueError("label must be one of: TP, FP, UNSURE, ADJ")


@dataclass(frozen=True)
class UpsertResult:
    signal_id: int
    canonical_key: str
    human_label: Label
    label_source: str
    labeled_at: str
    overwritten: bool


@dataclass(frozen=True)
class AdjReviewCandidate:
    signal_id: int
    queue_type: str
    canonical_key: str
    company_name: str
    source_api: str
    detected_at: str
    priority_rank: int
    reason_code: str
    reason_summary: str
    labeled_at: str
    labeled_by: Optional[str]
    confidence: float


def _iso_days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


def get_signal_canonical_key(conn: sqlite3.Connection, signal_id: int) -> str:
    row = conn.execute("SELECT canonical_key FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if not row:
        raise ValueError(f"Signal id={signal_id} not found in signals table")
    return str(row["canonical_key"])


def has_manual_label(conn: sqlite3.Connection, signal_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM signal_quality_metrics WHERE signal_id = ? AND label_source = 'manual' LIMIT 1",
        (signal_id,),
    ).fetchone()
    return bool(row)


def list_adj_review_candidates(
    conn: sqlite3.Connection,
    *,
    days: int = 90,
    limit: int = 50,
) -> list[AdjReviewCandidate]:
    """Return structured ADJ follow-up rows for operator review."""
    since = _iso_days_ago(days)
    rows = conn.execute(
        """
        SELECT
            sqm.signal_id,
            s.company_name,
            s.source_api,
            s.confidence,
            s.canonical_key,
            s.detected_at,
            sqm.labeled_at,
            sqm.labeled_by,
            sqm.notes,
            json_extract(sqm.metadata, '$.reason') AS reason
        FROM signal_quality_metrics sqm
        JOIN signals s ON s.id = sqm.signal_id
        WHERE sqm.human_label = 'ADJ'
          AND sqm.labeled_at >= ?
        ORDER BY sqm.labeled_at DESC, sqm.signal_id DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()

    candidates: list[AdjReviewCandidate] = []
    for r in rows:
        candidates.append(
            AdjReviewCandidate(
                signal_id=int(r["signal_id"]),
                queue_type="adj",
                canonical_key=str(r["canonical_key"] or ""),
                company_name=str(r["company_name"] or ""),
                source_api=str(r["source_api"] or ""),
                detected_at=str(r["detected_at"] or ""),
                priority_rank=50,
                reason_code="adj_followup",
                reason_summary=str(r["reason"] or r["notes"] or "adj_followup"),
                labeled_at=str(r["labeled_at"] or ""),
                labeled_by=str(r["labeled_by"]) if r["labeled_by"] is not None else None,
                confidence=float(r["confidence"] or 0.0),
            )
        )
    return candidates


def upsert_resolved_label(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    canonical_key: str,
    human_label: Label,
    label_source: str,
    labeled_by: Optional[str] = None,
    labeled_at: Optional[str] = None,
    notion_page_id: Optional[str] = None,
    notion_status: Optional[str] = None,
    status_event_id: Optional[int] = None,
    days_to_outcome: Optional[float] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    override_manual: bool = False,
) -> UpsertResult:
    """
    Upsert into signal_quality_metrics.

    If label_source != 'manual' and an existing manual label exists, we keep manual
    unless override_manual=True.
    """
    labeled_at = labeled_at or utc_now_iso()

    existing = conn.execute(
        "SELECT human_label, label_source, labeled_at FROM signal_quality_metrics WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()

    # Respect manual labels unless override_manual requested
    if label_source != "manual" and not override_manual and has_manual_label(conn, signal_id):
        return UpsertResult(
            signal_id=signal_id,
            canonical_key=canonical_key,
            human_label=str(existing["human_label"]),
            label_source=str(existing["label_source"]),
            labeled_at=str(existing["labeled_at"]),
            overwritten=False,
        )

    conn.execute(
        """
        INSERT INTO signal_quality_metrics (
            signal_id, canonical_key, human_label, label_source, labeled_by, labeled_at,
            notion_page_id, notion_status, status_event_id, days_to_outcome, notes, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id) DO UPDATE SET
            canonical_key=excluded.canonical_key,
            human_label=excluded.human_label,
            label_source=excluded.label_source,
            labeled_by=excluded.labeled_by,
            labeled_at=excluded.labeled_at,
            notion_page_id=excluded.notion_page_id,
            notion_status=excluded.notion_status,
            status_event_id=excluded.status_event_id,
            days_to_outcome=excluded.days_to_outcome,
            notes=excluded.notes,
            metadata=excluded.metadata
        """,
        (
            signal_id,
            canonical_key,
            human_label,
            label_source,
            labeled_by,
            labeled_at,
            notion_page_id,
            notion_status,
            status_event_id,
            days_to_outcome,
            notes,
            dumps_json(metadata),
        ),
    )
    conn.commit()

    overwritten = existing is not None
    return UpsertResult(
        signal_id=signal_id,
        canonical_key=canonical_key,
        human_label=human_label,
        label_source=label_source,
        labeled_at=labeled_at,
        overwritten=overwritten,
    )


def insert_feedback(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    label: Label,
    created_by: Optional[str] = None,
    reason: Optional[str] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> int:
    created_at = created_at or utc_now_iso()

    cur = conn.execute(
        """
        INSERT INTO quality_feedback (signal_id, label, reason, notes, created_by, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (signal_id, label, reason, notes, created_by, created_at, dumps_json(metadata)),
    )
    conn.commit()
    return int(cur.lastrowid)


def label_signal_manual(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    label: str,
    created_by: Optional[str] = None,
    reason: Optional[str] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[int, UpsertResult]:
    """
    Convenience helper: append to quality_feedback and upsert resolved label.
    """
    normalized = normalize_label(label)
    canonical_key = get_signal_canonical_key(conn, signal_id)

    try:
        feedback_id = insert_feedback(
            conn,
            signal_id=signal_id,
            label=normalized,
            created_by=created_by,
            reason=reason,
            notes=notes,
            metadata=metadata,
        )
    except sqlite3.IntegrityError as exc:
        if normalized == "ADJ" and "CHECK" in str(exc).upper():
            raise RuntimeError(
                "DB schema needs migration to v49 for ADJ label support. Run:\n"
                '  python -c "import asyncio; from storage.signal_store import SignalStore; '
                "asyncio.run(SignalStore('signals.db').initialize())\""
            ) from exc
        raise

    upsert = upsert_resolved_label(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        human_label=normalized,
        label_source="manual",
        labeled_by=created_by,
        notes=notes,
        metadata={"reason": reason, **(metadata or {})} if reason else metadata,
        override_manual=True,  # manual always wins
    )

    return feedback_id, upsert
