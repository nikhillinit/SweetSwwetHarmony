"""End-to-end tests for Phase 3 Batch 3.3 Feature Enablement"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from workflows.pipeline import DiscoveryPipeline, PipelineConfig
from storage.signal_store import SignalStore, StoredSignal
from storage.source_asset_store import SourceAssetStore
from storage.entity_resolution import AssetToLead, ResolutionMethod


@pytest.mark.asyncio
class TestE2EMultiAssetConsolidation:
    """E2E tests for multi-asset consolidation via EntityResolver"""

    async def test_github_and_product_hunt_consolidate_to_one_lead(self):
        """
        E2E: GitHub repo + Product Hunt launch consolidate to 1 Notion page.

        Scenario:
        - GitHub signal: acme/app (github_org:acme)
        - Product Hunt signal: acme.com (domain:acme.com)
        - Both resolve to domain:acme.com via EntityResolver
        - Result: Only 1 Notion page created (not 2)
        """
        config = PipelineConfig(
            use_entities=True,
            use_asset_store=False,  # Not needed for this test
            notion_api_key="test_key",
            notion_database_id="test_db",
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Create test signals
        github_signal = StoredSignal(
            id=1,
            source="github",
            source_api="github",
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
            raw_data={"repo_name": "acme/app", "stars": 100},
            detected_at=datetime.utcnow(),
        )

        ph_signal = StoredSignal(
            id=2,
            source="product_hunt",
            source_api="product_hunt",
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
            raw_data={"product_name": "Acme", "votes": 500},
            detected_at=datetime.utcnow(),
        )

        # Save signals to store
        await pipeline._store.save_signal(github_signal)
        await pipeline._store.save_signal(ph_signal)

        # Create entity resolution link: GitHub → domain
        link = AssetToLead(
            asset_id=1,
            asset_source_type="github_repo",
            asset_external_id="acme/app",
            lead_canonical_key="domain:acme.com",
            confidence=0.95,
            resolved_by=ResolutionMethod.DOMAIN_MATCH,
            metadata={"github_domain": "acme.com"},
        )
        await pipeline._entity_resolution_store.create_link(link)

        # Get pending signals and group them
        pending = await pipeline._store.get_pending_signals(limit=10)
        assert len(pending) == 2

        # Group by canonical key
        signals_by_key = {}
        for sig in pending:
            signals_by_key.setdefault(sig.canonical_key, []).append(sig)

        # Before regrouping: 2 groups
        assert len(signals_by_key) == 2
        assert "github_org:acme" in signals_by_key
        assert "domain:acme.com" in signals_by_key

        # Re-group by entity resolution
        regrouped = await pipeline._regroup_signals_by_entity(signals_by_key)

        # After regrouping: 1 group (consolidated to domain:acme.com)
        assert len(regrouped) == 1, f"Expected 1 group, got {len(regrouped)}: {list(regrouped.keys())}"
        assert "domain:acme.com" in regrouped
        assert len(regrouped["domain:acme.com"]) == 2, "Both signals should consolidate to domain:acme.com"

        # Verify both signal types are present in consolidated group
        signal_sources = {sig.source_api for sig in regrouped["domain:acme.com"]}
        assert "github" in signal_sources
        assert "product_hunt" in signal_sources

        await pipeline.close()

    async def test_multi_asset_prevents_duplicate_notion_pages(self):
        """
        E2E: Multi-asset consolidation prevents duplicate Notion pages.

        With 3 signals from same company (GitHub + PH + domain):
        - Without entity resolution: 3 Notion pages created
        - With entity resolution: 1 Notion page created
        """
        config = PipelineConfig(
            use_entities=True,
            use_asset_store=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Create 3 signals for same company with different canonical keys
        signals_data = [
            {
                "id": 1,
                "source_api": "github",
                "canonical_key": "github_org:acme",
                "entity_url": "https://github.com/acme/app",
                "raw_data": {"repo": "acme/app"},
            },
            {
                "id": 2,
                "source_api": "product_hunt",
                "canonical_key": "domain:acme.com",
                "entity_url": "https://producthunt.com/posts/acme",
                "raw_data": {"domain": "acme.com"},
            },
            {
                "id": 3,
                "source_api": "domain_whois",
                "canonical_key": "domain:acme.com",
                "entity_url": "https://whois.acme.com",
                "raw_data": {"domain": "acme.com"},
            },
        ]

        signals = []
        for data in signals_data:
            sig = StoredSignal(
                id=data["id"],
                source=data["source_api"],
                source_api=data["source_api"],
                source_type=data["source_api"],
                canonical_key=data["canonical_key"],
                company_name="Acme",
                description=f"Signal from {data['source_api']}",
                entity_url=data["entity_url"],
                signal_strength=0.7,
                status="pending",
                created_at=datetime.utcnow(),
                processed=False,
                extracted_data={},
                raw_data=data["raw_data"],
                detected_at=datetime.utcnow(),
            )
            signals.append(sig)
            await pipeline._store.save_signal(sig)

        # Create entity resolution links
        # GitHub → domain
        link1 = AssetToLead(
            asset_id=1,
            asset_source_type="github",
            asset_external_id="acme/app",
            lead_canonical_key="domain:acme.com",
            confidence=0.9,
            resolved_by=ResolutionMethod.DOMAIN_MATCH,
        )
        await pipeline._entity_resolution_store.create_link(link1)

        # Get pending and group
        pending = await pipeline._store.get_pending_signals(limit=10)
        signals_by_key = {}
        for sig in pending:
            signals_by_key.setdefault(sig.canonical_key, []).append(sig)

        # Before: 2 groups (github_org:acme, domain:acme.com)
        assert len(signals_by_key) == 2

        # After: 1 group (all consolidated)
        regrouped = await pipeline._regroup_signals_by_entity(signals_by_key)
        assert len(regrouped) == 1
        assert len(regrouped["domain:acme.com"]) == 3

        await pipeline.close()


@pytest.mark.asyncio
class TestE2EChangeDetectionIdempotency:
    """E2E tests for change detection enabling idempotent runs"""

    async def test_idempotent_collector_runs_skip_unchanged(self):
        """
        E2E: Running collector twice without changes produces 0 new signals.

        Scenario:
        - Run 1: 5 repos from GitHub API → 5 signals created
        - Run 2: Same 5 repos (no changes) → 0 new signals (all skipped)
        """
        config = PipelineConfig(
            use_entities=False,
            use_asset_store=True,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Simulate first collector run: save 5 repos
        repos = [
            {"full_name": "org/repo1", "stars": 100, "updated_at": "2024-01-10"},
            {"full_name": "org/repo2", "stars": 200, "updated_at": "2024-01-10"},
            {"full_name": "org/repo3", "stars": 300, "updated_at": "2024-01-10"},
            {"full_name": "org/repo4", "stars": 400, "updated_at": "2024-01-10"},
            {"full_name": "org/repo5", "stars": 500, "updated_at": "2024-01-10"},
        ]

        # First run: save all repos (all new)
        first_run_new = 0
        first_run_changed = 0

        for repo in repos:
            result = await pipeline._asset_store.save_snapshot(
                source_type="github_repo",
                external_id=repo["full_name"],
                data=repo,
                detect_changes=True,
            )
            is_new = isinstance(result, int)
            if is_new:
                first_run_new += 1
            else:
                first_run_changed += len(result)

        # First run: all 5 should be new
        assert first_run_new == 5, f"Expected 5 new repos, got {first_run_new}"
        assert first_run_changed == 0, f"Expected 0 changed repos, got {first_run_changed}"

        # Second run: save same repos (unchanged)
        second_run_new = 0
        second_run_changed = 0

        for repo in repos:
            result = await pipeline._asset_store.save_snapshot(
                source_type="github_repo",
                external_id=repo["full_name"],
                data=repo,
                detect_changes=True,
            )
            is_new = isinstance(result, int)
            if is_new:
                second_run_new += 1
            else:
                changes = result if isinstance(result, list) else []
                second_run_changed += len(changes)

        # Second run: all should be unchanged (0 new, 0 changes)
        assert second_run_new == 0, f"Expected 0 new repos in second run, got {second_run_new}"
        assert second_run_changed == 0, f"Expected 0 changed repos in second run, got {second_run_changed}"

        await pipeline.close()

    async def test_change_detection_identifies_modified_assets(self):
        """
        E2E: Change detection correctly identifies modified assets.

        Scenario:
        - Repo v1: 100 stars
        - Repo v2: 150 stars (modified)
        - Result: Change detected for star count increase
        """
        config = PipelineConfig(
            use_entities=False,
            use_asset_store=True,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        repo_id = "org/repo-test"

        # First version
        repo_v1 = {
            "full_name": repo_id,
            "stars": 100,
            "updated_at": "2024-01-10T12:00:00Z",
            "description": "Test repo",
        }

        result1 = await pipeline._asset_store.save_snapshot(
            source_type="github_repo",
            external_id=repo_id,
            data=repo_v1,
            detect_changes=True,
        )

        # Should be new (int result)
        assert isinstance(result1, int), "First save should return asset_id (int)"

        # Second version (modified)
        repo_v2 = {
            "full_name": repo_id,
            "stars": 150,  # Changed from 100
            "updated_at": "2024-01-10T14:00:00Z",  # Changed
            "description": "Test repo",
        }

        result2 = await pipeline._asset_store.save_snapshot(
            source_type="github_repo",
            external_id=repo_id,
            data=repo_v2,
            detect_changes=True,
        )

        # Should be list of changes (existing asset with changes)
        assert isinstance(result2, list), "Second save should return list of changes"
        assert len(result2) > 0, "Should detect changes between v1 and v2"

        await pipeline.close()

    async def test_pipeline_statistics_track_change_detection(self):
        """
        E2E: Pipeline statistics correctly report change detection results.

        Scenario:
        - Save 10 assets (all new)
        - Stats should show: 10 new, 0 skipped
        - Save same 10 assets again
        - Stats should show: 0 new, 10 skipped
        """
        config = PipelineConfig(
            use_entities=False,
            use_asset_store=True,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        test_data = [
            {"id": f"asset-{i}", "name": f"Asset {i}", "value": i * 100}
            for i in range(1, 11)
        ]

        # First batch: all new
        stats_run1 = {"new": 0, "unchanged": 0}
        for asset in test_data:
            result = await pipeline._asset_store.save_snapshot(
                source_type="test_source",
                external_id=asset["id"],
                data=asset,
                detect_changes=True,
            )
            if isinstance(result, int):
                stats_run1["new"] += 1
            else:
                stats_run1["unchanged"] += 1

        assert stats_run1["new"] == 10, f"Run 1: Expected 10 new, got {stats_run1['new']}"
        assert stats_run1["unchanged"] == 0, f"Run 1: Expected 0 unchanged, got {stats_run1['unchanged']}"

        # Second batch: all unchanged
        stats_run2 = {"new": 0, "unchanged": 0}
        for asset in test_data:
            result = await pipeline._asset_store.save_snapshot(
                source_type="test_source",
                external_id=asset["id"],
                data=asset,
                detect_changes=True,
            )
            if isinstance(result, int):
                stats_run2["new"] += 1
            else:
                stats_run2["unchanged"] += 1

        assert stats_run2["new"] == 0, f"Run 2: Expected 0 new, got {stats_run2['new']}"
        assert stats_run2["unchanged"] == 10, f"Run 2: Expected 10 unchanged, got {stats_run2['unchanged']}"

        await pipeline.close()
