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


class TestPresetCRUD:
    """Test preset create, read, update, delete operations."""

    @pytest.mark.asyncio
    async def test_save_preset(self):
        """Should save a new preset."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        filters = {"vertical": "health", "min_confidence": 0.7}
        preset_id = await store.save_filter_preset("Health High Confidence", filters)

        assert preset_id is not None
        await store.close()

    @pytest.mark.asyncio
    async def test_load_preset(self):
        """Should load a saved preset."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        filters = {"vertical": "travel", "sources": ["g2crowd"]}
        await store.save_filter_preset("Travel G2", filters)

        loaded = await store.load_filter_preset("Travel G2")

        assert loaded is not None
        assert loaded["filters"]["vertical"] == "travel"
        await store.close()

    @pytest.mark.asyncio
    async def test_load_updates_last_used(self):
        """Loading a preset should update last_used timestamp."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        await store.save_filter_preset("Test Preset", {"vertical": "saas"})

        # Load twice - second load should have later timestamp
        await store.load_filter_preset("Test Preset")
        preset = await store.load_filter_preset("Test Preset")

        assert preset["last_used"] is not None
        await store.close()

    @pytest.mark.asyncio
    async def test_list_presets(self):
        """Should list all presets."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        await store.save_filter_preset("Preset A", {"a": 1})
        await store.save_filter_preset("Preset B", {"b": 2})

        presets = await store.list_filter_presets()

        assert len(presets) == 2
        names = [p["name"] for p in presets]
        assert "Preset A" in names
        assert "Preset B" in names
        await store.close()

    @pytest.mark.asyncio
    async def test_delete_preset(self):
        """Should delete a preset."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        await store.save_filter_preset("To Delete", {"x": 1})
        await store.delete_filter_preset("To Delete")

        presets = await store.list_filter_presets()
        names = [p["name"] for p in presets]
        assert "To Delete" not in names
        await store.close()

    @pytest.mark.asyncio
    async def test_duplicate_name_raises(self):
        """Should raise error on duplicate preset name."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        await store.save_filter_preset("Duplicate", {"a": 1})

        with pytest.raises(ValueError, match="already exists"):
            await store.save_filter_preset("Duplicate", {"b": 2})
        await store.close()

    @pytest.mark.asyncio
    async def test_update_preset(self):
        """Should update existing preset filters."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        await store.save_filter_preset("Updatable", {"old": True})
        await store.update_filter_preset("Updatable", {"new": True})

        loaded = await store.load_filter_preset("Updatable")
        assert loaded["filters"]["new"] is True
        assert "old" not in loaded["filters"]
        await store.close()
