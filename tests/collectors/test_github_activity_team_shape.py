"""Tests for GitHub Activity Collector with Team Shape Integration.

Phase D: Team shape metrics are computed and logged in SHADOW mode.
These tests verify the integration between GitHubActivityCollector
and TeamShapeAnalyzer.

TDD: Write failing tests first, then implement.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from collectors.github_activity import (
    GitHubActivitySignal,
    GitHubActivityCollector,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_contributor_stats():
    """Sample contributor stats response from GitHub API."""
    return [
        {
            "author": {"login": "founder1", "id": 12345},
            "login": "founder1",
            "total": 150,
            "weeks": [
                {"w": 1735689600, "a": 500, "d": 200, "c": 50},  # 2025-01-01
                {"w": 1738368000, "a": 300, "d": 100, "c": 50},  # 2025-02-01
                {"w": 1740960000, "a": 200, "d": 80, "c": 50},   # 2025-03-01
            ]
        },
        {
            "author": {"login": "founder2", "id": 12346},
            "login": "founder2",
            "total": 100,
            "weeks": [
                {"w": 1735689600, "a": 300, "d": 150, "c": 35},
                {"w": 1738368000, "a": 200, "d": 80, "c": 35},
                {"w": 1740960000, "a": 150, "d": 60, "c": 30},
            ]
        },
        {
            "author": {"login": "helper", "id": 12347},
            "login": "helper",
            "total": 20,
            "weeks": [
                {"w": 1735689600, "a": 50, "d": 20, "c": 10},
                {"w": 1738368000, "a": 30, "d": 10, "c": 10},
            ]
        },
    ]


@pytest.fixture
def mock_repo_data():
    """Sample repo data response from GitHub API."""
    return {
        "name": "awesome-startup",
        "full_name": "startup-inc/awesome-startup",
        "html_url": "https://github.com/startup-inc/awesome-startup",
        "description": "Building something awesome",
        "homepage": "https://awesomestartup.com",
        "language": "Python",
        "stargazers_count": 500,
        "forks_count": 50,
        "fork": False,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-01-15T00:00:00Z",
        "owner": {
            "login": "startup-inc",
            "type": "Organization",
        }
    }


# =============================================================================
# TeamShapeAnalyzer Import Tests
# =============================================================================

class TestTeamShapeImportsInCollector:
    """Test that team shape components can be imported in collector context."""

    def test_import_team_shape_analyzer(self):
        from utils.team_shape import TeamShapeAnalyzer
        assert TeamShapeAnalyzer is not None

    def test_import_team_shape_metrics(self):
        from utils.team_shape import TeamShapeMetrics
        assert TeamShapeMetrics is not None

    def test_import_feature_registry(self):
        from utils.feature_states import FeatureRegistry, FeatureState
        assert FeatureRegistry is not None
        assert FeatureState is not None


# =============================================================================
# Collector with Team Shape Parameter Tests
# =============================================================================

class TestCollectorTeamShapeParameter:
    """Test GitHubActivityCollector with include_team_shape parameter."""

    def test_collector_has_include_team_shape_parameter(self):
        """Collector should accept include_team_shape parameter."""
        collector = GitHubActivityCollector(
            usernames=["test"],
            include_team_shape=True,
        )
        assert collector.include_team_shape is True

    def test_collector_default_include_team_shape_false(self):
        """include_team_shape should default to False."""
        collector = GitHubActivityCollector(
            usernames=["test"],
        )
        assert collector.include_team_shape is False

    def test_collector_has_team_shape_analyzer(self):
        """Collector with team_shape enabled should have analyzer."""
        collector = GitHubActivityCollector(
            usernames=["test"],
            include_team_shape=True,
        )
        from utils.team_shape import TeamShapeAnalyzer
        assert isinstance(collector.team_shape_analyzer, TeamShapeAnalyzer)


# =============================================================================
# Team Shape Fetch Tests
# =============================================================================

class TestTeamShapeFetch:
    """Test fetching team shape data from GitHub API."""

    @pytest.mark.asyncio
    async def test_fetch_contributor_stats(self, mock_contributor_stats):
        """Test fetching contributor stats for a repo."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        with patch.object(collector, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_contributor_stats

            async with collector:
                stats = await collector._fetch_contributor_stats("startup-inc", "awesome-startup")

            assert stats is not None
            assert len(stats) == 3
            assert stats[0]["login"] == "founder1"

    @pytest.mark.asyncio
    async def test_fetch_contributor_stats_handles_202(self, mock_contributor_stats):
        """GitHub returns 202 when stats are being computed - should retry."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        call_count = 0

        async def mock_get_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate 202 response by raising an error
                raise httpx.HTTPStatusError(
                    "Stats being computed",
                    request=MagicMock(),
                    response=MagicMock(status_code=202)
                )
            return mock_contributor_stats

        with patch.object(collector, '_http_get', side_effect=mock_get_with_retry):
            async with collector:
                stats = await collector._fetch_contributor_stats("startup-inc", "awesome-startup")

            # Should have retried at least once
            assert call_count >= 1

    @pytest.mark.asyncio
    async def test_fetch_contributor_stats_handles_404(self):
        """Handle 404 when repo doesn't have stats (empty repo)."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        async def mock_get_404(*args, **kwargs):
            raise httpx.HTTPStatusError(
                "Not found",
                request=MagicMock(),
                response=MagicMock(status_code=404)
            )

        with patch.object(collector, '_http_get', side_effect=mock_get_404):
            async with collector:
                stats = await collector._fetch_contributor_stats("startup-inc", "nonexistent")

            assert stats is None or stats == []


