"""Tests for v27 audit_log migration.

Verifies:
  - Table is created with correct columns
  - All three indexes are created
  - Can insert and query audit log entries
  - Queries work across time ranges, entity lookups, and action types
"""

import json
import pytest
from datetime import datetime, timezone, timedelta

import aiosqlite


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_db_with_audit_log() -> aiosqlite.Connection:
    """Create an in-memory DB with the audit_log table applied."""
    from storage.migrations.v27_audit_log import AUDIT_LOG_DDL

    db = await aiosqlite.connect(":memory:")
    await db.executescript(AUDIT_LOG_DDL)
    await db.commit()
    return db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAuditLogSchema:
    """Test the audit_log table schema."""

    @pytest.mark.asyncio
    async def test_table_created(self):
        """audit_log table should exist after applying DDL."""
        db = await _create_db_with_audit_log()
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "audit_log"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_columns(self):
        """audit_log table should have the expected columns."""
        db = await _create_db_with_audit_log()
        try:
            cursor = await db.execute("PRAGMA table_info(audit_log)")
            columns = await cursor.fetchall()
            col_names = [c[1] for c in columns]

            expected = [
                "id", "action_type", "entity_type", "entity_id",
                "actor", "details", "created_at",
            ]
            assert col_names == expected
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_not_null_constraints(self):
        """action_type, entity_type, entity_id, created_at must be NOT NULL."""
        db = await _create_db_with_audit_log()
        try:
            cursor = await db.execute("PRAGMA table_info(audit_log)")
            columns = await cursor.fetchall()
            # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
            col_notnull = {c[1]: bool(c[3]) for c in columns}

            assert col_notnull["action_type"] is True
            assert col_notnull["entity_type"] is True
            assert col_notnull["entity_id"] is True
            assert col_notnull["created_at"] is True
            # actor and details are nullable
            assert col_notnull["actor"] is False
            assert col_notnull["details"] is False
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_indexes_created(self):
        """All three indexes should exist."""
        db = await _create_db_with_audit_log()
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_log'"
            )
            rows = await cursor.fetchall()
            index_names = {r[0] for r in rows}

            assert "idx_audit_log_created" in index_names
            assert "idx_audit_log_entity" in index_names
            assert "idx_audit_log_action" in index_names
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_ddl_is_idempotent(self):
        """Applying DDL twice should not fail (CREATE IF NOT EXISTS)."""
        from storage.migrations.v27_audit_log import AUDIT_LOG_DDL

        db = await aiosqlite.connect(":memory:")
        try:
            await db.executescript(AUDIT_LOG_DDL)
            await db.executescript(AUDIT_LOG_DDL)  # second apply
            await db.commit()

            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
            )
            row = await cursor.fetchone()
            assert row is not None
        finally:
            await db.close()


