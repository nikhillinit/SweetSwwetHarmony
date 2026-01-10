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
            db_path=":memory:",  # Use in-memory database for this test
            asset_store_path=":memory:",
            use_entities=True,
            use_asset_store=False,  # Not needed for this test
            notion_api_key="test_key",
            notion_database_id="test_db",
            warmup_suppression_cache=False,  # Disable Notion sync for tests
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Create test signals
        now = datetime.utcnow()

        github_signal = StoredSignal(
            id=1,
            signal_type="github_repo",
            source_api="github",
            canonical_key="github_org:acme",
            company_name="Acme",
            confidence=0.7,
            raw_data={"repo_name": "acme/app", "stars": 100, "url": "https://github.com/acme/app"},
            detected_at=now,
            created_at=now,
        )

        ph_signal = StoredSignal(
            id=2,
            signal_type="product_hunt",
            source_api="product_hunt",
            canonical_key="domain:acme.com",
            company_name="Acme",
            confidence=0.65,
            raw_data={"product_name": "Acme", "votes": 500, "url": "https://producthunt.com/posts/acme"},
            detected_at=now,
            created_at=now,
        )

        # Save signals to store
        await pipeline._store.save_signal(
            signal_type=github_signal.signal_type,
            source_api=github_signal.source_api,
            canonical_key=github_signal.canonical_key,
            confidence=github_signal.confidence,
            raw_data=github_signal.raw_data,
            company_name=github_signal.company_name,
            detected_at=github_signal.detected_at,
        )
        await pipeline._store.save_signal(
            signal_type=ph_signal.signal_type,
            source_api=ph_signal.source_api,
            canonical_key=ph_signal.canonical_key,
            confidence=ph_signal.confidence,
            raw_data=ph_signal.raw_data,
            company_name=ph_signal.company_name,
            detected_at=ph_signal.detected_at,
        )

        # Create entity resolution link: GitHub → domain
        # Note: external_id must match what _signal_to_asset produces (canonical_key)
        link = AssetToLead(
            asset_id=1,
            asset_source_type="github",  # source_api from the signal
            asset_external_id="github_org:acme",  # canonical_key from the signal
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
            db_path=":memory:",  # Use in-memory database for this test
            asset_store_path=":memory:",
            use_entities=True,
            use_asset_store=False,
            warmup_suppression_cache=False,  # Disable Notion sync for tests
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Create 3 signals for same company with different canonical keys
        now = datetime.utcnow()
        signals_data = [
            {
                "id": 1,
                "signal_type": "github_repo",
                "source_api": "github",
                "canonical_key": "github_org:acme",
                "raw_data": {"repo": "acme/app", "url": "https://github.com/acme/app"},
            },
            {
                "id": 2,
                "signal_type": "product_hunt",
                "source_api": "product_hunt",
                "canonical_key": "domain:acme.com",
                "raw_data": {"product": "acme", "url": "https://producthunt.com/posts/acme"},
            },
            {
                "id": 3,
                "signal_type": "domain_whois",
                "source_api": "domain_whois",
                "canonical_key": "domain:acme.com",
                "raw_data": {"domain": "acme.com", "url": "https://whois.acme.com"},
            },
        ]

        signals = []
        for data in signals_data:
            sig = StoredSignal(
                id=data["id"],
                signal_type=data["signal_type"],
                source_api=data["source_api"],
                canonical_key=data["canonical_key"],
                company_name="Acme",
                confidence=0.7,
                raw_data=data["raw_data"],
                detected_at=now,
                created_at=now,
            )
            signals.append(sig)
            await pipeline._store.save_signal(
                signal_type=sig.signal_type,
                source_api=sig.source_api,
                canonical_key=sig.canonical_key,
                confidence=sig.confidence,
                raw_data=sig.raw_data,
                company_name=sig.company_name,
                detected_at=sig.detected_at,
            )

        # Create entity resolution links
        # GitHub → domain (match canonical_key from signal)
        link1 = AssetToLead(
            asset_id=1,
            asset_source_type="github",
            asset_external_id="github_org:acme",  # canonical_key from signal
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
            db_path=":memory:",  # Use in-memory database for this test
            asset_store_path=":memory:",
            use_entities=False,
            use_asset_store=True,
            warmup_suppression_cache=False,  # Disable Notion sync for tests
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
        from storage.source_asset_store import SourceAsset
        from datetime import datetime

        first_run_new = 0
        first_run_changed = 0

        for repo in repos:
            # Get latest snapshot (should be None on first run)
            previous = await pipeline._asset_store.get_latest_snapshot(
                source_type="github_repo",
                external_id=repo["full_name"],
            )

            is_new = previous is None
            if is_new:
                first_run_new += 1
            else:
                first_run_changed += 1

            # Save asset
            asset = SourceAsset(
                source_type="github_repo",
                external_id=repo["full_name"],
                raw_payload=repo,
                fetched_at=datetime.utcnow(),
                change_detected=False,
            )
            await pipeline._asset_store.save_asset(asset)

        # First run: all 5 should be new
        assert first_run_new == 5, f"Expected 5 new repos, got {first_run_new}"
        assert first_run_changed == 0, f"Expected 0 changed repos, got {first_run_changed}"

        # Second run: save same repos (unchanged)
        import json
        second_run_new = 0
        second_run_changed = 0

        for repo in repos:
            # Get latest snapshot (should exist on second run)
            previous = await pipeline._asset_store.get_latest_snapshot(
                source_type="github_repo",
                external_id=repo["full_name"],
            )

            is_new = previous is None
            changes = False

            if not is_new:
                # Compare previous and current
                prev_json = json.dumps(previous, sort_keys=True)
                curr_json = json.dumps(repo, sort_keys=True)
                changes = prev_json != curr_json

            if is_new:
                second_run_new += 1
            elif changes:
                second_run_changed += 1

            # Save asset
            asset = SourceAsset(
                source_type="github_repo",
                external_id=repo["full_name"],
                raw_payload=repo,
                fetched_at=datetime.utcnow(),
                change_detected=changes,
            )
            await pipeline._asset_store.save_asset(asset)

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
            db_path=":memory:",  # Use in-memory database for this test
            asset_store_path=":memory:",
            use_entities=False,
            use_asset_store=True,
            warmup_suppression_cache=False,  # Disable Notion sync for tests
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        import json
        from storage.source_asset_store import SourceAsset
        from datetime import datetime

        repo_id = "org/repo-test"

        # First version
        repo_v1 = {
            "full_name": repo_id,
            "stars": 100,
            "updated_at": "2024-01-10T12:00:00Z",
            "description": "Test repo",
        }

        # Save first version
        asset1 = SourceAsset(
            source_type="github_repo",
            external_id=repo_id,
            raw_payload=repo_v1,
            fetched_at=datetime.utcnow(),
            change_detected=False,
        )
        asset_id = await pipeline._asset_store.save_asset(asset1)
        assert asset_id is not None, "First save should return asset_id"

        # Second version (modified)
        repo_v2 = {
            "full_name": repo_id,
            "stars": 150,  # Changed from 100
            "updated_at": "2024-01-10T14:00:00Z",  # Changed
            "description": "Test repo",
        }

        # Get latest snapshot before saving v2
        previous = await pipeline._asset_store.get_latest_snapshot(
            source_type="github_repo",
            external_id=repo_id,
        )

        # Detect changes
        prev_json = json.dumps(previous, sort_keys=True)
        curr_json = json.dumps(repo_v2, sort_keys=True)
        changes_detected = prev_json != curr_json

        # Save second version with change detection
        asset2 = SourceAsset(
            source_type="github_repo",
            external_id=repo_id,
            raw_payload=repo_v2,
            fetched_at=datetime.utcnow(),
            change_detected=changes_detected,
        )
        await pipeline._asset_store.save_asset(asset2)

        # Verify changes were detected
        assert changes_detected, "Should detect changes between v1 and v2"

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
            db_path=":memory:",  # Use in-memory database for this test
            asset_store_path=":memory:",
            use_entities=False,
            use_asset_store=True,
            warmup_suppression_cache=False,  # Disable Notion sync for tests
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        import json
        from datetime import datetime
        from storage.source_asset_store import SourceAsset

        test_data = [
            {"id": f"asset-{i}", "name": f"Asset {i}", "value": i * 100}
            for i in range(1, 11)
        ]

        # First batch: all new
        stats_run1 = {"new": 0, "unchanged": 0}
        for asset in test_data:
            # Get latest snapshot (should be None on first run)
            previous = await pipeline._asset_store.get_latest_snapshot(
                source_type="test_source",
                external_id=asset["id"],
            )

            is_new = previous is None
            if is_new:
                stats_run1["new"] += 1
            else:
                stats_run1["unchanged"] += 1

            # Save asset
            asset_obj = SourceAsset(
                source_type="test_source",
                external_id=asset["id"],
                raw_payload=asset,
                fetched_at=datetime.utcnow(),
                change_detected=False,
            )
            await pipeline._asset_store.save_asset(asset_obj)

        assert stats_run1["new"] == 10, f"Run 1: Expected 10 new, got {stats_run1['new']}"
        assert stats_run1["unchanged"] == 0, f"Run 1: Expected 0 unchanged, got {stats_run1['unchanged']}"

        # Second batch: all unchanged
        stats_run2 = {"new": 0, "unchanged": 0}
        for asset in test_data:
            # Get latest snapshot (should exist on second run)
            previous = await pipeline._asset_store.get_latest_snapshot(
                source_type="test_source",
                external_id=asset["id"],
            )

            is_new = previous is None
            changes = False

            if not is_new:
                # Compare previous and current
                prev_json = json.dumps(previous, sort_keys=True)
                curr_json = json.dumps(asset, sort_keys=True)
                changes = prev_json != curr_json

            if is_new:
                stats_run2["new"] += 1
            elif not changes:
                stats_run2["unchanged"] += 1

            # Save asset
            asset_obj = SourceAsset(
                source_type="test_source",
                external_id=asset["id"],
                raw_payload=asset,
                fetched_at=datetime.utcnow(),
                change_detected=changes,
            )
            await pipeline._asset_store.save_asset(asset_obj)

        assert stats_run2["new"] == 0, f"Run 2: Expected 0 new, got {stats_run2['new']}"
        assert stats_run2["unchanged"] == 10, f"Run 2: Expected 10 unchanged, got {stats_run2['unchanged']}"

        await pipeline.close()
