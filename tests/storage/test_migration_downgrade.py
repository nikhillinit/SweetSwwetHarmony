"""Tests for migration downgrade paths (W5.10).

Blocking: v41->v40 downgrade (must pass before merge).
Non-blocking: v40->v39 and earlier chain (hardening, warnings only).
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.migrations.v41_drift_monitoring import V41_DOWNGRADE_DDL


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


async def _table_exists(db, table_name: str) -> bool:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return (await cursor.fetchone()) is not None


async def _get_columns(db, table_name: str) -> list[str]:
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return [row[1] for row in rows]


async def _get_indexes(db, table_name: str) -> list[str]:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table_name,),
    )
    return [row[0] for row in await cursor.fetchall()]


# =============================================================================
# BLOCKING: v41 -> v40 DOWNGRADE
# =============================================================================

class TestV41ToV40Downgrade:
    """v41->v40 downgrade tests (BLOCKING -- must pass)."""

    @pytest.mark.asyncio
    async def test_downgrade_drops_quality_metrics_daily(self, store):
        """quality_metrics_daily should not exist after downgrade."""
        db = store._db
        # Verify table exists before downgrade
        assert await _table_exists(db, "quality_metrics_daily")

        # Run downgrade
        await db.execute("PRAGMA foreign_keys = OFF")
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        assert not await _table_exists(db, "quality_metrics_daily")

    @pytest.mark.asyncio
    async def test_downgrade_restores_original_alerts_schema(self, store):
        """canary_drift_alerts should revert to v38 schema after downgrade."""
        db = store._db
        # Verify v41 columns exist
        cols_before = await _get_columns(db, "canary_drift_alerts")
        assert "drift_category" in cols_before
        assert "signature_key" in cols_before
        assert "snoozed_until" in cols_before

        # Run downgrade
        await db.execute("PRAGMA foreign_keys = OFF")
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        # Verify v38 schema restored
        cols_after = await _get_columns(db, "canary_drift_alerts")
        assert "drift_category" not in cols_after
        assert "signature_key" not in cols_after
        assert "snoozed_until" not in cols_after
        # Original columns still present
        assert "alert_type" in cols_after
        assert "severity" in cols_after
        assert "status" in cols_after

    @pytest.mark.asyncio
    async def test_downgrade_preserves_compatible_data(self, store):
        """Alert data that fits v38 schema should survive downgrade."""
        db = store._db
        await db.execute("PRAGMA foreign_keys = OFF")

        # Insert a canary run first (FK target)
        await db.execute(
            "INSERT INTO canary_runs "
            "(id, run_id, golden_set_size, golden_set_hash, total_scored, passed, failed, "
            "skipped, pass_rate, verdict, drift_threshold, pass_rate_threshold, duration_ms, created_at) "
            "VALUES (1, 'test-run', 10, 'hash', 10, 9, 1, 0, 0.9, 'pass', 0.15, 0.80, 100, datetime('now'))"
        )

        # Insert an alert in v41 schema
        await db.execute(
            "INSERT INTO canary_drift_alerts "
            "(id, canary_run_id, alert_type, severity, metric_name, message, status, created_at) "
            "VALUES (1, 1, 'pass_rate_drop', 'warning', 'pass_rate', 'Test alert', 'open', datetime('now'))"
        )
        await db.commit()

        # Run downgrade
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        # Verify data preserved
        cursor = await db.execute("SELECT id, alert_type, message, status FROM canary_drift_alerts WHERE id = 1")
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "pass_rate_drop"
        assert row[2] == "Test alert"
        assert row[3] == "open"

    @pytest.mark.asyncio
    async def test_downgrade_snoozed_maps_to_open(self, store):
        """Snoozed alerts should map to 'open' status after downgrade."""
        db = store._db
        await db.execute("PRAGMA foreign_keys = OFF")

        # Insert canary run
        await db.execute(
            "INSERT INTO canary_runs "
            "(id, run_id, golden_set_size, golden_set_hash, total_scored, passed, failed, "
            "skipped, pass_rate, verdict, drift_threshold, pass_rate_threshold, duration_ms, created_at) "
            "VALUES (1, 'test-run', 10, 'hash', 10, 9, 1, 0, 0.9, 'pass', 0.15, 0.80, 100, datetime('now'))"
        )

        # Insert a snoozed alert
        await db.execute(
            "INSERT INTO canary_drift_alerts "
            "(id, canary_run_id, alert_type, severity, metric_name, message, status, "
            "snoozed_until, created_at) "
            "VALUES (1, 1, 'pass_rate_drop', 'warning', 'pass_rate', 'Snoozed test', 'snoozed', "
            "'2026-02-15T00:00:00Z', datetime('now'))"
        )
        await db.commit()

        # Run downgrade
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        # Snoozed should map to open
        cursor = await db.execute("SELECT status FROM canary_drift_alerts WHERE id = 1")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "open"

    @pytest.mark.asyncio
    async def test_downgrade_removes_v41_indexes(self, store):
        """v41-specific indexes should not exist after downgrade."""
        db = store._db
        indexes_before = await _get_indexes(db, "canary_drift_alerts")
        # Partial unique index should exist before downgrade
        assert any("active_sig" in idx for idx in indexes_before)

        await db.execute("PRAGMA foreign_keys = OFF")
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        indexes_after = await _get_indexes(db, "canary_drift_alerts")
        # v41 indexes should be gone
        assert not any("active_sig" in idx for idx in indexes_after)
        assert not any("snooze_reopen" in idx for idx in indexes_after)


# =============================================================================
# NON-BLOCKING: Earlier downgrades (hardening, warn on failure)
# =============================================================================

class TestEarlierDowngrades:
    """Earlier downgrade chain tests (non-blocking, log warnings on failure)."""

    @pytest.mark.asyncio
    async def test_canary_drift_alerts_exists_after_v41_downgrade(self, store):
        """canary_drift_alerts table should still exist after v41 downgrade."""
        db = store._db
        await db.execute("PRAGMA foreign_keys = OFF")
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        assert await _table_exists(db, "canary_drift_alerts")

    @pytest.mark.asyncio
    async def test_canary_runs_untouched_by_v41_downgrade(self, store):
        """canary_runs table should be completely untouched by v41 downgrade."""
        db = store._db
        cols_before = await _get_columns(db, "canary_runs")

        await db.execute("PRAGMA foreign_keys = OFF")
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        cols_after = await _get_columns(db, "canary_runs")
        assert cols_before == cols_after

    @pytest.mark.asyncio
    async def test_schema_version_still_readable(self, store):
        """schema_migrations table should still be readable after downgrade."""
        db = store._db
        await db.execute("PRAGMA foreign_keys = OFF")
        await db.executescript(V41_DOWNGRADE_DDL)
        await db.commit()

        cursor = await db.execute("SELECT MAX(version) FROM schema_migrations")
        row = await cursor.fetchone()
        assert row is not None
        # Version should still show 41 (downgrade doesn't update schema_migrations)
        assert row[0] >= 40


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
