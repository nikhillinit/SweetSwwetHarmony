"""Tests for SourceAssetStore - raw snapshot storage for two-entity model."""
import pytest
from datetime import datetime, timedelta

from storage.source_asset_store import SourceAssetStore, SourceAsset


class TestSourceAssetStore:
    """Test suite for SourceAssetStore."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve_asset(self):
        """Should save and retrieve source assets."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        asset = SourceAsset(
            source_type="github_repo",
            external_id="owner/repo",
            raw_payload={"description": "Test repo", "stars": 100},
            fetched_at=datetime.utcnow(),
        )

        asset_id = await store.save_asset(asset)

        retrieved = await store.get_asset(asset_id)
        assert retrieved is not None
        assert retrieved.source_type == "github_repo"
        assert retrieved.external_id == "owner/repo"
        assert retrieved.raw_payload["stars"] == 100

        await store.close()

    @pytest.mark.asyncio
    async def test_get_previous_snapshot(self):
        """Should retrieve previous snapshot for comparison."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        # Save two versions
        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo",
                raw_payload={"description": "Version 1"},
                fetched_at=datetime(2026, 1, 1),
            )
        )

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo",
                raw_payload={"description": "Version 2"},
                fetched_at=datetime(2026, 1, 2),
            )
        )

        # Get previous
        previous = await store.get_previous_snapshot("github_repo", "owner/repo")

        assert previous is not None
        assert previous["description"] == "Version 1"

        await store.close()

    @pytest.mark.asyncio
    async def test_get_latest_snapshot(self):
        """Should retrieve the most recent snapshot."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        # Save two versions
        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo",
                raw_payload={"description": "Old version"},
                fetched_at=datetime(2026, 1, 1),
            )
        )

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo",
                raw_payload={"description": "Latest version"},
                fetched_at=datetime(2026, 1, 2),
            )
        )

        latest = await store.get_latest_snapshot("github_repo", "owner/repo")

        assert latest is not None
        assert latest["description"] == "Latest version"

        await store.close()

    @pytest.mark.asyncio
    async def test_no_previous_for_single_asset(self):
        """Should return None for previous when only one snapshot exists."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo",
                raw_payload={"description": "Only version"},
                fetched_at=datetime.utcnow(),
            )
        )

        previous = await store.get_previous_snapshot("github_repo", "owner/repo")

        assert previous is None

        await store.close()

    @pytest.mark.asyncio
    async def test_assets_isolated_by_external_id(self):
        """Different external_ids should have separate snapshots."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo1",
                raw_payload={"description": "Repo 1"},
                fetched_at=datetime.utcnow(),
            )
        )

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo2",
                raw_payload={"description": "Repo 2"},
                fetched_at=datetime.utcnow(),
            )
        )

        latest1 = await store.get_latest_snapshot("github_repo", "owner/repo1")
        latest2 = await store.get_latest_snapshot("github_repo", "owner/repo2")

        assert latest1["description"] == "Repo 1"
        assert latest2["description"] == "Repo 2"

        await store.close()

    @pytest.mark.asyncio
    async def test_assets_isolated_by_source_type(self):
        """Different source_types should have separate snapshots."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="12345",
                raw_payload={"description": "GitHub asset"},
                fetched_at=datetime.utcnow(),
            )
        )

        await store.save_asset(
            SourceAsset(
                source_type="product_hunt",
                external_id="12345",
                raw_payload={"description": "Product Hunt asset"},
                fetched_at=datetime.utcnow(),
            )
        )

        github = await store.get_latest_snapshot("github_repo", "12345")
        ph = await store.get_latest_snapshot("product_hunt", "12345")

        assert github["description"] == "GitHub asset"
        assert ph["description"] == "Product Hunt asset"

        await store.close()

    @pytest.mark.asyncio
    async def test_change_detected_flag(self):
        """Should preserve change_detected flag."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        asset = SourceAsset(
            source_type="github_repo",
            external_id="owner/repo",
            raw_payload={"description": "Test"},
            fetched_at=datetime.utcnow(),
            change_detected=True,
        )

        asset_id = await store.save_asset(asset)
        retrieved = await store.get_asset(asset_id)

        assert retrieved.change_detected is True

        await store.close()

    @pytest.mark.asyncio
    async def test_get_assets_with_changes(self):
        """Should retrieve only assets with changes detected."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        # Save one with change, one without
        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo1",
                raw_payload={"description": "No change"},
                fetched_at=datetime.utcnow(),
                change_detected=False,
            )
        )

        await store.save_asset(
            SourceAsset(
                source_type="github_repo",
                external_id="owner/repo2",
                raw_payload={"description": "Changed!"},
                fetched_at=datetime.utcnow(),
                change_detected=True,
            )
        )

        changed = await store.get_assets_with_changes(limit=10)

        assert len(changed) == 1
        assert changed[0].external_id == "owner/repo2"

        await store.close()

    @pytest.mark.asyncio
    async def test_count_assets_by_type(self):
        """Should count assets by source type."""
        store = SourceAssetStore(":memory:")
        await store.initialize()

        for i in range(3):
            await store.save_asset(
                SourceAsset(
                    source_type="github_repo",
                    external_id=f"repo{i}",
                    raw_payload={},
                    fetched_at=datetime.utcnow(),
                )
            )

        for i in range(2):
            await store.save_asset(
                SourceAsset(
                    source_type="product_hunt",
                    external_id=f"ph{i}",
                    raw_payload={},
                    fetched_at=datetime.utcnow(),
                )
            )

        counts = await store.count_by_source_type()

        assert counts["github_repo"] == 3
        assert counts["product_hunt"] == 2

        await store.close()
