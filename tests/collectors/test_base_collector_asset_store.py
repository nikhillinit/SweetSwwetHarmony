"""Test BaseCollector integration with SourceAssetStore for change detection"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from collectors.base import BaseCollector
from storage.signal_store import SignalStore
from storage.source_asset_store import SourceAssetStore


class MockCollector(BaseCollector):
    """Mock collector for testing BaseCollector"""

    SOURCE_TYPE = "mock_source"

    async def _collect_signals(self):
        """Mock implementation"""
        return []


@pytest.mark.asyncio
class TestBaseCollectorAssetStore:
    """Test BaseCollector asset store integration"""

    async def test_base_collector_accepts_asset_store(self):
        """BaseCollector accepts asset_store parameter"""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        asset_store = SourceAssetStore(db_path=":memory:")
        await asset_store.initialize()

        collector = MockCollector(store=store, asset_store=asset_store)

        assert collector.asset_store is asset_store

        await store.close()
        await asset_store.close()

    async def test_base_collector_asset_store_optional(self):
        """BaseCollector works without asset_store parameter"""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        collector = MockCollector(store=store)

        assert collector.asset_store is None

        await store.close()

    async def test_save_asset_with_change_detection_first_time(self):
        """First save of an asset returns is_new=True"""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        asset_store = SourceAssetStore(db_path=":memory:")
        await asset_store.initialize()

        collector = MockCollector(store=store, asset_store=asset_store)

        # Save asset first time
        raw_data = {"id": "test-123", "name": "Test", "updated_at": "2024-01-10"}
        is_new, changes = await collector._save_asset_with_change_detection(
            source_type="mock_source",
            external_id="test-123",
            raw_data=raw_data,
        )

        assert is_new is True
        assert changes == []

        await store.close()
        await asset_store.close()

    async def test_save_asset_with_change_detection_unchanged(self):
        """Second save of unchanged asset returns is_new=False, changes=[]"""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        asset_store = SourceAssetStore(db_path=":memory:")
        await asset_store.initialize()

        collector = MockCollector(store=store, asset_store=asset_store)

        raw_data = {"id": "test-123", "name": "Test", "updated_at": "2024-01-10"}

        # First save
        is_new, changes = await collector._save_asset_with_change_detection(
            source_type="mock_source",
            external_id="test-123",
            raw_data=raw_data,
        )
        assert is_new is True

        # Second save (unchanged)
        is_new, changes = await collector._save_asset_with_change_detection(
            source_type="mock_source",
            external_id="test-123",
            raw_data=raw_data,
        )

        assert is_new is False
        assert len(changes) == 0

        await store.close()
        await asset_store.close()

    async def test_save_asset_with_change_detection_changed(self):
        """Modified asset returns is_new=False, changes=[...]"""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        asset_store = SourceAssetStore(db_path=":memory:")
        await asset_store.initialize()

        collector = MockCollector(store=store, asset_store=asset_store)

        data_v1 = {"id": "test-123", "name": "Test", "stars": 10}
        data_v2 = {"id": "test-123", "name": "Test Updated", "stars": 15}

        # First save
        is_new, changes = await collector._save_asset_with_change_detection(
            source_type="mock_source",
            external_id="test-123",
            raw_data=data_v1,
        )
        assert is_new is True

        # Second save (changed)
        is_new, changes = await collector._save_asset_with_change_detection(
            source_type="mock_source",
            external_id="test-123",
            raw_data=data_v2,
        )

        assert is_new is False
        assert len(changes) > 0

        await store.close()
        await asset_store.close()
