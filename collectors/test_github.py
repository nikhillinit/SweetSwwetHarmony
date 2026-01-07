"""
Tests for GitHub collector (trending repos).

Basic coverage for BaseCollector integration and dataclasses.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone
import os


class TestGitHubCollectorBaseIntegration:
    """Test GitHubCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """GitHubCollector should inherit from BaseCollector"""
        from collectors.github import GitHubCollector
        from collectors.base import BaseCollector

        assert issubclass(GitHubCollector, BaseCollector)

    def test_has_collect_signals_method(self):
        """GitHubCollector should have _collect_signals method"""
        from collectors.github import GitHubCollector

        # GitHub collector requires a token
        collector = GitHubCollector(github_token="fake_token_for_test")

        assert hasattr(collector, '_collect_signals')
        assert callable(collector._collect_signals)

    def test_has_rate_limiter(self):
        """GitHubCollector should have rate limiter from BaseCollector"""
        from collectors.github import GitHubCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = GitHubCollector(github_token="fake_token_for_test")

        # BaseCollector provides rate_limiter property
        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    def test_has_retry_config(self):
        """GitHubCollector should have retry_config from BaseCollector"""
        from collectors.github import GitHubCollector
        from collectors.retry_strategy import RetryConfig

        collector = GitHubCollector(github_token="fake_token_for_test")

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)

    def test_accepts_store_parameter(self):
        """GitHubCollector should accept store parameter"""
        from collectors.github import GitHubCollector

        mock_store = MagicMock()
        collector = GitHubCollector(github_token="fake_token", store=mock_store)

        assert collector.store is mock_store

    def test_requires_github_token(self):
        """GitHubCollector should raise without token"""
        from collectors.github import GitHubCollector

        # Temporarily clear env var
        old_token = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with pytest.raises(ValueError, match="GitHub token required"):
                GitHubCollector()
        finally:
            if old_token:
                os.environ["GITHUB_TOKEN"] = old_token


