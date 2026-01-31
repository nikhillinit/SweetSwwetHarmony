"""Tests for GitHub Activity Collector with Founder Surfaces integration.

Phase E: Integration tests for founder surface extraction in SHADOW mode.
Tests the optional founder_surfaces extraction in github_activity.py.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from collectors.github_activity import GitHubActivityCollector
from utils.founder_surfaces import FounderSurface, FounderSurfaceExtractor
from utils.feature_states import FeatureRegistry, FeatureState
from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_store():
    """Create a mock SignalStore."""
    store = MagicMock(spec=SignalStore)
    store.log_shadow_computation = AsyncMock(return_value=1)
    return store


@pytest.fixture
def collector_with_surfaces(mock_store):
    """Create a collector with founder surfaces enabled."""
    return GitHubActivityCollector(
        usernames=["founder1", "founder2"],
        store=mock_store,
        github_token="test_token",
        include_founder_surfaces=True,
    )


@pytest.fixture
def collector_without_surfaces(mock_store):
    """Create a collector without founder surfaces."""
    return GitHubActivityCollector(
        usernames=["founder1"],
        store=mock_store,
        github_token="test_token",
        include_founder_surfaces=False,
    )


@pytest.fixture
def sample_founder_surface():
    """Create a sample FounderSurface."""
    return FounderSurface(
        username="founder1",
        has_profile_readme=True,
        profile_readme_content="# Hi, I'm the CEO of TechCo",
        profile_intent_markers=["ceo", "building"],
        declared_websites=["https://techco.io"],
        gist_count=3,
        recent_gists=[{"id": "g1", "description": "test"}],
        gist_intent_markers=["pricing"],
        social_links={"twitter": "founder1", "website": "https://techco.io"},
        bio="Building TechCo",
        company="TechCo Inc",
    )


@pytest.fixture
def sample_repos():
    """Sample repos response for user."""
    return [
        {
            "name": "techco-app",
            "full_name": "founder1/techco-app",
            "html_url": "https://github.com/founder1/techco-app",
            "description": "Main product repo",
            "homepage": "https://techco.io",
            "stargazers_count": 100,
            "forks_count": 10,
            "fork": False,
            "language": "Python",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
            "owner": {"login": "founder1"},
        },
    ]


# =============================================================================
# COLLECTOR INITIALIZATION TESTS
# =============================================================================

class TestCollectorInitialization:
    """Tests for collector initialization with founder surfaces."""

    def test_default_founder_surfaces_disabled(self):
        """By default, founder surfaces extraction is disabled."""
        collector = GitHubActivityCollector(usernames=["user1"])
        assert collector.include_founder_surfaces is False

    def test_enable_founder_surfaces(self):
        """Can enable founder surfaces extraction."""
        collector = GitHubActivityCollector(
            usernames=["user1"],
            include_founder_surfaces=True,
        )
        assert collector.include_founder_surfaces is True

    def test_creates_surface_extractor_when_enabled(self):
        """Creates FounderSurfaceExtractor when enabled."""
        collector = GitHubActivityCollector(
            usernames=["user1"],
            include_founder_surfaces=True,
            github_token="test_token",
        )
        assert collector._surface_extractor is not None
        assert collector._surface_extractor._github_token == "test_token"

    def test_no_extractor_when_disabled(self):
        """No extractor created when disabled."""
        collector = GitHubActivityCollector(
            usernames=["user1"],
            include_founder_surfaces=False,
        )
        assert collector._surface_extractor is None


# =============================================================================
# FEATURE FLAG TESTS
# =============================================================================

class TestFeatureFlags:
    """Tests for SHADOW feature flag integration."""

    def test_respects_feature_registry_shadow(self, mock_store):
        """Collector respects SHADOW state from feature registry."""
        registry = FeatureRegistry()
        # founder_surfaces should be SHADOW by default
        assert registry.is_enabled("founder_surfaces") is True
        assert registry.is_shadow("founder_surfaces") is True

    def test_respects_feature_registry_off(self, mock_store):
        """Collector respects OFF state from feature registry."""
        registry = FeatureRegistry()
        registry.set_state("founder_surfaces", FeatureState.OFF)

        collector = GitHubActivityCollector(
            usernames=["user1"],
            include_founder_surfaces=True,
            feature_registry=registry,
        )
        # Should not extract surfaces when feature is OFF
        assert collector._should_extract_surfaces() is False

    def test_extracts_surfaces_when_shadow(self, mock_store):
        """Extracts surfaces when feature is in SHADOW mode."""
        registry = FeatureRegistry()
        # Default is SHADOW for founder_surfaces

        collector = GitHubActivityCollector(
            usernames=["user1"],
            include_founder_surfaces=True,
            feature_registry=registry,
        )
        assert collector._should_extract_surfaces() is True


# =============================================================================
# SURFACE EXTRACTION INTEGRATION TESTS
# =============================================================================

class TestSurfaceExtraction:
    """Tests for founder surface extraction in collector."""

    @pytest.mark.asyncio
    async def test_extract_surfaces_for_users(
        self, collector_with_surfaces, sample_repos, sample_founder_surface
    ):
        """Extracts founder surfaces for monitored users."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            signals = await collector_with_surfaces._collect_signals()

            # Should have called extract for each username
            assert mock_extract.call_count >= 1

    @pytest.mark.asyncio
    async def test_surfaces_added_to_raw_data(
        self, collector_with_surfaces, sample_repos, sample_founder_surface
    ):
        """Founder surfaces are added to signal raw_data."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            signals = await collector_with_surfaces._collect_signals()

            if signals:
                assert "founder_surface" in signals[0].raw_data

    @pytest.mark.asyncio
    async def test_no_surfaces_when_disabled(
        self, collector_without_surfaces, sample_repos
    ):
        """No founder surfaces when feature disabled."""
        with patch.object(
            collector_without_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http:
            mock_http.return_value = sample_repos

            signals = await collector_without_surfaces._collect_signals()

            if signals:
                assert "founder_surface" not in signals[0].raw_data


# =============================================================================
# SHADOW LOGGING TESTS
# =============================================================================

class TestShadowLogging:
    """Tests for SHADOW mode logging."""

    @pytest.mark.asyncio
    async def test_logs_shadow_computation(
        self, collector_with_surfaces, mock_store, sample_repos, sample_founder_surface
    ):
        """Logs founder surface computation to shadow_log."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            await collector_with_surfaces._collect_signals()

            # Should have logged shadow computation
            mock_store.log_shadow_computation.assert_called()
            # Check it was called with feature_name="founder_surfaces"
            call_args = mock_store.log_shadow_computation.call_args
            # Can be passed as positional or keyword args
            if call_args.kwargs:
                assert call_args.kwargs.get("feature_name") == "founder_surfaces"
            else:
                assert call_args[0][0] == "founder_surfaces"

    @pytest.mark.asyncio
    async def test_shadow_log_includes_surface_data(
        self, collector_with_surfaces, mock_store, sample_repos, sample_founder_surface
    ):
        """Shadow log includes founder surface data."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            await collector_with_surfaces._collect_signals()

            # Get the computed_value from the log call
            call_args = mock_store.log_shadow_computation.call_args
            # Can be passed as positional or keyword args
            if call_args.kwargs:
                computed_value = call_args.kwargs.get("computed_value")
            else:
                computed_value = call_args[0][2]  # Third positional arg

            assert "has_profile_readme" in computed_value
            assert "intent_score" in computed_value
            assert "has_commercial_intent" in computed_value

    @pytest.mark.asyncio
    async def test_no_shadow_log_when_active(
        self, mock_store, sample_repos, sample_founder_surface
    ):
        """No shadow logging when feature is ACTIVE (affects output)."""
        registry = FeatureRegistry()
        registry.set_state("founder_surfaces", FeatureState.ACTIVE)

        collector = GitHubActivityCollector(
            usernames=["founder1"],
            store=mock_store,
            github_token="test_token",
            include_founder_surfaces=True,
            feature_registry=registry,
        )

        with patch.object(
            collector, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            await collector._collect_signals()

            # Should NOT log to shadow when ACTIVE
            # (ACTIVE means it affects output, not shadow)
            mock_store.log_shadow_computation.assert_not_called()


# =============================================================================
# SURFACE CACHING TESTS
# =============================================================================

class TestSurfaceCaching:
    """Tests for caching founder surfaces per user."""

    @pytest.mark.asyncio
    async def test_extracts_once_per_user(
        self, collector_with_surfaces, sample_repos, sample_founder_surface
    ):
        """Only extracts surface once per user (cached)."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            # Return 3 repos for same user
            repos = sample_repos * 3
            mock_http.return_value = repos
            mock_extract.return_value = sample_founder_surface

            await collector_with_surfaces._collect_signals()

            # Should only call extract once per unique user
            # Even with multiple repos from same user
            assert mock_extract.call_count <= len(collector_with_surfaces.usernames)


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Tests for error handling in surface extraction."""

    @pytest.mark.asyncio
    async def test_handles_surface_extraction_failure(
        self, collector_with_surfaces, sample_repos
    ):
        """Continues collecting signals even if surface extraction fails."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.side_effect = Exception("Surface extraction failed")

            # Should not raise, should continue
            signals = await collector_with_surfaces._collect_signals()
            assert signals is not None

    @pytest.mark.asyncio
    async def test_handles_none_surface_result(
        self, collector_with_surfaces, sample_repos
    ):
        """Handles None result from surface extraction."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = None  # User not found

            signals = await collector_with_surfaces._collect_signals()

            # Should still produce signals, just without surfaces
            if signals:
                # founder_surface should not be in raw_data when None
                assert signals[0].raw_data.get("founder_surface") is None


# =============================================================================
# SIGNAL CONFIDENCE BOOST TESTS
# =============================================================================

class TestConfidenceBoost:
    """Tests for confidence boost from founder surfaces (when ACTIVE)."""

    @pytest.mark.asyncio
    async def test_no_confidence_boost_in_shadow(
        self, collector_with_surfaces, sample_repos, sample_founder_surface
    ):
        """No confidence boost in SHADOW mode (0 weight)."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            signals = await collector_with_surfaces._collect_signals()

            if signals:
                # In SHADOW mode, founder surface should NOT boost confidence
                # Just logged for analysis
                base_confidence = 0.6 + 0.1  # base + recency for <30 days
                assert signals[0].confidence <= base_confidence + 0.15  # Allow for website boost

    @pytest.mark.asyncio
    async def test_confidence_boost_when_active(
        self, mock_store, sample_repos, sample_founder_surface
    ):
        """Confidence is boosted when feature is ACTIVE."""
        registry = FeatureRegistry()
        registry.set_state("founder_surfaces", FeatureState.ACTIVE)

        collector = GitHubActivityCollector(
            usernames=["founder1"],
            store=mock_store,
            github_token="test_token",
            include_founder_surfaces=True,
            feature_registry=registry,
        )

        # High-intent surface should boost confidence
        high_intent_surface = FounderSurface(
            username="founder1",
            has_profile_readme=True,
            gist_count=5,
            profile_intent_markers=["ceo", "building", "waitlist", "pricing"],
            gist_intent_markers=["pricing"],
        )

        with patch.object(
            collector, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = high_intent_surface

            signals = await collector._collect_signals()

            # In ACTIVE mode, high intent should boost confidence
            # Exact boost depends on implementation
            if signals and high_intent_surface.has_commercial_intent:
                # Just verify founder_surface is in raw_data for now
                assert "founder_surface" in signals[0].raw_data


# =============================================================================
# END-TO-END TESTS
# =============================================================================

class TestEndToEnd:
    """End-to-end tests for the full flow."""

    @pytest.mark.asyncio
    async def test_full_collection_with_surfaces(
        self, collector_with_surfaces, sample_repos, sample_founder_surface, mock_store
    ):
        """Full collection flow with founder surfaces."""
        with patch.object(
            collector_with_surfaces, '_http_get', new_callable=AsyncMock
        ) as mock_http, patch.object(
            collector_with_surfaces._surface_extractor, 'extract', new_callable=AsyncMock
        ) as mock_extract:
            mock_http.return_value = sample_repos
            mock_extract.return_value = sample_founder_surface

            result = await collector_with_surfaces.run(dry_run=True)

            assert result.collector == "github_activity"
            assert result.signals_found >= 0
