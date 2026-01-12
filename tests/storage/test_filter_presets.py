# tests/storage/test_filter_presets.py
"""Tests for filter presets functionality."""
import pytest
from storage.signal_store import SignalStore


class TestPresetsTable:
    """Test filter presets table creation."""

    @pytest.mark.asyncio
    async def test_presets_table_exists(self):
        """Presets table should be created during initialization."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='filter_presets'"
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "filter_presets"
        await store.close()
