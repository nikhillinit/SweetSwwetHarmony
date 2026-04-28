"""Day 2 — GP workload logger.

Tracks two distinct quantities, per the Phase 2 plan's prerequisite #9:

* ``raw_event_review_minutes_per_week`` — the burden of scanning a review-set
  before any labeling happens (review-set size × seconds-per-item).
* ``useful_label_minutes_per_week`` — actual labeling capacity, derived from
  successfully applied labels (count × seconds-per-label).

These must remain separate. A GP team may scan 200 events and apply 12 labels;
the goal is enough high-value labels, not maximal time-on-task. Conflating the
two metrics has caused incorrect headcount and pacing decisions in the past
(see Day 0 tracker item 9 background).

Events are appended atomically to a JSONL file (default ``state/gp_workload.jsonl``)
via two narrow hook functions called from ``ops.quality_cli``:

* ``log_review_set_generated`` — called after ``learning-loop review-set`` writes
  its canonical JSON output.
* ``log_labels_applied`` — called after ``learning-loop apply-labels`` finishes.

The summary view (``summarize_workload`` + ``render_summary_table``) is
exposed via ``scripts/gp_workload_report.py``. No DB access — this module is
purely a write-then-aggregate JSONL logger.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

DEFAULT_LOG_PATH = Path("state") / "gp_workload.jsonl"

EVENT_REVIEW_SET_GENERATED = "review_set_generated"
EVENT_LABELS_APPLIED = "labels_applied"

# Calibration estimates. These are deliberately generous defaults — operators
# can override per-call. The point is to surface the relative ratio of raw
# review burden vs useful labeling, not a Hawthorne-effect time-and-motion
# study.
DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM = 30.0
DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM = 45.0

_APPEND_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: Optional[datetime]) -> str:
    dt = value or _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _atomic_append(path: Path, line: str) -> None:
    """Append a single line under a process-wide lock.

    Uses a lock plus an open(.., 'a') call. Single-line writes under a few KB
    are atomic at the OS level on POSIX and on Windows for files opened in
    append mode, so a real ``rename``-based atomic-write is overkill for an
    audit log. The lock prevents interleaving when called from concurrent
    threads in the same process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")


def _record(
    event: str,
    fields: Mapping[str, Any],
    *,
    timestamp: Optional[datetime] = None,
    log_path: Optional[str | os.PathLike[str]] = None,
) -> dict[str, Any]:
    payload = {
        "event": event,
        "timestamp": _isoformat(timestamp),
        **dict(fields),
    }
    target = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
    _atomic_append(target, json.dumps(payload, sort_keys=True))
    return payload