class TestAuditLogInsertAndQuery:
    """Test inserting and querying audit log entries."""

    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self):
        """Should insert a row and retrieve it by id."""
        db = await _create_db_with_audit_log()
        try:
            now = _utc_now_iso()
            details = json.dumps({"reason": "Fits thesis", "confidence": 0.85})

            cursor = await db.execute(
                """INSERT INTO audit_log
                   (action_type, entity_type, entity_id, actor, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("triage_approve", "signal", "42", "operator", details, now),
            )
            await db.commit()
            row_id = cursor.lastrowid

            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE id = ?", (row_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[1] == "triage_approve"
            assert row[2] == "signal"
            assert row[3] == "42"
            assert row[4] == "operator"
            assert json.loads(row[5])["reason"] == "Fits thesis"
            assert row[6] == now
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_insert_nullable_fields(self):
        """actor and details can be NULL."""
        db = await _create_db_with_audit_log()
        try:
            now = _utc_now_iso()
            cursor = await db.execute(
                """INSERT INTO audit_log
                   (action_type, entity_type, entity_id, actor, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("delivery_policy_block", "signal", "99", None, None, now),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT actor, details FROM audit_log WHERE id = ?",
                (cursor.lastrowid,),
            )
            row = await cursor.fetchone()
            assert row[0] is None
            assert row[1] is None
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_not_null_violation(self):
        """Inserting without required NOT NULL fields should fail."""
        db = await _create_db_with_audit_log()
        try:
            with pytest.raises(aiosqlite.IntegrityError):
                await db.execute(
                    """INSERT INTO audit_log
                       (action_type, entity_type, entity_id, actor, details, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (None, "signal", "1", "operator", None, _utc_now_iso()),
                )
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_autoincrement_id(self):
        """IDs should auto-increment."""
        db = await _create_db_with_audit_log()
        try:
            now = _utc_now_iso()
            ids = []
            for i in range(3):
                cursor = await db.execute(
                    """INSERT INTO audit_log
                       (action_type, entity_type, entity_id, actor, details, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("triage_approve", "signal", str(i), "operator", None, now),
                )
                ids.append(cursor.lastrowid)
            await db.commit()

            assert ids[1] > ids[0]
            assert ids[2] > ids[1]
        finally:
            await db.close()


class TestAuditLogQueryPatterns:
    """Test the query patterns the indexes are designed to support."""

    async def _seed(self, db: aiosqlite.Connection):
        """Insert diverse audit entries for query tests."""
        base = datetime(2026, 2, 7, 12, 0, 0, tzinfo=timezone.utc)
        entries = [
            ("triage_approve", "signal", "10", "operator", None, base.isoformat()),
            ("triage_reject", "signal", "11", "operator",
             json.dumps({"reason": "B2B SaaS"}),
             (base + timedelta(minutes=5)).isoformat()),
            ("triage_defer", "signal", "12", "operator", None,
             (base + timedelta(minutes=10)).isoformat()),
            ("manual_push", "signal", "10", "operator",
             json.dumps({"notion_page_id": "abc-123"}),
             (base + timedelta(minutes=15)).isoformat()),
            ("batch_push", "batch", "batch-001", "pipeline",
             json.dumps({"count": 5}),
             (base + timedelta(minutes=20)).isoformat()),
            ("delivery_policy_block", "signal", "13", "pipeline",
             json.dumps({"rule": "max_daily_reached"}),
             (base + timedelta(minutes=25)).isoformat()),
        ]
        for entry in entries:
            await db.execute(
                """INSERT INTO audit_log
                   (action_type, entity_type, entity_id, actor, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                entry,
            )
        await db.commit()

    @pytest.mark.asyncio
    async def test_time_range_query(self):
        """Should retrieve entries within a time range (idx_audit_log_created)."""
        db = await _create_db_with_audit_log()
        try:
            await self._seed(db)

            start = datetime(2026, 2, 7, 12, 10, 0, tzinfo=timezone.utc).isoformat()
            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE created_at >= ? ORDER BY created_at DESC",
                (start,),
            )
            rows = await cursor.fetchall()
            # Should get: triage_defer (12:10), manual_push (12:15),
            #             batch_push (12:20), delivery_policy_block (12:25)
            assert len(rows) == 4
            # Verify DESC ordering
            assert rows[0][1] == "delivery_policy_block"
            assert rows[-1][1] == "triage_defer"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_entity_lookup(self):
        """Should retrieve all actions for a specific entity (idx_audit_log_entity)."""
        db = await _create_db_with_audit_log()
        try:
            await self._seed(db)

            cursor = await db.execute(
                """SELECT action_type FROM audit_log
                   WHERE entity_type = ? AND entity_id = ?
                   ORDER BY created_at""",
                ("signal", "10"),
            )
            rows = await cursor.fetchall()
            # Signal 10 has triage_approve then manual_push
            assert len(rows) == 2
            assert rows[0][0] == "triage_approve"
            assert rows[1][0] == "manual_push"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_action_type_query(self):
        """Should retrieve entries by action type (idx_audit_log_action)."""
        db = await _create_db_with_audit_log()
        try:
            await self._seed(db)

            cursor = await db.execute(
                """SELECT entity_id FROM audit_log
                   WHERE action_type = ?
                   ORDER BY created_at DESC""",
                ("triage_approve",),
            )
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "10"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_batch_entity_type(self):
        """Should distinguish between entity types."""
        db = await _create_db_with_audit_log()
        try:
            await self._seed(db)

            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE entity_type = ?",
                ("batch",),
            )
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][1] == "batch_push"
            assert rows[0][3] == "batch-001"
        finally:
            await db.close()


class TestAuditLogViaMigration:
    """Test that the migration integrates correctly with SignalStore."""

    @pytest.mark.asyncio
    async def test_signal_store_version_is_27(self):
        """CURRENT_SCHEMA_VERSION should be >= 27 with v27 migration present."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION, MIGRATIONS
        assert CURRENT_SCHEMA_VERSION >= 27, f"v27 migration requires schema >= 27, got {CURRENT_SCHEMA_VERSION}"
        assert 27 in MIGRATIONS, "v27 migration missing from MIGRATIONS dict"

    @pytest.mark.asyncio
    async def test_migration_27_in_migrations_dict(self):
        """Migration 27 should be in the MIGRATIONS dict."""
        from storage.signal_store import MIGRATIONS
        assert 27 in MIGRATIONS

    @pytest.mark.asyncio
    async def test_migration_27_matches_ddl(self):
        """Migration 27 should reference the AUDIT_LOG_DDL constant."""
        from storage.signal_store import MIGRATIONS
        from storage.migrations.v27_audit_log import AUDIT_LOG_DDL
        assert MIGRATIONS[27] is AUDIT_LOG_DDL
