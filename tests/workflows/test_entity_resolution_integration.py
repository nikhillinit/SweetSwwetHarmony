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

    async def test_multi_asset_signals_consolidate_to_one_lead(self):
        """Multi-asset signals (GitHub + Product Hunt) consolidate to 1 lead"""
        from storage.signal_store import SignalStore, StoredSignal
        from datetime import datetime

        config = PipelineConfig(use_entities=True)
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Create two signals with different canonical keys
        github_signal = StoredSignal(
            id=1,
            source="github",
            source_type="github_repo",
            canonical_key="github_org:acme",
            company_name="Acme",
            description="Acme app on GitHub",
            entity_url="https://github.com/acme/app",
            signal_strength=0.7,
            status="pending",
            created_at=datetime.utcnow(),
            processed=False,
            extracted_data={},
        )

        ph_signal = StoredSignal(
            id=2,
            source="product_hunt",
            source_type="product_hunt",
            canonical_key="domain:acme.com",
            company_name="Acme",
            description="Acme product on Product Hunt",
            entity_url="https://producthunt.com/posts/acme",
            signal_strength=0.65,
            status="pending",
            created_at=datetime.utcnow(),
            processed=False,
            extracted_data={},
        )

        # Simulate signal grouping
        signals_by_key = {
            "github_org:acme": [github_signal],
            "domain:acme.com": [ph_signal],
        }

        # Create asset-to-lead link to resolve them to same domain
        link = AssetToLead(
            asset_id=1,
            asset_source_type="github_repo",
            asset_external_id="acme/app",
            lead_canonical_key="domain:acme.com",  # Both resolve to this
            confidence=0.9,
            resolved_by=ResolutionMethod.DOMAIN_MATCH,
        )
        await pipeline._entity_resolution_store.create_link(link)

        # Regroup signals by entity resolution
        regrouped = await pipeline._regroup_signals_by_entity(signals_by_key)

        # Should have consolidated to 1 group with key domain:acme.com
        assert len(regrouped) == 1, f"Expected 1 group, got {len(regrouped)}: {list(regrouped.keys())}"
        assert "domain:acme.com" in regrouped
        assert len(regrouped["domain:acme.com"]) == 2, f"Expected 2 signals in domain:acme.com, got {len(regrouped['domain:acme.com'])}"

        await pipeline.close()