def log_review_set_generated(
    *,
    items_count: int,
    window_days: int,
    runner: str,
    log_path: Optional[str | os.PathLike[str]] = None,
    timestamp: Optional[datetime] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Record that a review-set was generated.

    ``items_count`` drives the ``raw_event_review_minutes_per_week`` metric.
    """
    fields: dict[str, Any] = {
        "items_count": int(items_count),
        "window_days": int(window_days),
        "runner": str(runner),
    }
    if extra:
        fields["extra"] = dict(extra)
    return _record(
        EVENT_REVIEW_SET_GENERATED,
        fields,
        timestamp=timestamp,
        log_path=log_path,
    )


def log_labels_applied(
    *,
    attempted: int,
    succeeded: int,
    failed: int,
    runner: str,
    log_path: Optional[str | os.PathLike[str]] = None,
    timestamp: Optional[datetime] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Record that a batch of labels was applied.

    ``succeeded`` drives the ``useful_label_minutes_per_week`` metric.
    """
    fields: dict[str, Any] = {
        "attempted": int(attempted),
        "succeeded": int(succeeded),
        "failed": int(failed),
        "runner": str(runner),
    }
    if extra:
        fields["extra"] = dict(extra)
    return _record(
        EVENT_LABELS_APPLIED,
        fields,
        timestamp=timestamp,
        log_path=log_path,
    )


def read_events(
    log_path: Optional[str | os.PathLike[str]] = None,
) -> list[dict[str, Any]]:
    """Read all events from the JSONL log; tolerate missing or malformed lines."""
    target = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def summarize_workload(
    log_path: Optional[str | os.PathLike[str]] = None,
    *,
    window_days: int = 7,
    now: Optional[datetime] = None,
    raw_review_seconds_per_item: float = DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM,
    useful_label_seconds_per_item: float = DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM,
) -> dict[str, Any]:
    """Aggregate events within the last ``window_days``.

    Returns a dict with the two headline metrics plus per-runner breakdowns.
    Minute estimates are deliberately derived from configurable
    seconds-per-item constants so operators can tune them as the team's
    actual cadence becomes known.
    """
    checked_at = (now or _utc_now()).astimezone(timezone.utc)
    cutoff = checked_at - timedelta(days=window_days)
    events = read_events(log_path)

    review_sets = 0
    raw_events_reviewed = 0
    labels_applied = 0
    labels_attempted = 0
    labels_failed = 0
    by_runner: dict[str, dict[str, int]] = {}

    for evt in events:
        ts = _parse_iso(evt.get("timestamp"))
        if ts is None or ts < cutoff or ts > checked_at:
            continue
        event_type = evt.get("event")
        runner = str(evt.get("runner") or "unknown")
        runner_bucket = by_runner.setdefault(
            runner,
            {
                "review_sets_generated": 0,
                "raw_events_reviewed": 0,
                "labels_attempted": 0,
                "labels_applied": 0,
                "labels_failed": 0,
            },
        )

        if event_type == EVENT_REVIEW_SET_GENERATED:
            items = int(evt.get("items_count") or 0)
            review_sets += 1
            raw_events_reviewed += items
            runner_bucket["review_sets_generated"] += 1
            runner_bucket["raw_events_reviewed"] += items
        elif event_type == EVENT_LABELS_APPLIED:
            attempted = int(evt.get("attempted") or 0)
            succeeded = int(evt.get("succeeded") or 0)
            failed = int(evt.get("failed") or 0)
            labels_attempted += attempted
            labels_applied += succeeded
            labels_failed += failed
            runner_bucket["labels_attempted"] += attempted
            runner_bucket["labels_applied"] += succeeded
            runner_bucket["labels_failed"] += failed

    raw_minutes = (raw_events_reviewed * raw_review_seconds_per_item) / 60.0
    useful_minutes = (labels_applied * useful_label_seconds_per_item) / 60.0

    return {
        "schema_version": 1,
        "window_days": window_days,
        "generated_at": _isoformat(checked_at),
        "review_sets_generated": review_sets,
        "raw_events_reviewed": raw_events_reviewed,
        "labels_attempted": labels_attempted,
        "labels_applied": labels_applied,
        "labels_failed": labels_failed,
        "raw_event_review_minutes_per_week": raw_minutes,
        "useful_label_minutes_per_week": useful_minutes,
        "raw_review_seconds_per_item": raw_review_seconds_per_item,
        "useful_label_seconds_per_item": useful_label_seconds_per_item,
        "by_runner": by_runner,
    }


def render_summary_table(summary: Mapping[str, Any]) -> str:
    """Render a human-readable summary."""
    lines: list[str] = []
    lines.append(
        f"GP workload (last {summary.get('window_days', '?')} days, generated {summary.get('generated_at', '')})"
    )
    lines.append(
        f"  review_sets_generated: {summary.get('review_sets_generated', 0)}"
    )
    lines.append(
        f"  raw_events_reviewed: {summary.get('raw_events_reviewed', 0)}"
    )
    lines.append(
        f"  labels_applied: {summary.get('labels_applied', 0)} (attempted {summary.get('labels_attempted', 0)}, failed {summary.get('labels_failed', 0)})"
    )
    lines.append("")
    lines.append("Headline metrics:")
    lines.append(
        f"  raw_event_review_minutes_per_week: {summary.get('raw_event_review_minutes_per_week', 0):.1f}"
    )
    lines.append(
        f"  useful_label_minutes_per_week: {summary.get('useful_label_minutes_per_week', 0):.1f}"
    )
    lines.append("")
    lines.append("By runner:")
    by_runner = summary.get("by_runner") or {}
    if not by_runner:
        lines.append("  (none in window)")
    else:
        for runner, b in sorted(by_runner.items()):
            lines.append(
                f"  {runner}: review_sets={b.get('review_sets_generated', 0)}, "
                f"raw_reviewed={b.get('raw_events_reviewed', 0)}, "
                f"labels_applied={b.get('labels_applied', 0)} "
                f"(failed={b.get('labels_failed', 0)})"
            )
    return "\n".join(lines)
