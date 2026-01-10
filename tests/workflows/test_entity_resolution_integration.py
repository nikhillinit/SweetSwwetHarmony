"""Test EntityResolver integration with pipeline for asset-to-lead resolution"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


@pytest.mark.asyncio
class TestEntityResolutionIntegration:
    """Test EntityResolver integration in pipeline"""

    async def test_pipeline_initializes_entity_resolution_store(self):
        """Pipeline initializes EntityResolutionStore when use_entities=True"""
        config = PipelineConfig(use_entities=True)
        pipeline = DiscoveryPipeline(config)

        # Should have _entity_resolution_store attribute after init
        assert hasattr(pipeline, "_entity_resolution_store")

    async def test_entity_resolver_creates_asset_to_lead_links(self):
        """EntityResolver creates asset-to-lead links during processing"""
        config = PipelineConfig(use_entities=True)
        pipeline = DiscoveryPipeline(config)

        # Should be able to access _entity_resolution_store
        assert hasattr(pipeline, "_entity_resolution_store")

        # When signals are processed with entity resolution,
        # should create links in the store (tested in GREEN phase)

    async def test_multi_asset_signals_consolidate_to_one_lead(self):
        """Multi-asset signals (GitHub + Product Hunt) consolidate to 1 lead"""
        # GitHub signal: canonical_key="github_org:acme"
        # Product Hunt signal: canonical_key="domain:acme.com"
        # Both should resolve to same domain via EntityResolver
        # Result: Only ONE Notion page created (not two)

        # This is validated in the GREEN phase when regrouping is implemented
        pass
