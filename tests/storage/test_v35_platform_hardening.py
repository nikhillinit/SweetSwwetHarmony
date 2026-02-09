"""Tests for v35 platform hardening migration (audit_events + run_history)."""

import os
import tempfile

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SignalStore(db_path=path)
    await store.initialize()
    yield store
    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


class TestV35Migration:
    @pytest.mark.asyncio
    async def test_audit_events_table_exists(self, store):
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
        )
        row = await cursor.fetchone()
        assert row is not None, "audit_events table should exist after migration"

    @pytest.mark.asyncio
    async def test_run_history_table_exists(self, store):
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='run_history'"
        )
        row = await cursor.fetchone()
        assert row is not None, "run_history table should exist after migration"

    @pytest.mark.asyncio
    async def test_audit_events_columns(self, store):
        db = store._db
        cursor = await db.execute("PRAGMA table_info(audit_events)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "action_type", "entity_type", "entity_id",
            "actor_id", "actor_email", "actor_role",
            "before_state", "after_state",
            "reason", "correlation_id", "metadata",
            "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_run_history_columns(self, store):
        db = store._db
        cursor = await db.execute("PRAGMA table_info(run_history)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "run_type", "status",
            "actor_id", "actor_email",
            "inputs_summary", "inputs_hash",
            "result", "error_message", "progress_pct",
            "started_at", "completed_at", "created_at",
            "correlation_id",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_run_history_status_constraint(self, store):
        """Run history status column should have CHECK constraint."""
        db = store._db
        # Try inserting an invalid status
        with pytest.raises(Exception):
            await db.execute(
                """
                INSERT INTO run_history (id, run_type, status, created_at)
                VALUES ('test-1', 'hunter', 'INVALID_STATUS', datetime('now'))
                """
            )
            await db.commit()

    @pytest.mark.asyncio
    async def test_audit_events_indexes(self, store):
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_events'"
        )
        rows = await cursor.fetchall()
        index_names = {row[0] for row in rows}
        expected_indexes = {
            "idx_audit_events_created",
            "idx_audit_events_entity",
            "idx_audit_events_action",
            "idx_audit_events_actor",
            "idx_audit_events_correlation",
        }
        assert expected_indexes.issubset(index_names), (
            f"Missing indexes: {expected_indexes - index_names}"
        )

    @pytest.mark.asyncio
    async def test_run_history_indexes(self, store):
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='run_history'"
        )
        rows = await cursor.fetchall()
        index_names = {row[0] for row in rows}
        expected_indexes = {
            "idx_run_history_type_status",
            "idx_run_history_created",
            "idx_run_history_status",
        }
        assert expected_indexes.issubset(index_names), (
            f"Missing indexes: {expected_indexes - index_names}"
        )

    @pytest.mark.asyncio
    async def test_schema_version_is_35(self, store):
        db = store._db
        cursor = await db.execute(
            "SELECT MAX(version) FROM schema_migrations"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert int(row[0]) >= 35
