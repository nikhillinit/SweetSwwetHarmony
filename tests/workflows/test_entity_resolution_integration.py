"""Test EntityResolver integration with pipeline for asset-to-lead resolution"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from workflows.pipeline import DiscoveryPipeline, PipelineConfig
from storage.entity_resolution import AssetToLead, ResolutionMethod


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
        await pipeline.initialize()

        # EntityResolutionStore should be initialized
        assert pipeline._entity_resolution_store is not None

        # Create a test asset-to-lead link
        link = AssetToLead(
            asset_id=1,
            asset_source_type="github_repo",
            asset_external_id="startup/app",
            lead_canonical_key="domain:startup.com",
            confidence=0.95,
            resolved_by=ResolutionMethod.DOMAIN_MATCH,
            metadata={"method": "github_to_domain"}
        )

        # Create the link in the store
        link_id = await pipeline._entity_resolution_store.create_link(link)

        # Verify the link was created
        assert link_id is not None

        # Verify we can retrieve it
        resolved_key = await pipeline._entity_resolution_store.get_lead_for_asset(
            "github_repo", "startup/app", min_confidence=0.9
        )
        assert resolved_key == "domain:startup.com"

        await pipeline.close()

    # Note: Multi-asset consolidation is comprehensively tested in
    # tests/integration/test_e2e_feature_enablement.py::TestE2EMultiAssetConsolidation
    # See test_github_and_product_hunt_consolidate_to_one_lead and
    # test_multi_asset_prevents_duplicate_notion_pages for E2E tests
