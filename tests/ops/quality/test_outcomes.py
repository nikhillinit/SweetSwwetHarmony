"""Tests for ops.quality.outcomes — outcome labeling from Notion status events."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tests.ops.quality.conftest import _insert_signal, _utc_iso
from ops.quality.db import quality_conn
from ops.quality.labels import upsert_resolved_label
from ops.quality.outcomes import (
    backfill_outcomes_from_events,
    backfill_from_snapshot_status,
)


def _insert_processing(conn, signal_id, notion_page_id="page_1", status="pushed", processed_at=None):
    """Insert a signal_processing row for a pushed signal."""
    processed_at = processed_at or _utc_iso(0)
    conn.execute(
        """INSERT INTO signal_processing (signal_id, status, notion_page_id, processed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (signal_id, status, notion_page_id, processed_at, _utc_iso(0), _utc_iso(0)),
    )
    conn.commit()


def _insert_status_event(conn, canonical_key, notion_page_id, old_status, new_status, observed_at, source="sync_suppression", metadata=None):
    """Insert a notion_status_events row."""
    conn.execute(
        """INSERT INTO notion_status_events (canonical_key, notion_page_id, old_status, new_status, observed_at, source, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (canonical_key, notion_page_id, old_status, new_status, observed_at, source, metadata),
    )
    conn.commit()


def _insert_suppression(conn, canonical_key, notion_page_id, status):
    """Insert a suppression_cache row."""
    conn.execute(
        """INSERT OR REPLACE INTO suppression_cache (canonical_key, notion_page_id, status, cached_at, expires_at)
        VALUES (?, ?, ?, ?, ?)""",
        (canonical_key, notion_page_id, status, _utc_iso(0), _utc_iso(-30)),
    )
    conn.commit()


class TestBackfillOutcomesFromEvents:
    def test_no_pushed_signals(self, quality_db):
        """No pushed signals -> scanned=0."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            stats = backfill_outcomes_from_events(conn)
            assert stats.scanned == 0
            assert stats.labeled == 0

    def test_pushed_signal_with_passed_event_labeled_fp(self, quality_db):
        """Pushed signal + 'Passed' event within window -> labeled FP."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:out1.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_out1", processed_at=pushed_at)
            # Event 2 days after push
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(conn, "domain:out1.com", "page_out1", "Source", "Passed", event_at)

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.scanned == 1
            assert stats.labeled == 1
            assert stats.fp == 1

    def test_pushed_signal_with_funded_event_labeled_tp(self, quality_db):
        """Pushed signal + 'Funded' event within window -> labeled TP."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:out2.com")
            pushed_at = _utc_iso(10)
            _insert_processing(conn, sid, notion_page_id="page_out2", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
            _insert_status_event(conn, "domain:out2.com", "page_out2", "Source", "Funded", event_at)

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.labeled == 1
            assert stats.tp == 1

    def test_event_outside_window_skipped(self, quality_db):
        """Event beyond days_to_count window is skipped."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:out3.com")
            pushed_at = _utc_iso(60)
            _insert_processing(conn, sid, notion_page_id="page_out3", processed_at=pushed_at)
            # Event 50 days after push (only 10 days ago)
            event_at = _utc_iso(10)
            _insert_status_event(conn, "domain:out3.com", "page_out3", "Source", "Passed", event_at)

            stats = backfill_outcomes_from_events(conn, days_to_count=5)
            assert stats.labeled == 0
            assert stats.skipped_no_events >= 1

    def test_no_event_for_signal(self, quality_db):
        """Pushed signal with no matching events -> skipped."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:out4.com")
            _insert_processing(conn, sid, notion_page_id="page_out4", processed_at=_utc_iso(5))

            stats = backfill_outcomes_from_events(conn)
            assert stats.scanned == 1
            assert stats.labeled == 0
            assert stats.skipped_no_events == 1

    def test_manual_label_not_overridden(self, quality_db):
        """If manual label exists, backfill does not overwrite (override_manual=False)."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:out5.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_out5", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(conn, "domain:out5.com", "page_out5", "Source", "Passed", event_at)

            # Pre-label as TP manually
            upsert_resolved_label(
                conn, signal_id=sid, canonical_key="domain:out5.com",
                human_label="TP", label_source="manual", labeled_by="human",
            )

            stats = backfill_outcomes_from_events(conn, override_manual=False)
            # The manual label should be preserved
            row = conn.execute(
                "SELECT human_label FROM signal_quality_metrics WHERE signal_id = ?", (sid,)
            ).fetchone()
            assert row["human_label"] == "TP"

    def test_override_manual_true(self, quality_db):
        """With override_manual=True, backfill overwrites manual label."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:out6.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_out6", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(conn, "domain:out6.com", "page_out6", "Source", "Passed", event_at)

            # Pre-label as TP manually
            upsert_resolved_label(
                conn, signal_id=sid, canonical_key="domain:out6.com",
                human_label="TP", label_source="manual", labeled_by="human",
            )

            stats = backfill_outcomes_from_events(conn, override_manual=True)
            assert stats.labeled == 1
            row = conn.execute(
                "SELECT human_label FROM signal_quality_metrics WHERE signal_id = ?", (sid,)
            ).fetchone()
            assert row["human_label"] == "FP"


    def test_baseline_true_event_skipped(self, quality_db):
        """Event with metadata='{"baseline": true}' is skipped by backfill."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:base1.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_base1", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(
                conn, "domain:base1.com", "page_base1", "Source", "Passed", event_at,
                metadata='{"baseline": true}',
            )

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.scanned == 1
            assert stats.labeled == 0
            assert stats.skipped_no_events == 1

    def test_baseline_false_event_labeled(self, quality_db):
        """Event with metadata='{"baseline": false}' is still labeled correctly."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:base2.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_base2", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(
                conn, "domain:base2.com", "page_base2", "Source", "Passed", event_at,
                metadata='{"baseline": false}',
            )

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.labeled == 1
            assert stats.fp == 1

    def test_empty_metadata_event_labeled(self, quality_db):
        """Event with metadata='{}' is still labeled correctly."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:base3.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_base3", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(
                conn, "domain:base3.com", "page_base3", "Source", "Funded", event_at,
                metadata='{}',
            )

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.labeled == 1
            assert stats.tp == 1

    def test_null_metadata_event_labeled(self, quality_db):
        """Event with metadata IS NULL is still labeled correctly."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:base4.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_base4", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(
                conn, "domain:base4.com", "page_base4", "Source", "Passed", event_at,
                metadata=None,
            )

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.labeled == 1
            assert stats.fp == 1

    def test_malformed_metadata_event_labeled(self, quality_db):
        """Event with malformed metadata (e.g. 'not-json') is still labeled correctly."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:base5.com")
            pushed_at = _utc_iso(5)
            _insert_processing(conn, sid, notion_page_id="page_base5", processed_at=pushed_at)
            event_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            _insert_status_event(
                conn, "domain:base5.com", "page_base5", "Source", "Funded", event_at,
                metadata='not-json',
            )

            stats = backfill_outcomes_from_events(conn, days_to_count=30)
            assert stats.labeled == 1
            assert stats.tp == 1


class TestBackfillFromSnapshotStatus:
    def test_no_pushed_signals(self, quality_db):
        """No pushed signals -> 0 labeled."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            labeled = backfill_from_snapshot_status(conn, mapping={"Passed": "FP"})
            assert labeled == 0

    def test_snapshot_labels_pushed_signal(self, quality_db):
        """Pushed signal with matching suppression_cache status -> labeled."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:snap1.com")
            _insert_processing(conn, sid, notion_page_id="page_snap1", status="pushed", processed_at=_utc_iso(5))
            _insert_suppression(conn, "domain:snap1.com", "page_snap1", "Passed")

            labeled = backfill_from_snapshot_status(conn, mapping={"Passed": "FP", "Funded": "TP"})
            assert labeled == 1

            row = conn.execute(
                "SELECT human_label FROM signal_quality_metrics WHERE signal_id = ?", (sid,)
            ).fetchone()
            assert row["human_label"] == "FP"

    def test_snapshot_unmapped_status_skipped(self, quality_db):
        """Suppression status not in mapping -> not labeled."""
        db_path, _ = quality_db
        with quality_conn(db_path) as conn:
            sid = _insert_signal(conn, canonical_key="domain:snap2.com")
            _insert_processing(conn, sid, notion_page_id="page_snap2", status="pushed", processed_at=_utc_iso(5))
            _insert_suppression(conn, "domain:snap2.com", "page_snap2", "Source")

            labeled = backfill_from_snapshot_status(conn, mapping={"Passed": "FP"})
            assert labeled == 0
