"""Tests for EntityResolver integration in pipeline."""
import pytest
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


class TestPipelineEntityResolver:
    """Test EntityResolver integration."""

    @pytest.mark.asyncio
    async def test_entity_resolver_initialized_when_entities_enabled(self):
        """EntityResolver should be initialized when use_entities=True."""
        config = PipelineConfig(
            use_entities=True,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._entity_resolver is not None

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_entity_resolver_not_initialized_when_entities_disabled(self):
        """EntityResolver should be None when use_entities=False."""
        config = PipelineConfig(
            use_entities=False,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._entity_resolver is None

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_all_v2_components_initialized_together(self):
        """All v2 components should work together."""
        config = PipelineConfig(
            use_gating=True,
            use_entities=True,
            use_asset_store=True,
            asset_store_path=":memory:",
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._signal_processor is not None
        assert pipeline._entity_resolver is not None
        assert pipeline._asset_store is not None

        await pipeline.close()
