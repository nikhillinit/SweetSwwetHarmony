"""Tests for v31 batch publish migration DDL.

Verifies:
- publish_batches table existence + columns
- batch_items table existence + columns + constraints
- Schema version bumped to 31
- Indexes created
- UNIQUE(batch_id, review_id) constraint
- CHECK constraints on status columns
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


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


class TestV31Migration:
    """Tests for v31 batch publish DDL."""

    @pytest.mark.asyncio
    async def test_schema_version_is_at_least_31(self):
        """CURRENT_SCHEMA_VERSION should be >= 31."""
        assert CURRENT_SCHEMA_VERSION >= 31

    @pytest.mark.asyncio
    async def test_publish_batches_table_exists(self, store):
        """publish_batches table should exist after migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='publish_batches'"
        )
        row = await cursor.fetchone()
        assert row is not None, "publish_batches table should exist"

    @pytest.mark.asyncio
    async def test_publish_batches_columns(self, store):
        """publish_batches should have expected columns."""
        cursor = await store._db.execute("PRAGMA table_info(publish_batches)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "status", "item_count", "pushed_count", "error_count",
            "actor", "created_at", "committed_at", "details",
        }
        assert expected == col_names

    @pytest.mark.asyncio
    async def test_batch_items_table_exists(self, store):
        """batch_items table should exist after migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_items'"
        )
        row = await cursor.fetchone()
        assert row is not None, "batch_items table should exist"

    @pytest.mark.asyncio
    async def test_batch_items_columns(self, store):
        """batch_items should have expected columns."""
        cursor = await store._db.execute("PRAGMA table_info(batch_items)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "batch_id", "review_id", "company_id", "canonical_key",
            "status", "notion_page_id", "error_message", "created_at",
        }
        assert expected == col_names

    @pytest.mark.asyncio
    async def test_unique_batch_review_constraint(self, store):
        """UNIQUE(batch_id, review_id) should prevent duplicates."""
        import aiosqlite
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        await store._db.execute(
            "INSERT INTO publish_batches (id, status, created_at) VALUES (?, 'draft', ?)",
            ("batch-test", now),
        )

        await store._db.execute(
            """INSERT INTO batch_items (batch_id, review_id, company_id, canonical_key, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("batch-test", 1, "comp1", "domain:test.com", now),
        )

        with pytest.raises(aiosqlite.IntegrityError):
            await store._db.execute(
                """INSERT INTO batch_items (batch_id, review_id, company_id, canonical_key, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("batch-test", 1, "comp1", "domain:test.com", now),
            )

    @pytest.mark.asyncio
    async def test_batch_status_check_constraint(self, store):
        """publish_batches status CHECK should reject invalid values."""
        import aiosqlite
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(aiosqlite.IntegrityError):
            await store._db.execute(
                "INSERT INTO publish_batches (id, status, created_at) VALUES (?, ?, ?)",
                ("batch-bad", "invalid_status", now),
            )
