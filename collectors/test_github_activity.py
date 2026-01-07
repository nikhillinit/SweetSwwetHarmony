"""
Unit tests for GitHub Activity Collector - TDD

Tests the GitHubActivitySignal and GitHubActivityCollector classes.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from collectors.github_activity import (
    GitHubActivitySignal,
    GitHubActivityCollector
)


class TestGitHubActivitySignal:
    """Test signal data class"""

    def test_age_calculation(self):
        """Signal age calculated from created_at"""
        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
            created_at=ten_days_ago,
        )
        assert 9 <= signal.age_days <= 11

    def test_age_zero_when_no_created_at(self):
        """Age is 0 when no created_at"""
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
        )
        assert signal.age_days == 0

    def test_score_new_repo_recent(self):
        """Recent new repo = high score"""
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
            website_url="https://acme.ai",
        )
        score = signal.calculate_signal_score()
        # base 0.6 + recency 0.1 + website 0.1 = 0.8
        assert 0.7 <= score <= 0.9

    def test_score_org_created(self):
        """Org created = highest base score"""
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="org_created",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        score = signal.calculate_signal_score()
        # base 0.8 + recency 0.1 = 0.9
        assert 0.85 <= score <= 0.95

    def test_score_commit_spike(self):
        """Commit spike = medium-high base score"""
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="commit_spike",
            commit_count_30d=100,
        )
        score = signal.calculate_signal_score()
        assert 0.6 <= score <= 0.8

    def test_score_with_stars(self):
        """Stars boost confidence score"""
        signal_no_stars = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
            stars=0,
        )
        signal_with_stars = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
            stars=150,
        )
        assert signal_with_stars.calculate_signal_score() > \
               signal_no_stars.calculate_signal_score()

    def test_canonical_key_from_website(self):
        """Canonical key derived from website domain"""
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
            website_url="https://www.acme.ai/about",
        )
        converted = signal.to_signal()
        assert converted.raw_data["canonical_key"] == "domain:acme.ai"

    def test_canonical_key_fallback_username(self):
        """Fallback to username if no website"""
        signal = GitHubActivitySignal(
            username="FounderName",
            signal_type="new_repo",
        )
        converted = signal.to_signal()
        assert converted.raw_data["canonical_key"] == "github_user:foundername"

    def test_to_signal_conversion(self):
        """Signal converts to verification gate Signal correctly"""
        signal = GitHubActivitySignal(
            username="founder",
            signal_type="new_repo",
            repo_name="awesome-project",
            repo_url="https://github.com/founder/awesome-project",
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
            description="An awesome project",
            language="Python",
            stars=50,
            forks=10,
        )

        converted = signal.to_signal()

        assert converted.signal_type == "github_activity"
        assert converted.source_api == "github"
        assert converted.verified_by_sources == ["github"]
        assert converted.raw_data["activity_type"] == "new_repo"
        assert converted.raw_data["repo_name"] == "awesome-project"
        assert converted.raw_data["language"] == "Python"


class TestGitHubActivityCollector:
    """Test collector (requires mock or network)"""

    @pytest.mark.asyncio
    async def test_collector_initialization(self):
        """Collector initializes without error"""
        async with GitHubActivityCollector() as collector:
            # After BaseCollector migration, we no longer use a persistent client
            # _http_get() creates clients per request
            assert collector is not None

    @pytest.mark.asyncio
    async def test_check_user_returns_signals(self):
        """Check user returns signal list (may be empty)"""
        async with GitHubActivityCollector(lookback_days=30) as collector:
            # Use a known active GitHub user
            signals = await collector.check_user("octocat")
            assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_run_returns_result_dict(self):
        """Run method returns proper result structure"""
        from discovery_engine.mcp_server import CollectorResult

        collector = GitHubActivityCollector(
            usernames=["octocat"],
            lookback_days=30
        )
        result = await collector.run(dry_run=True)

        # Result is a CollectorResult dataclass
        assert isinstance(result, CollectorResult)
        assert result.collector == "github_activity"
        assert hasattr(result, "signals_found")
        assert result.dry_run is True

        # Can be converted to dict
        result_dict = result.to_dict()
        assert isinstance(result_dict, dict)
        assert "status" in result_dict
        assert "signals_found" in result_dict

    @pytest.mark.asyncio
    async def test_run_with_invalid_user(self):
        """Run handles non-existent users gracefully"""
        from discovery_engine.mcp_server import CollectorResult

        collector = GitHubActivityCollector(
            usernames=["definitely-not-a-real-user-12345678"],
            lookback_days=30
        )
        result = await collector.run(dry_run=True)

        assert isinstance(result, CollectorResult)
        assert result.signals_found == 0

    @pytest.mark.asyncio
    async def test_check_org_returns_signals(self):
        """Check org returns signal list (may be empty)"""
        async with GitHubActivityCollector(lookback_days=90) as collector:
            # Use a known org
            signals = await collector.check_org("github")
            assert isinstance(signals, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
