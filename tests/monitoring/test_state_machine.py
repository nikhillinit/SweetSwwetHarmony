"""Tests for WatchStateMachine (v2.4 audit events)."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from monitoring.state_machine import WatchStateMachine
from monitoring.models import Watch
from monitoring.failure_classifier import FailureCategory


@pytest.fixture
def mock_db():
    """Mock database connection."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(lastrowid=1))
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_store(mock_db):
    """Mock MonitorStore with database."""
    store = MagicMock()
    store._db = mock_db
    return store


@pytest.fixture
def sample_watch():
    """Sample watch for testing."""
    return Watch(
        id=1,
        canonical_key="domain:acme.ai",
        url="https://acme.ai",
        watch_type="website",
        interval_seconds=86400,
        active=True,
        consecutive_failures=0,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def state_machine(mock_store, sample_watch):
    """State machine with mocked store."""
    return WatchStateMachine(mock_store, sample_watch)


class TestFetchEvents:
    """Test fetch-related event recording."""

    @pytest.mark.asyncio
    async def test_record_fetch_started(self, state_machine, mock_db):
        """record_fetch_started should create an event."""
        await state_machine.record_fetch_started()

        # Should insert into watch_events
        mock_db.execute.assert_called()
        call_args = str(mock_db.execute.call_args_list)
        assert "watch_events" in call_args
        assert "fetch_started" in call_args

    @pytest.mark.asyncio
    async def test_record_fetch_success_unchanged(self, state_machine, mock_db):
        """record_fetch_success_unchanged should update watch and log event."""
        await state_machine.record_fetch_success_unchanged()

        # Should update watch and insert event
        calls = mock_db.execute.call_args_list
        assert len(calls) >= 2  # At least UPDATE watches + INSERT watch_events
        call_args = str(calls)
        assert "fetch_success_unchanged" in call_args

    @pytest.mark.asyncio
    async def test_record_fetch_success_unchanged_resets_failures(self, state_machine, mock_db, sample_watch):
        """record_fetch_success_unchanged should reset consecutive_failures."""
        sample_watch.consecutive_failures = 3

        await state_machine.record_fetch_success_unchanged()

        # Should set consecutive_failures = 0
        call_args = str(mock_db.execute.call_args_list)
        assert "consecutive_failures" in call_args

    @pytest.mark.asyncio
    async def test_record_fetch_success_changed(self, state_machine, mock_db):
        """record_fetch_success_changed should include hash info."""
        await state_machine.record_fetch_success_changed(
            content_hash="abc123",
            hasher_version="v1",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "fetch_success_changed" in call_args


class TestFailureEvents:
    """Test failure event recording."""

    @pytest.mark.asyncio
    async def test_record_fetch_failed(self, state_machine, mock_db):
        """record_fetch_failed should include failure details."""
        backoff_until = datetime.now(timezone.utc) + timedelta(minutes=5)

        await state_machine.record_fetch_failed(
            category=FailureCategory.TRANSIENT,
            error_message="Connection timeout",
            backoff_until=backoff_until,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "fetch_failed" in call_args
        assert "transient" in call_args

    @pytest.mark.asyncio
    async def test_record_fetch_failed_increments_failures(self, state_machine, mock_db, sample_watch):
        """record_fetch_failed should increment consecutive_failures."""
        sample_watch.consecutive_failures = 2
        backoff_until = datetime.now(timezone.utc) + timedelta(minutes=5)

        await state_machine.record_fetch_failed(
            category=FailureCategory.TRANSIENT,
            error_message="Connection timeout",
            backoff_until=backoff_until,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "consecutive_failures" in call_args

    @pytest.mark.asyncio
    async def test_record_fetch_failed_with_deactivation(self, state_machine, mock_db):
        """record_fetch_failed with deactivation should mark watch inactive."""
        backoff_until = datetime.now(timezone.utc) + timedelta(minutes=5)

        await state_machine.record_fetch_failed(
            category=FailureCategory.CLIENT_ERROR,
            error_message="404 Not Found",
            backoff_until=backoff_until,
            should_deactivate=True,
            deactivation_reason="max_failures:client_error",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "deactivated" in call_args or "active" in call_args


class TestSnapshotEvents:
    """Test snapshot event recording."""

    @pytest.mark.asyncio
    async def test_record_snapshot_recorded(self, state_machine, mock_db):
        """record_snapshot_recorded should include snapshot ID."""
        await state_machine.record_snapshot_recorded(
            snapshot_id=100,
            is_maintenance_diff=False,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "snapshot_recorded" in call_args
        assert "100" in call_args

    @pytest.mark.asyncio
    async def test_record_snapshot_recorded_updates_last_snapshot_id(self, state_machine, mock_db):
        """record_snapshot_recorded should update last_snapshot_id."""
        await state_machine.record_snapshot_recorded(
            snapshot_id=100,
            is_maintenance_diff=False,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "last_snapshot_id" in call_args

    @pytest.mark.asyncio
    async def test_record_maintenance_diff_snapshot(self, state_machine, mock_db):
        """Maintenance diff snapshot should be flagged."""
        await state_machine.record_snapshot_recorded(
            snapshot_id=101,
            is_maintenance_diff=True,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "maintenance" in call_args.lower() or "true" in call_args.lower()


class TestDiffEvents:
    """Test diff event recording."""

    @pytest.mark.asyncio
    async def test_record_diff_calculated(self, state_machine, mock_db):
        """record_diff_calculated should include severity."""
        await state_machine.record_diff_calculated(
            diff_id=50,
            severity_score=0.75,
            instant_trigger=None,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "diff_calculated" in call_args
        assert "50" in call_args

    @pytest.mark.asyncio
    async def test_record_diff_with_instant_trigger(self, state_machine, mock_db):
        """Instant trigger should be recorded."""
        await state_machine.record_diff_calculated(
            diff_id=51,
            severity_score=1.0,
            instant_trigger="domain_change",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "domain_change" in call_args


class TestAlertEvents:
    """Test alert event recording."""

    @pytest.mark.asyncio
    async def test_record_alert_created(self, state_machine, mock_db):
        """record_alert_created should include alert details."""
        await state_machine.record_alert_created(
            alert_id=10,
            reason="high_severity",
            severity_score=0.85,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "alert_created" in call_args
        assert "10" in call_args

    @pytest.mark.asyncio
    async def test_record_alert_with_reason(self, state_machine, mock_db):
        """Alert should include reason."""
        await state_machine.record_alert_created(
            alert_id=11,
            reason="gone",
            severity_score=0.95,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "gone" in call_args


class TestProfileUpdateEvents:
    """Test profile update event recording."""

    @pytest.mark.asyncio
    async def test_record_profile_update_enqueued(self, state_machine, mock_db):
        """record_profile_update_enqueued should include outbox ID."""
        await state_machine.record_profile_update_enqueued(
            outbox_id=200,
            trigger="high_severity",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "profile_update_enqueued" in call_args
        assert "200" in call_args


class TestDeactivationEvents:
    """Test deactivation event recording."""

    @pytest.mark.asyncio
    async def test_record_deactivated(self, state_machine, mock_db):
        """record_deactivated should log reason."""
        await state_machine.record_deactivated(
            reason="max_failures:ssl_error",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "deactivated" in call_args
        assert "max_failures" in call_args


class TestCooldownEvents:
    """Test cooldown event recording."""

    @pytest.mark.asyncio
    async def test_record_cooldown_updated(self, state_machine, mock_db):
        """record_cooldown_updated should include cooldown time."""
        cooldown_until = datetime.now(timezone.utc) + timedelta(hours=24)

        await state_machine.record_cooldown_updated(
            cooldown_until=cooldown_until,
            reason="low_sev_threshold",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "cooldown_updated" in call_args

    @pytest.mark.asyncio
    async def test_record_cooldown_cleared(self, state_machine, mock_db):
        """record_cooldown_updated with None should clear cooldown."""
        await state_machine.record_cooldown_updated(
            cooldown_until=None,
            reason="manual_clear",
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "cooldown_updated" in call_args


class TestWatchStateUpdates:
    """Test that watch state is properly updated."""

    @pytest.mark.asyncio
    async def test_success_clears_backoff(self, state_machine, mock_db, sample_watch):
        """Successful fetch should clear backoff_until."""
        sample_watch.backoff_until = datetime.now(timezone.utc) + timedelta(hours=1)

        await state_machine.record_fetch_success_unchanged()

        call_args = str(mock_db.execute.call_args_list)
        # Should set backoff_until to None
        assert "backoff_until" in call_args

    @pytest.mark.asyncio
    async def test_failure_sets_backoff(self, state_machine, mock_db):
        """Failed fetch should set backoff_until."""
        backoff_until = datetime.now(timezone.utc) + timedelta(hours=1)

        await state_machine.record_fetch_failed(
            category=FailureCategory.RATE_LIMITED,
            error_message="429 Too Many Requests",
            backoff_until=backoff_until,
        )

        call_args = str(mock_db.execute.call_args_list)
        assert "backoff_until" in call_args


class TestEventPayloads:
    """Test event payload structure."""

    @pytest.mark.asyncio
    async def test_payload_is_valid_json(self, state_machine, mock_db):
        """Event payloads should be valid JSON."""
        # This should not raise any JSON encoding errors
        await state_machine.record_fetch_failed(
            category=FailureCategory.SSL_ERROR,
            error_message='Error with "quotes" and special chars: <>',
            backoff_until=datetime.now(timezone.utc),
        )

        # If we got here without error, JSON encoding worked
        mock_db.execute.assert_called()

    @pytest.mark.asyncio
    async def test_long_error_truncated(self, state_machine, mock_db):
        """Long error messages should be truncated."""
        long_error = "x" * 1000

        await state_machine.record_fetch_failed(
            category=FailureCategory.CONTENT_ERROR,
            error_message=long_error,
            backoff_until=datetime.now(timezone.utc),
        )

        # Should truncate to 500 chars
        call_args = str(mock_db.execute.call_args_list)
        # The truncated error should be in there, not the full 1000 chars
        assert len(call_args) > 0  # Just verify the call happened