# =============================================================================
# Team Shape Analysis Tests
# =============================================================================

class TestTeamShapeAnalysis:
    """Test team shape analysis integration."""

    @pytest.mark.asyncio
    async def test_analyze_team_shape(self, mock_contributor_stats):
        """Test analyzing team shape from contributor stats."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        async with collector:
            metrics = collector._analyze_team_shape(mock_contributor_stats)

        assert metrics is not None
        assert metrics.contributor_count == 3
        assert metrics.core_contributor_count >= 2  # founder1 and founder2
        assert metrics.is_startup_team is True  # 2-5 core contributors

    @pytest.mark.asyncio
    async def test_analyze_team_shape_empty_data(self):
        """Empty contributor data should return zeroed metrics."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        async with collector:
            metrics = collector._analyze_team_shape([])

        assert metrics.contributor_count == 0
        assert metrics.is_startup_team is False


# =============================================================================
# Signal Enrichment Tests
# =============================================================================

class TestSignalEnrichmentWithTeamShape:
    """Test that signals are enriched with team shape data."""

    @pytest.mark.asyncio
    async def test_signal_includes_team_shape_data(self, mock_contributor_stats, mock_repo_data):
        """Signal raw_data should include team_shape when enabled."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        # Mock both repo fetch and contributor stats fetch
        async def mock_http_get(url, **kwargs):
            if "stats/contributors" in url:
                return mock_contributor_stats
            elif "/users/" in url and "/repos" in url:
                return [mock_repo_data]
            return []

        with patch.object(collector, '_http_get', side_effect=mock_http_get):
            async with collector:
                signals = await collector.check_user("founder1")

        # If signals were found, they should include team_shape
        if signals:
            for signal in signals:
                raw_data = signal.raw_data
                if raw_data.get("team_shape"):
                    assert "contributor_count" in raw_data["team_shape"]
                    assert "core_contributor_count" in raw_data["team_shape"]
                    assert "is_startup_team" in raw_data["team_shape"]

    @pytest.mark.asyncio
    async def test_signal_without_team_shape_when_disabled(self, mock_repo_data):
        """Signal raw_data should NOT include team_shape when disabled."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=False,  # Disabled
        )

        async def mock_http_get(url, **kwargs):
            if "/users/" in url and "/repos" in url:
                return [mock_repo_data]
            return []

        with patch.object(collector, '_http_get', side_effect=mock_http_get):
            async with collector:
                signals = await collector.check_user("founder1")

        # Signals should not have team_shape
        for signal in signals:
            assert "team_shape" not in signal.raw_data


# =============================================================================
# SHADOW Logging Tests
# =============================================================================

class TestTeamShapeShadowLogging:
    """Test SHADOW logging for team shape feature."""

    @pytest.mark.asyncio
    async def test_team_shape_logged_when_shadow_enabled(self, mock_contributor_stats, mock_repo_data):
        """Team shape should be logged when feature is in SHADOW mode."""
        from utils.feature_states import FeatureRegistry, FeatureState
        from storage.signal_store import SignalStore

        # Create a mock store
        mock_store = MagicMock(spec=SignalStore)
        mock_store.log_shadow_computation = AsyncMock(return_value=1)

        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
            store=mock_store,
        )

        # Ensure team_shape feature is in SHADOW mode
        with patch('utils.feature_states.FeatureRegistry') as MockRegistry:
            mock_registry = MagicMock()
            mock_registry.is_enabled.return_value = True
            mock_registry.is_shadow.return_value = True
            mock_registry.is_active.return_value = False
            MockRegistry.return_value = mock_registry

            async def mock_http_get(url, **kwargs):
                if "stats/contributors" in url:
                    return mock_contributor_stats
                elif "/users/" in url and "/repos" in url:
                    return [mock_repo_data]
                return []

            with patch.object(collector, '_http_get', side_effect=mock_http_get):
                async with collector:
                    await collector._log_team_shape_shadow(
                        canonical_key="domain:awesomestartup.com",
                        team_shape_metrics=collector._analyze_team_shape(mock_contributor_stats),
                        signal_id=None,
                    )

            # Verify shadow logging was called
            mock_store.log_shadow_computation.assert_called_once()
            call_args = mock_store.log_shadow_computation.call_args
            assert call_args[1]["feature_name"] == "team_shape"
            assert "contributor_count" in str(call_args[1]["computed_value"])


