"""
Tests for monitoring data-model DTOs.

Covers:
- Watch.is_due
- Snapshot.success
- Snapshot.has_redirect
- Diff.to_dict
- MonitoringAlert.to_dict
- SeverityComponents.to_dict
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from monitoring.models import (
    Watch,
    Snapshot,
    Diff,
    SeverityComponents,
    MonitoringAlert,
)


class TestWatchIsDue:
    """Tests for Watch.is_due()."""

    def test_inactive_watch_never_due(self):
        watch = Watch(active=False, last_checked_at=None)
        assert watch.is_due() is False

    def test_never_checked_is_due(self):
        watch = Watch(active=True, last_checked_at=None)
        assert watch.is_due() is True

    def test_interval_not_elapsed(self):
        now = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        watch = Watch(
            active=True,
            interval_seconds=3600,
            last_checked_at=now - timedelta(seconds=1800),
        )
        assert watch.is_due(now=now) is False

    def test_interval_elapsed(self):
        now = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        watch = Watch(
            active=True,
            interval_seconds=3600,
            last_checked_at=now - timedelta(seconds=3601),
        )
        assert watch.is_due(now=now) is True

    def test_in_backoff_not_due(self):
        now = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        watch = Watch(
            active=True,
            last_checked_at=None,
            backoff_until=now + timedelta(minutes=10),
        )
        assert watch.is_due(now=now) is False

    def test_in_cooldown_not_due(self):
        now = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        watch = Watch(
            active=True,
            last_checked_at=None,
            cooldown_until=now + timedelta(minutes=5),
        )
        assert watch.is_due(now=now) is False


class TestSnapshotSuccess:
    """Tests for Snapshot.success property."""

    def test_success_when_200_no_error(self):
        snap = Snapshot(status_code=200, error=None)
        assert snap.success is True

    def test_not_success_when_500(self):
        snap = Snapshot(status_code=500, error=None)
        assert snap.success is False

    def test_not_success_when_200_with_error(self):
        snap = Snapshot(status_code=200, error="parse failure")
        assert snap.success is False


class TestSnapshotHasRedirect:
    """Tests for Snapshot.has_redirect property."""

    def test_no_redirect_same_host(self):
        snap = Snapshot(
            requested_url="https://example.com/page",
            final_url="https://example.com/other",
        )
        assert snap.has_redirect is False

    def test_redirect_different_host(self):
        snap = Snapshot(
            requested_url="https://old.example.com/page",
            final_url="https://new.example.com/page",
        )
        assert snap.has_redirect is True

    def test_no_redirect_when_final_url_none(self):
        snap = Snapshot(requested_url="https://example.com", final_url=None)
        assert snap.has_redirect is False


class TestDiffToDict:
    """Tests for Diff.to_dict()."""

    def test_to_dict_has_all_keys(self):
        created = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        components = SeverityComponents(
            content_delta=0.3, semantic_drift=0.1, state_change=0.0, redirect=0.0
        )
        diff = Diff(
            id=1,
            watch_id=10,
            old_snapshot_id=100,
            new_snapshot_id=101,
            created_at=created,
            severity_score=0.42,
            severity_components=components,
            semantic_drift=0.1,
            has_redirect=False,
            has_state_change=False,
            has_text_change=True,
            diff_summary={"length_change": 500},
        )
        d = diff.to_dict()

        assert d["id"] == 1
        assert d["watch_id"] == 10
        assert d["old_snapshot_id"] == 100
        assert d["new_snapshot_id"] == 101
        assert d["created_at"] == created.isoformat()
        assert d["severity_score"] == 0.42
        assert d["severity_components"] == components.to_dict()
        assert d["semantic_drift"] == 0.1
        assert d["has_redirect"] is False
        assert d["has_state_change"] is False
        assert d["has_text_change"] is True
        assert d["diff_summary"] == {"length_change": 500}


class TestMonitoringAlertToDict:
    """Tests for MonitoringAlert.to_dict()."""

    def test_to_dict_includes_payload(self):
        created = datetime(2026, 3, 19, 10, 0, 0, tzinfo=timezone.utc)
        acked_at = datetime(2026, 3, 19, 11, 0, 0, tzinfo=timezone.utc)
        alert = MonitoringAlert(
            id=5,
            watch_id=10,
            diff_id=20,
            alert_reason="high_severity",
            severity_score=0.85,
            acknowledged=True,
            acknowledged_by="operator",
            acknowledged_at=acked_at,
            created_at=created,
            payload={"context": "test"},
        )
        d = alert.to_dict()

        assert d["id"] == 5
        assert d["alert_reason"] == "high_severity"
        assert d["acknowledged"] is True
        assert d["acknowledged_by"] == "operator"
        assert d["acknowledged_at"] == acked_at.isoformat()
        assert d["created_at"] == created.isoformat()
        assert d["payload"] == {"context": "test"}


class TestSeverityComponentsToDict:
    """Tests for SeverityComponents.to_dict()."""

    def test_to_dict_fields(self):
        sc = SeverityComponents(
            content_delta=0.5, semantic_drift=None, state_change=1.0, redirect=0.0
        )
        d = sc.to_dict()
        assert d == {
            "content_delta": 0.5,
            "semantic_drift": None,
            "state_change": 1.0,
            "redirect": 0.0,
        }
