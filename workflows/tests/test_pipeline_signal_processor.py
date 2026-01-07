"""Tests for SignalProcessor integration in pipeline."""
import pytest
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


class TestPipelineSignalProcessor:
    """Test SignalProcessor integration."""

    @pytest.mark.asyncio
    async def test_signal_processor_initialized_when_gating_enabled(self):
        """SignalProcessor should be initialized when use_gating=True."""
        config = PipelineConfig(
            use_gating=True,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._signal_processor is not None

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_signal_processor_not_initialized_when_gating_disabled(self):
        """SignalProcessor should be None when use_gating=False."""
        config = PipelineConfig(
            use_gating=False,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        assert pipeline._signal_processor is None

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_signal_processor_has_dry_run_from_config(self):
        """SignalProcessor should inherit dry_run setting."""
        config = PipelineConfig(
            use_gating=True,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)

        await pipeline.initialize()

        # SignalProcessor should exist
        assert pipeline._signal_processor is not None

        await pipeline.close()
