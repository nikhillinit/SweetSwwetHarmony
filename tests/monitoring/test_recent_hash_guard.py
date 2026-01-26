"""Tests for recent-hash guard (v2.4 Section 10.2)."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from monitoring.models import Watch, Snapshot


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
        last_snapshot_id=100,
    )


@pytest.fixture
def sample_snapshot():
    """Sample snapshot."""
    return Snapshot(
        id=100,
        watch_id=1,
        fetched_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        status_code=200,
        requested_url="https://acme.ai",
        final_url="https://acme.ai",
        final_host="acme.ai",
        page_state="live",
        content_hash="abc123def456",
        hasher_version="v1",
        text_length=5000,
    )


class TestRecentHashGuardStep1:
    """Test Step 1: Check against last_snapshot_id."""

    @pytest.mark.asyncio
    async def test_unchanged_content_via_last_snapshot(self, sample_watch, sample_snapshot):
        """If last_snapshot has same hash, return unchanged."""
        from monitoring.monitor_store import MonitorStore

        # Create mock that returns the sample snapshot
        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=sample_snapshot)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="abc123def456",
            hasher_version="v1",
        )

        assert is_unchanged is True
        assert existing.id == sample_snapshot.id

    @pytest.mark.asyncio
    async def test_changed_content_different_hash(self, sample_watch, sample_snapshot):
        """If last_snapshot has different hash, return changed."""
        from monitoring.monitor_store import MonitorStore

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=sample_snapshot)
        store.find_recent_snapshot_by_hash = AsyncMock(return_value=None)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="different_hash",
            hasher_version="v1",
        )

        assert is_unchanged is False
        assert existing is None

    @pytest.mark.asyncio
    async def test_changed_content_different_hasher_version(self, sample_watch, sample_snapshot):
        """Different hasher_version should be treated as changed."""
        from monitoring.monitor_store import MonitorStore

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=sample_snapshot)
        store.find_recent_snapshot_by_hash = AsyncMock(return_value=None)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="abc123def456",  # Same hash
            hasher_version="v2",  # Different version
        )

        assert is_unchanged is False
        assert existing is None


class TestRecentHashGuardStep2:
    """Test Step 2: Time-window fallback (crash recovery)."""

    @pytest.mark.asyncio
    async def test_fallback_to_recent_hash_query(self, sample_watch):
        """If last_snapshot lookup fails, fall back to time-window query."""
        from monitoring.monitor_store import MonitorStore

        # No last_snapshot_id
        sample_watch.last_snapshot_id = None

        recent_snapshot = Snapshot(
            id=101,
            watch_id=1,
            fetched_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            status_code=200,
            requested_url="https://acme.ai",
            final_url="https://acme.ai",
            final_host="acme.ai",
            page_state="live",
            content_hash="abc123def456",
            hasher_version="v1",
            text_length=5000,
        )

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=None)
        store.find_recent_snapshot_by_hash = AsyncMock(return_value=recent_snapshot)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="abc123def456",
            hasher_version="v1",
        )

        assert is_unchanged is True
        assert existing.id == 101

    @pytest.mark.asyncio
    async def test_fallback_when_last_snapshot_stale(self, sample_watch):
        """If last_snapshot is stale (different ID), still find via time-window."""
        from monitoring.monitor_store import MonitorStore

        # Simulate stale last_snapshot_id pointing to wrong snapshot
        different_snapshot = Snapshot(
            id=100,
            watch_id=1,
            fetched_at=datetime.now(timezone.utc) - timedelta(days=1),
            status_code=200,
            requested_url="https://acme.ai",
            final_url="https://acme.ai",
            final_host="acme.ai",
            page_state="live",
            content_hash="old_hash",  # Different hash
            hasher_version="v1",
            text_length=4000,
        )

        recent_snapshot = Snapshot(
            id=105,  # Different ID
            watch_id=1,
            fetched_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status_code=200,
            requested_url="https://acme.ai",
            final_url="https://acme.ai",
            final_host="acme.ai",
            page_state="live",
            content_hash="abc123def456",
            hasher_version="v1",
            text_length=5000,
        )

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=different_snapshot)
        store.find_recent_snapshot_by_hash = AsyncMock(return_value=recent_snapshot)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="abc123def456",
            hasher_version="v1",
        )

        assert is_unchanged is True
        assert existing.id == 105


class TestRecentHashGuardEdgeCases:
    """Test edge cases for recent-hash guard."""

    @pytest.mark.asyncio
    async def test_no_previous_snapshots(self, sample_watch):
        """First snapshot should not be considered unchanged."""
        from monitoring.monitor_store import MonitorStore

        sample_watch.last_snapshot_id = None

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=None)
        store.find_recent_snapshot_by_hash = AsyncMock(return_value=None)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="first_hash",
            hasher_version="v1",
        )

        assert is_unchanged is False
        assert existing is None


class TestHasherVersionMaintenance:
    """Test hasher_version handling for maintenance diffs."""

    @pytest.mark.asyncio
    async def test_version_upgrade_creates_maintenance_diff(self, sample_watch, sample_snapshot):
        """Hasher version upgrade should mark snapshot for maintenance diff."""
        from monitoring.monitor_store import MonitorStore

        sample_snapshot.hasher_version = "v1"

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=sample_snapshot)
        store.find_recent_snapshot_by_hash = AsyncMock(return_value=None)

        # Same content hash but different hasher version
        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="abc123def456",  # Same hash
            hasher_version="v2",  # New version
        )

        # Should be treated as changed to create maintenance diff
        assert is_unchanged is False

    @pytest.mark.asyncio
    async def test_same_version_same_hash_unchanged(self, sample_watch, sample_snapshot):
        """Same version and hash should be unchanged."""
        from monitoring.monitor_store import MonitorStore

        sample_snapshot.hasher_version = "v1"

        mock_signal_store = MagicMock()
        mock_db = AsyncMock()
        mock_signal_store._db = mock_db

        store = MonitorStore(mock_signal_store)
        store.get_snapshot = AsyncMock(return_value=sample_snapshot)

        is_unchanged, existing = await store.check_hash_unchanged(
            sample_watch,
            content_hash="abc123def456",
            hasher_version="v1",
        )

        assert is_unchanged is True
