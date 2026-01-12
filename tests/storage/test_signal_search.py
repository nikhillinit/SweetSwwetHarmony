# tests/storage/test_signal_search.py
"""Tests for FTS5 signal search functionality."""
import pytest
from storage.signal_store import SignalStore


class TestFTSSetup:
    """Test FTS5 virtual table creation."""

    @pytest.mark.asyncio
    async def test_fts_table_exists_after_init(self):
        """FTS table should be created during initialization."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # Query sqlite_master to check for FTS table
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals_fts'"
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "signals_fts"
        await store.close()
