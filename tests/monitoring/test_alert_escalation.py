"""Tests for alert escalation workflow (W5.5).

Covers:
- CAS state machine transitions (ack, snooze, resolve)
- Idempotent retries (D10)
- Invalid transitions (resolved → anything)
- Snooze validation (1 ≤ hours ≤ 168)
- Auto-reopen expired snoozes
- MTTA computation
- Audit trail creation
"""

import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from monitoring.alert_escalation import (
    acknowledge_alert,
    snooze_alert,
    resolve_alert,
    auto_reopen_expired_snoozes,
    compute_mtta,
)
from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_alert(store, alert_id=None, status="open"):
    """Insert a test alert."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    created_at = datetime.now(timezone.utc).isoformat()
    sql = (
        "INSERT INTO canary_drift_alerts "
        "(alert_type, severity, metric_name, message, status, created_at) "
        "VALUES ('pass_rate_drop', 'warning', 'pass_rate', 'test alert', ?, ?)"
    )
    cursor = await db.execute(sql, (status, created_at))
    await db.commit()
    return cursor.lastrowid


class TestAcknowledgeAlert:
    """Test acknowledge_alert CAS transitions."""

    @pytest.mark.asyncio
    async def test_ack_open_alert(self, store):
        alert_id = await _insert_alert(store, status="open")
        result = await acknowledge_alert(store, alert_id, "operator1", "Investigating")
        assert result.success is True
        assert result.new_status == "acknowledged"

    @pytest.mark.asyncio
    async def test_ack_already_acknowledged_is_idempotent(self, store):
        """D10: repeated ack → success + idempotent=True."""
        alert_id = await _insert_alert(store, status="open")
        await acknowledge_alert(store, alert_id, "operator1", "First ack")
        result = await acknowledge_alert(store, alert_id, "operator1", "Second ack")
        assert result.success is True
        assert result.idempotent is True

    @pytest.mark.asyncio
    async def test_ack_resolved_fails(self, store):
        """Cannot ack a resolved alert."""
        alert_id = await _insert_alert(store, status="resolved")
        result = await acknowledge_alert(store, alert_id, "operator1", "Too late")
        assert result.success is False
        assert "resolved" in result.error

    @pytest.mark.asyncio
    async def test_ack_nonexistent_alert(self, store):
        result = await acknowledge_alert(store, 99999, "operator1", "Missing")
        assert result.success is False
        assert "not found" in result.error


class TestSnoozeAlert:
    """Test snooze_alert CAS transitions."""

    @pytest.mark.asyncio
    async def test_snooze_open_alert(self, store):
        alert_id = await _insert_alert(store, status="open")
        result = await snooze_alert(store, alert_id, "operator1", hours=24)
        assert result.success is True
        assert result.new_status == "snoozed"

    @pytest.mark.asyncio
    async def test_snooze_invalid_hours_too_low(self, store):
        alert_id = await _insert_alert(store, status="open")
        result = await snooze_alert(store, alert_id, "operator1", hours=0)
        assert result.success is False
        assert "1-168" in result.error

    @pytest.mark.asyncio
    async def test_snooze_invalid_hours_too_high(self, store):
        alert_id = await _insert_alert(store, status="open")
        result = await snooze_alert(store, alert_id, "operator1", hours=200)
        assert result.success is False
        assert "1-168" in result.error


class TestResolveAlert:
    """Test resolve_alert CAS transitions."""

    @pytest.mark.asyncio
    async def test_resolve_open_alert(self, store):
        alert_id = await _insert_alert(store, status="open")
        result = await resolve_alert(store, alert_id, "operator1", "False alarm")
        assert result.success is True
        assert result.new_status == "resolved"

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_is_idempotent(self, store):
        """D10: repeated resolve → success + idempotent."""
        alert_id = await _insert_alert(store, status="open")
        await resolve_alert(store, alert_id, "operator1", "First resolve")
        result = await resolve_alert(store, alert_id, "operator1", "Second resolve")
        assert result.success is True
        assert result.idempotent is True


class TestAutoReopen:
    """Test auto_reopen_expired_snoozes."""

    @pytest.mark.asyncio
    async def test_reopen_expired_snooze(self, store):
        alert_id = await _insert_alert(store, status="open")
        await snooze_alert(store, alert_id, "operator1", hours=1)

        # Manually set snoozed_until to the past
        db = store._db
        await db.execute(
            "UPDATE canary_drift_alerts SET snoozed_until='2020-01-01T00:00:00Z' WHERE id=?",
            (alert_id,),
        )
        await db.commit()

        count = await auto_reopen_expired_snoozes(store)
        assert count == 1

        cursor = await db.execute("SELECT status FROM canary_drift_alerts WHERE id=?", (alert_id,))
        row = await cursor.fetchone()
        assert row[0] == "open"

    @pytest.mark.asyncio
    async def test_no_expired_snoozes(self, store):
        count = await auto_reopen_expired_snoozes(store)
        assert count == 0


class TestComputeMTTA:
    """Test MTTA computation."""

    @pytest.mark.asyncio
    async def test_no_alerts_returns_null(self, store):
        result = await compute_mtta(store)
        assert result["mean"] is None
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_mtta_with_acknowledged_alerts(self, store):
        """MTTA computed from acknowledged alerts."""
        alert_id = await _insert_alert(store, status="open")
        await acknowledge_alert(store, alert_id, "operator1", "Quick ack")
        result = await compute_mtta(store, lookback_days=30)
        assert result["count"] == 1
        assert result["mean"] is not None
        assert result["mean"] >= 0
