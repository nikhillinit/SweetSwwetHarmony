"""
Tests for suppression cache warmup on pipeline initialization.

TDD Phase: RED - These tests should FAIL initially.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# We expect these imports to work after implementation
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


class TestPipelineConfigWarmup:
    """Test PipelineConfig has warmup_suppression_cache field"""

    def test_config_has_warmup_field(self):
        """PipelineConfig should have warmup_suppression_cache field"""
        config = PipelineConfig()
        assert hasattr(config, 'warmup_suppression_cache')

    def test_warmup_defaults_to_true(self):
        """warmup_suppression_cache should default to True"""
        config = PipelineConfig()
        assert config.warmup_suppression_cache is True

    def test_warmup_can_be_disabled(self):
        """warmup_suppression_cache can be set to False"""
        config = PipelineConfig(warmup_suppression_cache=False)
        assert config.warmup_suppression_cache is False


class TestPipelineWarmup:
    """Test suppression cache warmup during initialization"""

    @pytest.mark.asyncio
    async def test_pipeline_calls_warmup_on_init(self):
        """Pipeline should call _warmup_suppression_cache during initialize"""
        config = PipelineConfig(
            warmup_suppression_cache=True,
            notion_api_key="test_key",
            notion_database_id="test_db",
        )
        pipeline = DiscoveryPipeline(config)

        # Mock the warmup method to verify it's called
        with patch.object(pipeline, '_warmup_suppression_cache', new_callable=AsyncMock) as mock_warmup:
            with patch.object(pipeline, '_store', MagicMock()):
                with patch('workflows.pipeline.SignalStore') as mock_store:
                    mock_store_instance = AsyncMock()
                    mock_store.return_value = mock_store_instance

                    with patch('workflows.pipeline.NotionConnector') as mock_notion:
                        await pipeline.initialize()

                        # Verify warmup was called
                        mock_warmup.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_skips_warmup_when_disabled(self):
        """Pipeline should skip warmup when warmup_suppression_cache=False"""
        config = PipelineConfig(
            warmup_suppression_cache=False,
            notion_api_key="test_key",
            notion_database_id="test_db",
        )
        pipeline = DiscoveryPipeline(config)

        with patch.object(pipeline, '_warmup_suppression_cache', new_callable=AsyncMock) as mock_warmup:
            with patch('workflows.pipeline.SignalStore') as mock_store:
                mock_store_instance = AsyncMock()
                mock_store.return_value = mock_store_instance

                with patch('workflows.pipeline.NotionConnector'):
                    await pipeline.initialize()

                    # Warmup should NOT be called when disabled
                    mock_warmup.assert_not_called()

    @pytest.mark.asyncio
    async def test_warmup_is_non_fatal(self):
        """Pipeline should continue if warmup fails (non-fatal)"""
        config = PipelineConfig(
            warmup_suppression_cache=True,
            notion_api_key="test_key",
            notion_database_id="test_db",
        )
        pipeline = DiscoveryPipeline(config)

        # Make warmup raise an exception
        async def failing_warmup():
            raise Exception("Notion API unavailable")

        with patch.object(pipeline, '_warmup_suppression_cache', side_effect=failing_warmup):
            with patch('workflows.pipeline.SignalStore') as mock_store:
                mock_store_instance = AsyncMock()
                mock_store.return_value = mock_store_instance

                with patch('workflows.pipeline.NotionConnector'):
                    # Should NOT raise - warmup failure is non-fatal
                    await pipeline.initialize()

                    # Pipeline should still be initialized
                    assert pipeline._initialized is True


class TestWarmupMethod:
    """Test the _warmup_suppression_cache method itself"""

    @pytest.mark.asyncio
    async def test_warmup_method_exists(self):
        """DiscoveryPipeline should have _warmup_suppression_cache method"""
        config = PipelineConfig()
        pipeline = DiscoveryPipeline(config)

        assert hasattr(pipeline, '_warmup_suppression_cache')
        assert callable(pipeline._warmup_suppression_cache)

    @pytest.mark.asyncio
    async def test_warmup_calls_suppression_sync(self):
        """_warmup_suppression_cache should call SuppressionSync.sync"""
        config = PipelineConfig(
            notion_api_key="test_key",
            notion_database_id="test_db",
        )
        pipeline = DiscoveryPipeline(config)

        # Setup mock components
        pipeline._store = AsyncMock()
        pipeline._notion = MagicMock()

        with patch('workflows.pipeline.SuppressionSync') as mock_sync_class:
            mock_sync_instance = AsyncMock()
            mock_sync_instance.sync.return_value = MagicMock(entries_synced=100)
            mock_sync_class.return_value = mock_sync_instance

            await pipeline._warmup_suppression_cache()

            # Verify SuppressionSync was called
            mock_sync_class.assert_called_once()
            mock_sync_instance.sync.assert_called_once_with(dry_run=False)