class TestRepoMetrics:
    """Test RepoMetrics dataclass"""

    def test_repo_metrics_exists(self):
        """RepoMetrics dataclass should exist"""
        from collectors.github import RepoMetrics

        metrics = RepoMetrics(
            repo_full_name="org/repo",
            org="org",
            repo="repo",
            description="Test repo",
            stars=1000,
            forks=100,
            watchers=50,
            open_issues=10,
            language="Python",
            topics=["ai", "ml"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/org/repo",
            homepage=None,
        )

        assert metrics.repo_full_name == "org/repo"
        assert metrics.stars == 1000

    def test_is_relevant_property(self):
        """is_relevant should check topics against RELEVANT_TOPICS"""
        from collectors.github import RepoMetrics

        metrics = RepoMetrics(
            repo_full_name="org/repo",
            org="org",
            repo="repo",
            description="Test repo",
            stars=1000,
            forks=100,
            watchers=50,
            open_issues=10,
            language="Python",
            topics=["ai", "machine-learning"],  # Relevant topics
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/org/repo",
            homepage=None,
        )

        assert metrics.is_relevant is True

    def test_age_days_property(self):
        """age_days should calculate days since creation"""
        from collectors.github import RepoMetrics
        from datetime import timedelta

        metrics = RepoMetrics(
            repo_full_name="org/repo",
            org="org",
            repo="repo",
            description="Test repo",
            stars=1000,
            forks=100,
            watchers=50,
            open_issues=10,
            language="Python",
            topics=[],
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/org/repo",
            homepage=None,
        )

        assert metrics.age_days == 30


class TestRelevantTopics:
    """Test thesis-relevant topics configuration"""

    def test_relevant_topics_exist(self):
        """RELEVANT_TOPICS should be defined"""
        from collectors.github import RELEVANT_TOPICS

        assert isinstance(RELEVANT_TOPICS, set)
        assert "ai" in RELEVANT_TOPICS
        assert "machine-learning" in RELEVANT_TOPICS

    def test_min_stars_threshold(self):
        """MIN_STARS threshold should be defined"""
        from collectors.github import MIN_STARS

        assert MIN_STARS > 0


class TestConsumerTopics:
    """Test consumer-focused topic configuration for Press On Ventures thesis."""

    def test_consumer_topics_exist(self):
        """CONSUMER_TOPICS should be defined with consumer thesis categories."""
        from collectors.github import CONSUMER_TOPICS

        assert isinstance(CONSUMER_TOPICS, set)
        # Consumer CPG
        assert "food-delivery" in CONSUMER_TOPICS
        assert "grocery" in CONSUMER_TOPICS
        assert "beauty" in CONSUMER_TOPICS
        # Consumer Health Tech
        assert "fitness-app" in CONSUMER_TOPICS
        assert "wellness" in CONSUMER_TOPICS
        assert "mental-health" in CONSUMER_TOPICS
        # Travel & Hospitality
        assert "travel-booking" in CONSUMER_TOPICS
        assert "hospitality" in CONSUMER_TOPICS
        assert "restaurant" in CONSUMER_TOPICS
        # Consumer Marketplaces
        assert "marketplace" in CONSUMER_TOPICS
        assert "consumer" in CONSUMER_TOPICS
        assert "d2c" in CONSUMER_TOPICS

    def test_topic_mode_enum_exists(self):
        """TopicMode enum should exist with TECH and CONSUMER values."""
        from collectors.github import TopicMode

        assert hasattr(TopicMode, "TECH")
        assert hasattr(TopicMode, "CONSUMER")

    def test_collector_accepts_topic_mode_parameter(self):
        """GitHubCollector should accept topic_mode parameter."""
        from collectors.github import GitHubCollector, TopicMode

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        assert collector.topic_mode == TopicMode.CONSUMER

    def test_collector_defaults_to_tech_mode(self):
        """GitHubCollector should default to TECH topic mode."""
        from collectors.github import GitHubCollector, TopicMode

        collector = GitHubCollector(github_token="fake_token")

        assert collector.topic_mode == TopicMode.TECH

    def test_consumer_mode_uses_consumer_topics(self):
        """Consumer mode should use CONSUMER_TOPICS for relevance check."""
        from collectors.github import GitHubCollector, TopicMode, RepoMetrics
        from datetime import datetime, timezone

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        # Create a repo with consumer topics (not AI topics)
        metrics = RepoMetrics(
            repo_full_name="startup/fitness-tracker",
            org="startup",
            repo="fitness-tracker",
            description="Fitness tracking app",
            stars=500,
            forks=50,
            watchers=30,
            open_issues=5,
            language="TypeScript",
            topics=["fitness-app", "wellness", "health"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/startup/fitness-tracker",
            homepage="https://fitnessapp.io",
        )

        # Should be relevant in consumer mode
        assert collector.is_topic_relevant(metrics) is True

    def test_consumer_mode_rejects_tech_only_topics(self):
        """Consumer mode should reject repos with only tech topics."""
        from collectors.github import GitHubCollector, TopicMode, RepoMetrics
        from datetime import datetime, timezone

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        # Create a repo with only AI/tech topics
        metrics = RepoMetrics(
            repo_full_name="techcorp/llm-framework",
            org="techcorp",
            repo="llm-framework",
            description="LLM inference framework",
            stars=1000,
            forks=100,
            watchers=50,
            open_issues=10,
            language="Python",
            topics=["ai", "llm", "machine-learning"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/techcorp/llm-framework",
            homepage=None,
        )

        # Should NOT be relevant in consumer mode
        assert collector.is_topic_relevant(metrics) is False


class TestConsumerThesisFit:
    """Test thesis fit assessment for consumer categories."""

    def test_assess_consumer_cpg_fit(self):
        """Should identify Consumer CPG category."""
        from collectors.github import GitHubCollector, TopicMode, RepoMetrics
        from datetime import datetime, timezone

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        metrics = RepoMetrics(
            repo_full_name="foodco/meal-kit-app",
            org="foodco",
            repo="meal-kit-app",
            description="Meal kit delivery platform",
            stars=500,
            forks=50,
            watchers=30,
            open_issues=5,
            language="Python",
            topics=["meal-kit", "food-delivery", "subscription-box"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/foodco/meal-kit-app",
            homepage=None,
        )

        thesis_fit = collector._assess_thesis_fit(metrics)
        assert thesis_fit == "Consumer CPG"

    def test_assess_consumer_health_tech_fit(self):
        """Should identify Consumer Health Tech category."""
        from collectors.github import GitHubCollector, TopicMode, RepoMetrics
        from datetime import datetime, timezone

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        metrics = RepoMetrics(
            repo_full_name="healthapp/mental-health-tracker",
            org="healthapp",
            repo="mental-health-tracker",
            description="Mental health and wellness app",
            stars=500,
            forks=50,
            watchers=30,
            open_issues=5,
            language="React Native",
            topics=["mental-health", "wellness", "fitness-app"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/healthapp/mental-health-tracker",
            homepage=None,
        )

        thesis_fit = collector._assess_thesis_fit(metrics)
        assert thesis_fit == "Consumer Health Tech"

    def test_assess_travel_hospitality_fit(self):
        """Should identify Travel & Hospitality category."""
        from collectors.github import GitHubCollector, TopicMode, RepoMetrics
        from datetime import datetime, timezone

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        metrics = RepoMetrics(
            repo_full_name="travelco/booking-platform",
            org="travelco",
            repo="booking-platform",
            description="Travel booking and experiences platform",
            stars=500,
            forks=50,
            watchers=30,
            open_issues=5,
            language="TypeScript",
            topics=["travel-booking", "hospitality", "experiences"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/travelco/booking-platform",
            homepage=None,
        )

        thesis_fit = collector._assess_thesis_fit(metrics)
        assert thesis_fit == "Travel & Hospitality"

    def test_assess_consumer_marketplace_fit(self):
        """Should identify Consumer Marketplaces category."""
        from collectors.github import GitHubCollector, TopicMode, RepoMetrics
        from datetime import datetime, timezone

        collector = GitHubCollector(
            github_token="fake_token",
            topic_mode=TopicMode.CONSUMER
        )

        metrics = RepoMetrics(
            repo_full_name="marketco/consumer-marketplace",
            org="marketco",
            repo="consumer-marketplace",
            description="Two-sided consumer marketplace",
            stars=500,
            forks=50,
            watchers=30,
            open_issues=5,
            language="Python",
            topics=["marketplace", "consumer", "d2c"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pushed_at=datetime.now(timezone.utc),
            html_url="https://github.com/marketco/consumer-marketplace",
            homepage=None,
        )

        thesis_fit = collector._assess_thesis_fit(metrics)
        assert thesis_fit == "Consumer Marketplaces"


class TestDeltaComputation:
    """Test daily delta computation for idempotent runs."""

    @pytest.mark.asyncio
    async def test_compute_delta_detects_new_repos(self):
        """Should detect new repos not in previous snapshot."""
        from collectors.github import GitHubCollector
        from storage.source_asset_store import SourceAssetStore

        # Create in-memory store
        store = SourceAssetStore(":memory:")
        await store.initialize()

        collector = GitHubCollector(github_token="fake_token")

        # Current repos (new)
        current_repos = [
            {"full_name": "new/repo1", "id": 1, "stargazers_count": 500},
            {"full_name": "new/repo2", "id": 2, "stargazers_count": 300},
        ]

        # Compute delta against empty store
        delta = await collector.compute_delta(current_repos, store)

        # Both repos should be in delta (all new)
        assert len(delta["new"]) == 2
        assert len(delta["changed"]) == 0
        assert len(delta["unchanged"]) == 0

        await store.close()

    @pytest.mark.asyncio
    async def test_compute_delta_detects_star_changes(self):
        """Should detect repos with significant star changes."""
        from collectors.github import GitHubCollector
        from storage.source_asset_store import SourceAssetStore, SourceAsset
        from datetime import datetime

        store = SourceAssetStore(":memory:")
        await store.initialize()

        collector = GitHubCollector(github_token="fake_token")

        # Save previous snapshot
        await store.save_asset(SourceAsset(
            source_type="github_repo",
            external_id="org/repo1",
            raw_payload={"full_name": "org/repo1", "stargazers_count": 100},
            fetched_at=datetime.utcnow(),
        ))

        # Current repos with significant star increase
        current_repos = [
            {"full_name": "org/repo1", "id": 1, "stargazers_count": 200},  # +100%
        ]

        delta = await collector.compute_delta(current_repos, store)

        assert len(delta["new"]) == 0
        assert len(delta["changed"]) == 1
        assert delta["changed"][0]["full_name"] == "org/repo1"

        await store.close()

    @pytest.mark.asyncio
    async def test_compute_delta_ignores_minor_changes(self):
        """Should ignore repos with insignificant changes."""
        from collectors.github import GitHubCollector
        from storage.source_asset_store import SourceAssetStore, SourceAsset
        from datetime import datetime

        store = SourceAssetStore(":memory:")
        await store.initialize()

        collector = GitHubCollector(github_token="fake_token")

        # Save previous snapshot
        await store.save_asset(SourceAsset(
            source_type="github_repo",
            external_id="org/repo1",
            raw_payload={"full_name": "org/repo1", "stargazers_count": 100},
            fetched_at=datetime.utcnow(),
        ))

        # Current repos with minor star increase (< 10%)
        current_repos = [
            {"full_name": "org/repo1", "id": 1, "stargazers_count": 105},  # +5%
        ]

        delta = await collector.compute_delta(current_repos, store)

        assert len(delta["new"]) == 0
        assert len(delta["changed"]) == 0
        assert len(delta["unchanged"]) == 1

        await store.close()

    @pytest.mark.asyncio
    async def test_compute_delta_saves_current_snapshot(self):
        """Should save current snapshot after computing delta."""
        from collectors.github import GitHubCollector
        from storage.source_asset_store import SourceAssetStore
        from datetime import datetime

        store = SourceAssetStore(":memory:")
        await store.initialize()

        collector = GitHubCollector(github_token="fake_token")

        current_repos = [
            {"full_name": "new/repo1", "id": 1, "stargazers_count": 500},
        ]

        # Compute delta (should save snapshot)
        await collector.compute_delta(current_repos, store)

        # Verify snapshot was saved
        snapshot = await store.get_latest_snapshot("github_repo", "new/repo1")
        assert snapshot is not None
        assert snapshot["full_name"] == "new/repo1"
        assert snapshot["stargazers_count"] == 500

        await store.close()

    @pytest.mark.asyncio
    async def test_compute_delta_with_threshold_config(self):
        """Should respect star_change_threshold configuration."""
        from collectors.github import GitHubCollector
        from storage.source_asset_store import SourceAssetStore, SourceAsset
        from datetime import datetime

        store = SourceAssetStore(":memory:")
        await store.initialize()

        # Set higher threshold (25%)
        collector = GitHubCollector(
            github_token="fake_token",
            star_change_threshold=0.25
        )

        # Save previous snapshot
        await store.save_asset(SourceAsset(
            source_type="github_repo",
            external_id="org/repo1",
            raw_payload={"full_name": "org/repo1", "stargazers_count": 100},
            fetched_at=datetime.utcnow(),
        ))

        # 20% change should be ignored with 25% threshold
        current_repos = [
            {"full_name": "org/repo1", "id": 1, "stargazers_count": 120},
        ]

        delta = await collector.compute_delta(current_repos, store)

        assert len(delta["changed"]) == 0
        assert len(delta["unchanged"]) == 1

        await store.close()
