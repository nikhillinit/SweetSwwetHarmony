"""Tests for drift recommendation engine (W5.7)."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from monitoring.drift_recommendations import generate_recommendations
from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store():
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


async def _insert_alert(store, alert_type, metric_name="pass_rate",
                         status="open", created_at=None):
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    from datetime import datetime, timezone
    now = created_at or datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "INSERT INTO canary_drift_alerts "
        "(alert_type, severity, metric_name, message, status, created_at) "
        "VALUES (?, 'warning', ?, 'test', ?, ?)",
        (alert_type, metric_name, status, now),
    )
    await db.commit()
    return cursor.lastrowid


class TestRecommendations:

    @pytest.mark.asyncio
    async def test_no_alerts_no_recommendations(self, store):
        recs = await generate_recommendations(store, lookback_days=7)
        assert len(recs) == 0

    @pytest.mark.asyncio
    async def test_archetype_regression_recommendation(self, store):
        for _ in range(3):
            await _insert_alert(store, "archetype_regression", "pass_rate:cpg")
        recs = await generate_recommendations(store, lookback_days=7)
        archetype_recs = [r for r in recs if r.type == "archetype_expand"]
        assert len(archetype_recs) == 1
        assert "cpg" in archetype_recs[0].message

    @pytest.mark.asyncio
    async def test_pass_rate_drop_recommendation(self, store):
        await _insert_alert(store, "pass_rate_drop")
        recs = await generate_recommendations(store, lookback_days=7)
        collector_recs = [r for r in recs if r.type == "collector_investigate"]
        assert len(collector_recs) == 1

    @pytest.mark.asyncio
    async def test_priority_ordering(self, store):
        """High priority recommendations come first."""
        # Add archetype regressions (→ high)
        for _ in range(3):
            await _insert_alert(store, "archetype_regression", "pass_rate:cpg")
        # Add trend alert (→ medium)
        await _insert_alert(store, "trend_alert", "overall_fp_rate")
        recs = await generate_recommendations(store, lookback_days=7)
        assert len(recs) >= 2
        assert recs[0].priority == "high"

    @pytest.mark.asyncio
    async def test_resolved_alerts_excluded(self, store):
        """Resolved alerts should not trigger recommendations."""
        for _ in range(3):
            await _insert_alert(store, "archetype_regression", "pass_rate:cpg", status="resolved")
        recs = await generate_recommendations(store, lookback_days=7)
        archetype_recs = [r for r in recs if r.type == "archetype_expand"]
        assert len(archetype_recs) == 0
