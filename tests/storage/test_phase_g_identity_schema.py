"""
Tests for Phase G Sprint 2 Schema (Migrations 20-21)

Covers:
- Migration 20: entity_key_aliases and entity_blocking_index tables
- Migration 21: claim_facts table (bi-temporal SCD-2)
- Schema version bump to 21
- Fresh DB initialization creates all tables
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def fresh_db() -> tuple[SignalStore, str]:
    """Fresh database with all migrations applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store, path

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# SCHEMA VERSION TESTS
# =============================================================================

class TestSchemaVersion:
    """Tests for schema version (at least 21 for Phase G Sprint 2)."""

    def test_current_schema_version_is_at_least_21(self):
        """CURRENT_SCHEMA_VERSION should be at least 21 (Phase G Sprint 2 baseline)."""
        assert CURRENT_SCHEMA_VERSION >= 21

    @pytest.mark.asyncio
    async def test_migration_21_applied(self, fresh_db: tuple[SignalStore, str]):
        """Migration 21 should be in schema_migrations table."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT version FROM schema_migrations WHERE version = 21"
        )
        row = await cursor.fetchone()

        assert row is not None, "Migration 21 should be applied"
        assert row[0] == 21


# =============================================================================
# MIGRATION 20 TESTS - Weak Aliases & Blocking Index
# =============================================================================

class TestMigration20EntityKeyAliases:
    """Tests for migration 20 - entity_key_aliases table."""

    @pytest.mark.asyncio
    async def test_entity_key_aliases_table_exists(self, fresh_db: tuple[SignalStore, str]):
        """entity_key_aliases table should exist."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_key_aliases'"
        )
        row = await cursor.fetchone()

        assert row is not None, "entity_key_aliases table should exist"

    @pytest.mark.asyncio
    async def test_entity_key_aliases_columns(self, fresh_db: tuple[SignalStore, str]):
        """entity_key_aliases should have required columns."""
        store, path = fresh_db

        cursor = await store._db.execute("PRAGMA table_info(entity_key_aliases)")
        columns = {row[1]: row[2] for row in await cursor.fetchall()}

        expected_columns = {
            "alias_key": "TEXT",
            "entity_id": "TEXT",
            "alias_type": "TEXT",
            "confidence": "REAL",
            "source": "TEXT",
            "expires_at": "TEXT",
            "archived_at": "TEXT",
            "created_at": "TEXT",
        }

        for col, dtype in expected_columns.items():
            assert col in columns, f"Missing column: {col}"

    @pytest.mark.asyncio
    async def test_entity_key_aliases_primary_key(self, fresh_db: tuple[SignalStore, str]):
        """alias_key should be primary key."""
        store, path = fresh_db

        cursor = await store._db.execute("PRAGMA table_info(entity_key_aliases)")
        columns = await cursor.fetchall()

        pk_column = next((c for c in columns if c[5] == 1), None)  # pk column at index 5
        assert pk_column is not None, "Should have a primary key"
        assert pk_column[1] == "alias_key", "alias_key should be primary key"

    @pytest.mark.asyncio
    async def test_entity_key_aliases_insert(self, fresh_db: tuple[SignalStore, str]):
        """Can insert into entity_key_aliases table."""
        store, path = fresh_db

        now_iso = datetime.now(timezone.utc).isoformat()

        await store._db.execute(
            """INSERT INTO entity_key_aliases
               (alias_key, entity_id, alias_type, confidence, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("name_norm:acme", "abc123", "name_norm", 0.85, "sec_edgar", now_iso)
        )
        await store._db.commit()

        cursor = await store._db.execute(
            "SELECT entity_id, alias_type FROM entity_key_aliases WHERE alias_key = ?",
            ("name_norm:acme",)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "abc123"
        assert row[1] == "name_norm"


class TestMigration20BlockingIndex:
    """Tests for migration 20 - entity_blocking_index table."""

    @pytest.mark.asyncio
    async def test_entity_blocking_index_table_exists(self, fresh_db: tuple[SignalStore, str]):
        """entity_blocking_index table should exist."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_blocking_index'"
        )
        row = await cursor.fetchone()

        assert row is not None, "entity_blocking_index table should exist"

    @pytest.mark.asyncio
    async def test_entity_blocking_index_columns(self, fresh_db: tuple[SignalStore, str]):
        """entity_blocking_index should have required columns."""
        store, path = fresh_db

        cursor = await store._db.execute("PRAGMA table_info(entity_blocking_index)")
        columns = {row[1]: row[2] for row in await cursor.fetchall()}

        expected_columns = {
            "blocking_token": "TEXT",
            "token_type": "TEXT",
            "entity_id": "TEXT",
            "alias_key": "TEXT",
            "created_at": "TEXT",
        }

        for col, dtype in expected_columns.items():
            assert col in columns, f"Missing column: {col}"

    @pytest.mark.asyncio
    async def test_entity_blocking_index_composite_pk(self, fresh_db: tuple[SignalStore, str]):
        """Should have composite primary key on (blocking_token, token_type, entity_id, alias_key)."""
        store, path = fresh_db

        cursor = await store._db.execute("PRAGMA table_info(entity_blocking_index)")
        pk_cols = [row[1] for row in await cursor.fetchall() if row[5] > 0]

        # All 4 columns should be part of primary key
        assert len(pk_cols) == 4, f"Expected 4 PK columns, got {len(pk_cols)}"

    @pytest.mark.asyncio
    async def test_entity_blocking_index_insert(self, fresh_db: tuple[SignalStore, str]):
        """Can insert into entity_blocking_index table."""
        store, path = fresh_db

        now_iso = datetime.now(timezone.utc).isoformat()

        await store._db.execute(
            """INSERT INTO entity_blocking_index
               (blocking_token, token_type, entity_id, alias_key, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("tok:first:acme", "first", "abc123", "name_norm:acme", now_iso)
        )
        await store._db.commit()

        cursor = await store._db.execute(
            "SELECT entity_id FROM entity_blocking_index WHERE blocking_token = ?",
            ("tok:first:acme",)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "abc123"


# =============================================================================
# MIGRATION 21 TESTS - Bi-Temporal Claim Facts
# =============================================================================

class TestMigration21ClaimFacts:
    """Tests for migration 21 - claim_facts table."""

    @pytest.mark.asyncio
    async def test_claim_facts_table_exists(self, fresh_db: tuple[SignalStore, str]):
        """claim_facts table should exist."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='claim_facts'"
        )
        row = await cursor.fetchone()

        assert row is not None, "claim_facts table should exist"

    @pytest.mark.asyncio
    async def test_claim_facts_columns(self, fresh_db: tuple[SignalStore, str]):
        """claim_facts should have required columns."""
        store, path = fresh_db

        cursor = await store._db.execute("PRAGMA table_info(claim_facts)")
        columns = {row[1]: row[2] for row in await cursor.fetchall()}

        expected_columns = {
            "id": "INTEGER",
            "entity_id": "TEXT",
            "predicate": "TEXT",
            "value_json": "TEXT",
            "source_tier": "INTEGER",
            "confidence": "REAL",
            "valid_from": "TEXT",
            "valid_until": "TEXT",
            "observed_at": "TEXT",
            "last_observed_at": "TEXT",
            "is_retracted": "INTEGER",
            "supporting_signal_ids": "TEXT",
            "source_canonical_key": "TEXT",
            "created_at": "TEXT",
        }

        for col, dtype in expected_columns.items():
            assert col in columns, f"Missing column: {col}"

    @pytest.mark.asyncio
    async def test_claim_facts_insert(self, fresh_db: tuple[SignalStore, str]):
        """Can insert into claim_facts table."""
        store, path = fresh_db

        now_iso = datetime.now(timezone.utc).isoformat()

        await store._db.execute(
            """INSERT INTO claim_facts
               (entity_id, predicate, value_json, source_tier, confidence,
                valid_from, observed_at, is_retracted, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("abc123", "company_name", '"Acme Corp"', 2, 0.85,
             now_iso, now_iso, 0, now_iso)
        )
        await store._db.commit()

        cursor = await store._db.execute(
            "SELECT predicate, value_json, source_tier FROM claim_facts WHERE entity_id = ?",
            ("abc123",)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "company_name"
        assert row[1] == '"Acme Corp"'
        assert row[2] == 2

    @pytest.mark.asyncio
    async def test_claim_facts_active_index_exists(self, fresh_db: tuple[SignalStore, str]):
        """idx_claim_facts_active index should exist."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_claim_facts_active'"
        )
        row = await cursor.fetchone()

        assert row is not None, "idx_claim_facts_active index should exist"

    @pytest.mark.asyncio
    async def test_claim_facts_bi_temporal_query(self, fresh_db: tuple[SignalStore, str]):
        """Can query claim_facts with bi-temporal conditions."""
        store, path = fresh_db

        now_iso = datetime.now(timezone.utc).isoformat()

        # Insert an active fact
        await store._db.execute(
            """INSERT INTO claim_facts
               (entity_id, predicate, value_json, source_tier, confidence,
                valid_from, valid_until, observed_at, is_retracted, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("ent1", "company_name", '"Active Corp"', 1, 0.9,
             now_iso, None, now_iso, 0, now_iso)
        )

        # Insert a closed/superseded fact
        await store._db.execute(
            """INSERT INTO claim_facts
               (entity_id, predicate, value_json, source_tier, confidence,
                valid_from, valid_until, observed_at, is_retracted, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("ent1", "company_name", '"Old Name"', 3, 0.7,
             "2023-01-01T00:00:00Z", now_iso, "2023-01-01T00:00:00Z", 0, now_iso)
        )

        await store._db.commit()

        # Query active facts only
        cursor = await store._db.execute(
            """SELECT value_json FROM claim_facts
               WHERE entity_id = ? AND predicate = ?
               AND valid_until IS NULL AND is_retracted = 0""",
            ("ent1", "company_name")
        )
        rows = await cursor.fetchall()

        assert len(rows) == 1, "Should have exactly 1 active fact"
        assert rows[0][0] == '"Active Corp"'


# =============================================================================
# INDEXES TESTS
# =============================================================================

class TestPhaseGIndexes:
    """Tests for Phase G Sprint 2 indexes."""

    @pytest.mark.asyncio
    async def test_migration_20_indexes_exist(self, fresh_db: tuple[SignalStore, str]):
        """Migration 20 should create required indexes."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in await cursor.fetchall()}

        expected_indexes = {
            "idx_entity_key_aliases_entity",
            "idx_entity_key_aliases_type",
            "idx_blocking_token_lookup",
            "idx_blocking_entity",
        }

        for idx in expected_indexes:
            assert idx in indexes, f"Missing index: {idx}"

    @pytest.mark.asyncio
    async def test_migration_21_indexes_exist(self, fresh_db: tuple[SignalStore, str]):
        """Migration 21 should create required indexes."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in await cursor.fetchall()}

        expected_indexes = {
            "idx_claim_facts_active",
            "idx_claim_facts_entity",
            "idx_claim_facts_tier",
            "idx_claim_facts_temporal",
        }

        for idx in expected_indexes:
            assert idx in indexes, f"Missing index: {idx}"


# =============================================================================
# TRANSACTION IMMEDIATE TESTS
# =============================================================================

class TestTransactionImmediate:
    """Tests for transaction_immediate() context manager."""

    @pytest.mark.asyncio
    async def test_transaction_immediate_commits(self, fresh_db: tuple[SignalStore, str]):
        """transaction_immediate should commit on success."""
        store, path = fresh_db

        now_iso = datetime.now(timezone.utc).isoformat()

        async with store.transaction_immediate() as tx:
            await tx.execute(
                """INSERT INTO entity_key_aliases
                   (alias_key, entity_id, alias_type, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("test:txn", "txn123", "test", 1.0, now_iso)
            )

        # Verify committed
        cursor = await store._db.execute(
            "SELECT entity_id FROM entity_key_aliases WHERE alias_key = ?",
            ("test:txn",)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "txn123"

    @pytest.mark.asyncio
    async def test_transaction_immediate_rollback_on_error(self, fresh_db: tuple[SignalStore, str]):
        """transaction_immediate should rollback on exception."""
        store, path = fresh_db

        now_iso = datetime.now(timezone.utc).isoformat()

        with pytest.raises(ValueError):
            async with store.transaction_immediate() as tx:
                await tx.execute(
                    """INSERT INTO entity_key_aliases
                       (alias_key, entity_id, alias_type, confidence, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    ("test:rollback", "roll123", "test", 1.0, now_iso)
                )
                raise ValueError("Simulated error")

        # Verify rolled back
        cursor = await store._db.execute(
            "SELECT entity_id FROM entity_key_aliases WHERE alias_key = ?",
            ("test:rollback",)
        )
        row = await cursor.fetchone()

        assert row is None, "Transaction should have been rolled back"
