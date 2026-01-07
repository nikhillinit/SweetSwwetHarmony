"""Tests for EntityResolutionStore - Asset to Lead mapping."""
import pytest
from datetime import datetime

from storage.entity_resolution import (
    EntityResolutionStore,
    AssetToLead,
    ResolutionMethod,
)


class TestEntityResolutionStore:
    """Test suite for EntityResolutionStore."""

    @pytest.mark.asyncio
    async def test_link_asset_to_lead(self):
        """Should create a link between asset and lead."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        link = AssetToLead(
            asset_id=1,
            asset_source_type="github_repo",
            asset_external_id="startup/app",
            lead_canonical_key="domain:startup.com",
            confidence=0.95,
            resolved_by=ResolutionMethod.DOMAIN_MATCH,
        )

        link_id = await store.create_link(link)

        retrieved = await store.get_link(link_id)
        assert retrieved is not None
        assert retrieved.asset_id == 1
        assert retrieved.lead_canonical_key == "domain:startup.com"
        assert retrieved.resolved_by == ResolutionMethod.DOMAIN_MATCH

        await store.close()

    @pytest.mark.asyncio
    async def test_get_lead_for_asset(self):
        """Should retrieve lead canonical key for an asset."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:startup.com",
                confidence=0.9,
                resolved_by=ResolutionMethod.DOMAIN_MATCH,
            )
        )

        lead_key = await store.get_lead_for_asset(
            source_type="github_repo",
            external_id="startup/app",
        )

        assert lead_key == "domain:startup.com"

        await store.close()

    @pytest.mark.asyncio
    async def test_get_assets_for_lead(self):
        """Should retrieve all assets linked to a lead."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        # Link multiple assets to same lead
        await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:startup.com",
                confidence=0.9,
                resolved_by=ResolutionMethod.DOMAIN_MATCH,
            )
        )

        await store.create_link(
            AssetToLead(
                asset_id=2,
                asset_source_type="product_hunt",
                asset_external_id="ph_12345",
                lead_canonical_key="domain:startup.com",
                confidence=0.85,
                resolved_by=ResolutionMethod.DOMAIN_MATCH,
            )
        )

        assets = await store.get_assets_for_lead("domain:startup.com")

        assert len(assets) == 2
        assert {a.asset_source_type for a in assets} == {"github_repo", "product_hunt"}

        await store.close()

    @pytest.mark.asyncio
    async def test_manual_override_takes_precedence(self):
        """Manual override should replace automatic resolution."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        # Create automatic link
        await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:wrong.com",
                confidence=0.7,
                resolved_by=ResolutionMethod.HEURISTIC,
            )
        )

        # Override with manual link
        await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:correct.com",
                confidence=1.0,
                resolved_by=ResolutionMethod.MANUAL,
            )
        )

        lead_key = await store.get_lead_for_asset(
            source_type="github_repo",
            external_id="startup/app",
        )

        # Manual override should win
        assert lead_key == "domain:correct.com"

        await store.close()

    @pytest.mark.asyncio
    async def test_unresolved_asset_returns_none(self):
        """Unlinked asset should return None."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        lead_key = await store.get_lead_for_asset(
            source_type="github_repo",
            external_id="unknown/repo",
        )

        assert lead_key is None

        await store.close()

    @pytest.mark.asyncio
    async def test_get_unresolved_assets(self):
        """Should list assets without lead links."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        # Add a resolved asset
        await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="resolved/repo",
                lead_canonical_key="domain:resolved.com",
                confidence=0.9,
                resolved_by=ResolutionMethod.DOMAIN_MATCH,
            )
        )

        # Query for unresolved (should not include the one we just linked)
        # This requires knowing which assets exist - we'll track asset registrations
        await store.register_asset(
            asset_id=1,
            source_type="github_repo",
            external_id="resolved/repo",
        )
        await store.register_asset(
            asset_id=2,
            source_type="github_repo",
            external_id="unresolved/repo",
        )

        unresolved = await store.get_unresolved_assets(limit=10)

        assert len(unresolved) == 1
        assert unresolved[0]["external_id"] == "unresolved/repo"

        await store.close()

    @pytest.mark.asyncio
    async def test_resolution_confidence_threshold(self):
        """Should filter links by confidence threshold."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        # Low confidence link
        await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:maybe.com",
                confidence=0.5,
                resolved_by=ResolutionMethod.HEURISTIC,
            )
        )

        # With high threshold, should return None
        lead_key = await store.get_lead_for_asset(
            source_type="github_repo",
            external_id="startup/app",
            min_confidence=0.7,
        )

        assert lead_key is None

        # With lower threshold, should return the link
        lead_key = await store.get_lead_for_asset(
            source_type="github_repo",
            external_id="startup/app",
            min_confidence=0.4,
        )

        assert lead_key == "domain:maybe.com"

        await store.close()

    @pytest.mark.asyncio
    async def test_count_links_by_method(self):
        """Should count links by resolution method."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        for i in range(3):
            await store.create_link(
                AssetToLead(
                    asset_id=i,
                    asset_source_type="github_repo",
                    asset_external_id=f"repo{i}",
                    lead_canonical_key=f"domain:company{i}.com",
                    confidence=0.9,
                    resolved_by=ResolutionMethod.DOMAIN_MATCH,
                )
            )

        for i in range(2):
            await store.create_link(
                AssetToLead(
                    asset_id=10 + i,
                    asset_source_type="github_repo",
                    asset_external_id=f"repo1{i}",
                    lead_canonical_key=f"domain:other{i}.com",
                    confidence=1.0,
                    resolved_by=ResolutionMethod.MANUAL,
                )
            )

        counts = await store.count_by_resolution_method()

        assert counts[ResolutionMethod.DOMAIN_MATCH] == 3
        assert counts[ResolutionMethod.MANUAL] == 2

        await store.close()

    @pytest.mark.asyncio
    async def test_delete_link(self):
        """Should delete a link."""
        store = EntityResolutionStore(":memory:")
        await store.initialize()

        link_id = await store.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:startup.com",
                confidence=0.9,
                resolved_by=ResolutionMethod.DOMAIN_MATCH,
            )
        )

        await store.delete_link(link_id)

        retrieved = await store.get_link(link_id)
        assert retrieved is None

        await store.close()
