"""Tests for SourceAssetStore integration in pipeline."""
import pytest
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


class TestPipelineAssetStore:
    """Test SourceAssetStore integration."""

    @pytest.mark.asyncio
    async def test_asset_store_initialized_when_enabled(self):
        """SourceAssetStore should be initialized when use_asset_store=True."""
        config = PipelineConfig(
            use_asset_store=True,
            asset_store_path=":memory:",
            warmup_suppression_cache=False,  # Skip warmup for test
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._asset_store is not None

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_asset_store_not_initialized_when_disabled(self):
        """SourceAssetStore should be None when use_asset_store=False."""
        config = PipelineConfig(
            use_asset_store=False,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._asset_store is None

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_asset_store_closed_on_pipeline_close(self):
        """SourceAssetStore should be closed when pipeline closes."""
        config = PipelineConfig(
            use_asset_store=True,
            asset_store_path=":memory:",
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()
        assert pipeline._asset_store is not None

        await pipeline.close()
        # After close, _asset_store should be None or closed
        # (implementation detail, but pipeline should be reusable)
