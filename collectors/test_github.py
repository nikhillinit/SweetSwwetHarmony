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
