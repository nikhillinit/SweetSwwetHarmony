from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.gp_workload import (
    DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM,
    DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM,
    EVENT_LABELS_APPLIED,
    EVENT_REVIEW_SET_GENERATED,
    log_labels_applied,
    log_review_set_generated,
    read_events,
    render_summary_table,
    summarize_workload,
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_log_review_set_generated_appends_event(tmp_path):
    log_path = tmp_path / "state" / "gp_workload.jsonl"
    log_review_set_generated(
        items_count=120,
        window_days=30,
        runner="alice",
        log_path=log_path,
    )
    events = _read_jsonl(log_path)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == EVENT_REVIEW_SET_GENERATED
    assert e["items_count"] == 120
    assert e["window_days"] == 30
    assert e["runner"] == "alice"
    assert "timestamp" in e


def test_log_labels_applied_appends_event(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    log_labels_applied(
        attempted=15,
        succeeded=14,
        failed=1,
        runner="alice",
        log_path=log_path,
    )
    events = _read_jsonl(log_path)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == EVENT_LABELS_APPLIED
    assert e["attempted"] == 15
    assert e["succeeded"] == 14
    assert e["failed"] == 1
    assert e["runner"] == "alice"


def test_logger_appends_without_overwriting(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    log_review_set_generated(items_count=10, window_days=7, runner="a", log_path=log_path)
    log_labels_applied(attempted=5, succeeded=5, failed=0, runner="a", log_path=log_path)
    log_review_set_generated(items_count=20, window_days=7, runner="b", log_path=log_path)
    events = _read_jsonl(log_path)
    assert [e["event"] for e in events] == [
        EVENT_REVIEW_SET_GENERATED,
        EVENT_LABELS_APPLIED,
        EVENT_REVIEW_SET_GENERATED,
    ]


def test_read_events_returns_empty_for_missing_log(tmp_path):
    assert read_events(tmp_path / "absent.jsonl") == []


def test_read_events_skips_malformed_lines(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    log_path.write_text(
        '{"event":"review_set_generated","items_count":3,"timestamp":"2026-04-27T00:00:00+00:00"}\n'
        "this is not json\n"
        '{"event":"labels_applied","succeeded":2,"timestamp":"2026-04-27T00:01:00+00:00"}\n',
        encoding="utf-8",
    )
    events = read_events(log_path)
    assert len(events) == 2
    assert events[0]["event"] == EVENT_REVIEW_SET_GENERATED
    assert events[1]["event"] == EVENT_LABELS_APPLIED


def test_summarize_separates_raw_review_from_useful_label_minutes(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    # 200 raw reviewed + 30 actual labels applied
    log_review_set_generated(
        items_count=200,
        window_days=30,
        runner="alice",
        log_path=log_path,
        timestamp=now,
    )
    log_labels_applied(
        attempted=30,
        succeeded=30,
        failed=0,
        runner="alice",
        log_path=log_path,
        timestamp=now,
    )

    summary = summarize_workload(log_path, window_days=7, now=now)

    raw_minutes = (200 * DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM) / 60.0
    useful_minutes = (30 * DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM) / 60.0

    assert summary["window_days"] == 7
    assert summary["review_sets_generated"] == 1
    assert summary["raw_events_reviewed"] == 200
    assert summary["labels_applied"] == 30
    assert summary["raw_event_review_minutes_per_week"] == raw_minutes
    assert summary["useful_label_minutes_per_week"] == useful_minutes
    # The two metrics must be distinct — that is the entire point of the plan's
    # GP workload guidance.
    assert summary["raw_event_review_minutes_per_week"] != summary["useful_label_minutes_per_week"]


def test_summarize_respects_window(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=60)

    log_review_set_generated(items_count=100, window_days=30, runner="a", log_path=log_path, timestamp=old)
    log_review_set_generated(items_count=50, window_days=30, runner="a", log_path=log_path, timestamp=now)

    summary = summarize_workload(log_path, window_days=7, now=now)
    assert summary["review_sets_generated"] == 1
    assert summary["raw_events_reviewed"] == 50


def test_summarize_breaks_down_by_runner(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

    log_labels_applied(attempted=10, succeeded=10, failed=0, runner="alice", log_path=log_path, timestamp=now)
    log_labels_applied(attempted=5, succeeded=4, failed=1, runner="bob", log_path=log_path, timestamp=now)

    summary = summarize_workload(log_path, window_days=7, now=now)
    by_runner = summary["by_runner"]
    assert by_runner["alice"]["labels_applied"] == 10
    assert by_runner["bob"]["labels_applied"] == 4
    assert by_runner["bob"]["labels_failed"] == 1


def test_render_summary_table_emits_both_metrics(tmp_path):
    log_path = tmp_path / "gp_workload.jsonl"
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    log_review_set_generated(items_count=100, window_days=30, runner="a", log_path=log_path, timestamp=now)
    log_labels_applied(attempted=20, succeeded=20, failed=0, runner="a", log_path=log_path, timestamp=now)

    summary = summarize_workload(log_path, window_days=7, now=now)
    rendered = render_summary_table(summary)
    assert "raw_event_review_minutes_per_week" in rendered
    assert "useful_label_minutes_per_week" in rendered
    assert "alice" in rendered or "a" in rendered  # runner appears