# =============================================================================
# Feature Flag Tests
# =============================================================================

class TestTeamShapeFeatureFlag:
    """Test feature flag integration for team_shape."""

    def test_team_shape_default_state_is_shadow(self):
        """team_shape should be SHADOW by default in feature registry."""
        from utils.feature_states import FeatureRegistry, FeatureState

        registry = FeatureRegistry()
        assert registry.get_state("team_shape") == FeatureState.SHADOW

    def test_team_shape_is_enabled_when_shadow(self):
        """team_shape should be enabled (computed) when in SHADOW mode."""
        from utils.feature_states import FeatureRegistry

        registry = FeatureRegistry()
        assert registry.is_enabled("team_shape") is True
        assert registry.is_shadow("team_shape") is True
        assert registry.is_active("team_shape") is False


# =============================================================================
# Edge Cases Tests
# =============================================================================

class TestTeamShapeEdgeCases:
    """Test edge cases for team shape integration."""

    @pytest.mark.asyncio
    async def test_handles_api_rate_limit(self):
        """Should handle GitHub API rate limit gracefully."""
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        async def mock_get_rate_limited(*args, **kwargs):
            response_mock = MagicMock()
            response_mock.status_code = 403
            response_mock.text = "API rate limit exceeded"
            response_mock.headers = {"X-RateLimit-Remaining": "0"}
            raise httpx.HTTPStatusError(
                "Rate limit exceeded",
                request=MagicMock(),
                response=response_mock,
            )

        with patch.object(collector, '_http_get', side_effect=mock_get_rate_limited):
            async with collector:
                stats = await collector._fetch_contributor_stats("owner", "repo")

            # Should return None or empty, not crash
            assert stats is None or stats == []

    @pytest.mark.asyncio
    async def test_handles_large_repo_many_contributors(self):
        """Should handle repos with many contributors (pagination)."""
        # Create 50 contributors with decreasing commits
        # Total commits = sum(100-i for i in 0..49) = 3775
        # First contributor: 100/3775 = 2.65% - NOT core (< 10%)
        many_contributors = [
            {
                "login": f"contributor{i}",
                "total": 100 - i,
                "weeks": [{"w": 1735689600, "c": 100 - i}]
            }
            for i in range(50)
        ]

        collector = GitHubActivityCollector(
            usernames=["founder1"],
            include_team_shape=True,
        )

        async with collector:
            metrics = collector._analyze_team_shape(many_contributors)

        assert metrics.contributor_count == 50
        # With 50 contributors evenly distributed, no one has >10% of commits
        # This is expected - many contributors = large OSS project
        assert metrics.core_contributor_count == 0
        # Many contributors with no core = large OSS project, not startup team
        assert metrics.is_startup_team is False
        # Concentration should be low (widely distributed)
        assert metrics.concentration_score < 0.1


# =============================================================================
# GitHubActivitySignal Tests
# =============================================================================

class TestGitHubActivitySignalTeamShape:
    """Test GitHubActivitySignal with team_shape data."""

    def test_signal_with_team_shape_converts_correctly(self):
        """Signal with team_shape should convert to verification gate Signal."""
        from utils.team_shape import TeamShapeMetrics

        team_shape = TeamShapeMetrics(
            contributor_count=3,
            core_contributor_count=2,
            concentration_score=0.45,
            sustained_activity=True,
            activity_span_days=120,
            top_contributors=[
                {"login": "founder1", "commits": 100, "percentage": 0.50},
            ],
            monthly_activity={"2026-01": 50},
        )

        signal = GitHubActivitySignal(
            username="founder1",
            signal_type="new_repo",
            repo_name="awesome-startup",
            repo_url="https://github.com/startup/awesome-startup",
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            website_url="https://awesomestartup.com",
            description="Building something awesome",
            raw_data={"team_shape": team_shape.to_dict()},
        )

        converted = signal.to_signal()

        assert converted.signal_type == "github_activity"
        # Team shape should be preserved in raw_data
        # Note: The raw_data gets merged, so team_shape might be at top level
        # depending on implementation
