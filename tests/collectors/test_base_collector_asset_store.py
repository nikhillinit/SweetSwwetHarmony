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
