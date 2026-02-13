"""Tests for v32 functional_schemas migration DDL.

Verifies:
- functional_schemas table existence + columns
- Schema version bumped to 32
- Indexes created (company_active, archetype partial)
- UNIQUE(company_id, schema_version) constraint enforced
- is_advisory defaults to 0, is_active defaults to 1
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION, MIGRATIONS


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


class TestV32Migration:
    """Tests for v32 functional_schemas DDL."""

    @pytest.mark.asyncio
    async def test_schema_version_is_32(self):
        """CURRENT_SCHEMA_VERSION should be >= 32 with v32 migration present."""
        assert CURRENT_SCHEMA_VERSION >= 32, f"v32 migration requires schema >= 32, got {CURRENT_SCHEMA_VERSION}"
        assert 32 in MIGRATIONS, "v32 migration missing from MIGRATIONS dict"

    @pytest.mark.asyncio
    async def test_functional_schemas_table_exists(self, store):
        """functional_schemas table should exist after migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='functional_schemas'"
        )
        row = await cursor.fetchone()
        assert row is not None, "functional_schemas table should exist"

    @pytest.mark.asyncio
    async def test_functional_schemas_columns(self, store):
        """functional_schemas should have expected columns."""
        cursor = await store._db.execute("PRAGMA table_info(functional_schemas)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "company_id", "schema_version",
            "problem_solved_text", "customer_text", "approach_text",
            "customer_archetype", "problem_archetypes",
            "schema_confidence", "is_advisory",
            "evidence_signal_ids", "extraction_model", "extraction_prompt_version",
            "is_active", "superseded_by", "created_at",
        }
        assert expected == col_names

    @pytest.mark.asyncio
    async def test_unique_company_version_constraint(self, store):
        """UNIQUE(company_id, schema_version) should prevent duplicates."""
        import aiosqlite

        await store._db.execute(
            """INSERT INTO functional_schemas (company_id, schema_version, customer_archetype)
               VALUES (?, ?, ?)""",
            ("comp-001", 1, "foodies"),
        )

        with pytest.raises(aiosqlite.IntegrityError):
            await store._db.execute(
                """INSERT INTO functional_schemas (company_id, schema_version, customer_archetype)
                   VALUES (?, ?, ?)""",
                ("comp-001", 1, "travelers"),
            )

    @pytest.mark.asyncio
    async def test_different_versions_allowed(self, store):
        """Same company_id with different schema_version should succeed."""
        await store._db.execute(
            """INSERT INTO functional_schemas (company_id, schema_version, customer_archetype)
               VALUES (?, ?, ?)""",
            ("comp-002", 1, "foodies"),
        )
        await store._db.execute(
            """INSERT INTO functional_schemas (company_id, schema_version, customer_archetype)
               VALUES (?, ?, ?)""",
            ("comp-002", 2, "travelers"),
        )

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM functional_schemas WHERE company_id = ?",
            ("comp-002",),
        )
        row = await cursor.fetchone()
        assert row[0] == 2

    @pytest.mark.asyncio
    async def test_is_advisory_defaults_to_zero(self, store):
        """is_advisory should default to 0 (False)."""
        await store._db.execute(
            """INSERT INTO functional_schemas (company_id, schema_version, customer_archetype)
               VALUES (?, ?, ?)""",
            ("comp-003", 1, "creators"),
        )

        cursor = await store._db.execute(
            "SELECT is_advisory FROM functional_schemas WHERE company_id = ?",
            ("comp-003",),
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_is_active_defaults_to_one(self, store):
        """is_active should default to 1 (True)."""
        await store._db.execute(
            """INSERT INTO functional_schemas (company_id, schema_version, customer_archetype)
               VALUES (?, ?, ?)""",
            ("comp-004", 1, "gamers"),
        )

        cursor = await store._db.execute(
            "SELECT is_active FROM functional_schemas WHERE company_id = ?",
            ("comp-004",),
        )
        row = await cursor.fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_idx_fs_company_active_exists(self, store):
        """Index idx_fs_company_active should exist."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_fs_company_active'"
        )
        row = await cursor.fetchone()
        assert row is not None, "idx_fs_company_active index should exist"

    @pytest.mark.asyncio
    async def test_idx_fs_archetype_exists(self, store):
        """Partial index idx_fs_archetype should exist."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_fs_archetype'"
        )
        row = await cursor.fetchone()
        assert row is not None, "idx_fs_archetype index should exist"
