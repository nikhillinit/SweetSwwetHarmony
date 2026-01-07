"""
Tests for github_activity.py refactored to use BaseCollector.

TDD Phase: RED - These tests verify BaseCollector integration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestGitHubActivityInheritsBaseCollector:
    """Test GitHubActivityCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """GitHubActivityCollector should inherit from BaseCollector"""
        from collectors.github_activity import GitHubActivityCollector
        from collectors.base import BaseCollector

        assert issubclass(GitHubActivityCollector, BaseCollector)

    def test_has_api_name_for_rate_limiting(self):
        """GitHubActivityCollector should set api_name for rate limiting"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(usernames=["testuser"])

        # Should have api_name set for GitHub rate limiting
        assert collector.api_name == "github_activity"

    def test_has_retry_config(self):
        """GitHubActivityCollector should have retry_config from BaseCollector"""
        from collectors.github_activity import GitHubActivityCollector
        from collectors.retry_strategy import RetryConfig

        collector = GitHubActivityCollector(usernames=["testuser"])

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)


class TestGitHubActivityCollectSignalsMethod:
    """Test _collect_signals() implementation"""

    @pytest.mark.asyncio
    async def test_collect_signals_returns_list(self):
        """_collect_signals should return list of Signal objects"""
        from collectors.github_activity import GitHubActivityCollector
        from verification.verification_gate_v2 import Signal

        with patch.object(
            GitHubActivityCollector, 'check_user', new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = []

            collector = GitHubActivityCollector(usernames=["testuser"])
            signals = await collector._collect_signals()

            assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_run_returns_collector_result(self):
        """run() should return CollectorResult from BaseCollector"""
        from collectors.github_activity import GitHubActivityCollector
        from discovery_engine.mcp_server import CollectorResult

        with patch.object(
            GitHubActivityCollector, 'check_user', new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = []

            collector = GitHubActivityCollector(usernames=["testuser"])
            result = await collector.run(dry_run=True)

            assert isinstance(result, CollectorResult)
            assert hasattr(result, 'collector')
            assert hasattr(result, 'status')
            assert hasattr(result, 'signals_found')


class TestGitHubActivityUsesRetryLogic:
    """Test that API calls use retry logic"""

    @pytest.mark.asyncio
    async def test_uses_fetch_with_retry(self):
        """HTTP calls should use _fetch_with_retry for retries"""
        from collectors.github_activity import GitHubActivityCollector
        from collectors.retry_strategy import RetryConfig

        collector = GitHubActivityCollector(
            usernames=["testuser"],
            retry_config=RetryConfig(max_retries=3, backoff_base=0.01),
        )

        # Verify collector has _fetch_with_retry method from BaseCollector
        assert hasattr(collector, '_fetch_with_retry')
        assert callable(collector._fetch_with_retry)


class TestGitHubActivityUsesRateLimiter:
    """Test that rate limiting is applied"""

    @pytest.mark.asyncio
    async def test_has_rate_limiter(self):
        """GitHubActivityCollector should have rate limiter"""
        from collectors.github_activity import GitHubActivityCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = GitHubActivityCollector(usernames=["testuser"])

        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    @pytest.mark.asyncio
    async def test_rate_limiter_github_limits(self):
        """GitHub rate limit should be 5000/hour"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(usernames=["testuser"])

        # GitHub: 5000 requests per hour
        assert collector.rate_limiter.rate == 5000
        assert collector.rate_limiter.period == 3600


class TestGitHubActivityInitialization:
    """Test collector initialization"""

    def test_accepts_usernames_parameter(self):
        """GitHubActivityCollector should accept usernames in __init__"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(usernames=["user1", "user2"])

        assert hasattr(collector, 'usernames')
        assert collector.usernames == ["user1", "user2"]

    def test_accepts_org_names_parameter(self):
        """GitHubActivityCollector should accept org_names in __init__"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(org_names=["org1", "org2"])

        assert hasattr(collector, 'org_names')
        assert collector.org_names == ["org1", "org2"]

    def test_accepts_store_parameter(self):
        """GitHubActivityCollector should accept store from BaseCollector"""
        from collectors.github_activity import GitHubActivityCollector

        mock_store = MagicMock()
        collector = GitHubActivityCollector(
            usernames=["testuser"],
            store=mock_store,
        )

        assert collector.store is mock_store

    def test_collector_name_is_github_activity(self):
        """collector_name should be 'github_activity'"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(usernames=["testuser"])

        assert collector.collector_name == "github_activity"

    def test_accepts_github_token(self):
        """GitHubActivityCollector should accept github_token"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(
            usernames=["testuser"],
            github_token="ghp_test123",
        )

        assert collector.github_token == "ghp_test123"


class TestGitHubActivityBackwardsCompatibility:
    """Test backwards compatibility with existing usage"""

    @pytest.mark.asyncio
    async def test_check_user_still_works(self):
        """check_user() method should still be available"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(usernames=[])

        assert hasattr(collector, 'check_user')
        assert callable(collector.check_user)

    @pytest.mark.asyncio
    async def test_check_org_still_works(self):
        """check_org() method should still be available"""
        from collectors.github_activity import GitHubActivityCollector

        collector = GitHubActivityCollector(usernames=[])

        assert hasattr(collector, 'check_org')
        assert callable(collector.check_org)

    def test_activity_signal_dataclass_exists(self):
        """GitHubActivitySignal dataclass should still exist"""
        from collectors.github_activity import GitHubActivitySignal

        signal = GitHubActivitySignal(
            username="testuser",
            signal_type="new_repo",
            repo_name="test-repo",
        )

        assert signal.username == "testuser"
        assert signal.signal_type == "new_repo"

    def test_signal_score_calculation(self):
        """calculate_signal_score() should still work"""
        from collectors.github_activity import GitHubActivitySignal
        from datetime import datetime, timezone

        signal = GitHubActivitySignal(
            username="testuser",
            signal_type="commit_spike",
            website_url="https://example.com",
            stars=150,
            created_at=datetime.now(timezone.utc),  # Recent
        )

        score = signal.calculate_signal_score()

        # 0.7 (commit_spike) + 0.1 (recent) + 0.1 (website) + 0.1 (stars) = 1.0
        assert score == pytest.approx(1.0)
