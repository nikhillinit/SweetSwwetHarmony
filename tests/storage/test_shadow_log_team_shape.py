"""Tests for Team Shape SHADOW logging integration.

Tests the full flow of team shape metrics being logged to the shadow_log table.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import os

from utils.team_shape import TeamShapeMetrics, TeamShapeAnalyzer
from utils.feature_states import FeatureRegistry, FeatureState


# =============================================================================
# Shadow Log Storage Tests
# =============================================================================

class TestTeamShapeShadowLogStorage:
    """Test shadow_log storage for team shape metrics."""

    @pytest_asyncio.fixture
    async def signal_store(self):
        """Create a real SignalStore with in-memory SQLite."""
        from storage.signal_store import SignalStore

        # Use a temp file for SQLite (in-memory doesn't support async well)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = SignalStore(db_path=db_path)
            await store.initialize()
            yield store
            await store.close()
        finally:
            # Clean up
            if os.path.exists(db_path):
                os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_log_team_shape_to_shadow_log(self, signal_store):
        """Test logging team shape metrics to shadow_log table."""
        metrics = TeamShapeMetrics(
            contributor_count=3,
            core_contributor_count=2,
            concentration_score=0.45,
            sustained_activity=True,
            activity_span_days=90,
            top_contributors=[
                {"login": "founder1", "commits": 100, "percentage": 0.50},
                {"login": "founder2", "commits": 50, "percentage": 0.25},
            ],
            monthly_activity={"2026-01": 75, "2025-12": 50},
        )

        log_id = await signal_store.log_shadow_computation(
            feature_name="team_shape",
            canonical_key="domain:example.com",
            computed_value=metrics.to_dict(),
        )

        assert log_id > 0

    @pytest.mark.asyncio
    async def test_retrieve_team_shape_logs(self, signal_store):
        """Test retrieving team shape logs from shadow_log."""
        # Log several entries
        for i in range(3):
            metrics = TeamShapeMetrics(
                contributor_count=i + 2,
                core_contributor_count=i + 1,
                concentration_score=0.4 + i * 0.1,
                sustained_activity=True,
                activity_span_days=90,
                top_contributors=[],
                monthly_activity={},
            )

            await signal_store.log_shadow_computation(
                feature_name="team_shape",
                canonical_key=f"domain:company{i}.com",
                computed_value=metrics.to_dict(),
            )

        # Retrieve
        logs = await signal_store.get_shadow_logs(feature_name="team_shape")

        assert len(logs) == 3
        for log in logs:
            assert log["feature_name"] == "team_shape"
            assert "contributor_count" in log["computed_value"]
            assert "is_startup_team" in log["computed_value"]

    @pytest.mark.asyncio
    async def test_count_team_shape_logs(self, signal_store):
        """Test counting team shape logs."""
        # Log some entries
        for i in range(5):
            metrics = TeamShapeMetrics(
                contributor_count=3,
                core_contributor_count=2,
                concentration_score=0.45,
                sustained_activity=True,
                activity_span_days=90,
                top_contributors=[],
                monthly_activity={},
            )

            await signal_store.log_shadow_computation(
                feature_name="team_shape",
                canonical_key=f"domain:company{i}.com",
                computed_value=metrics.to_dict(),
            )

        count = await signal_store.count_shadow_logs(feature_name="team_shape")
        assert count == 5


# =============================================================================
# Feature State Integration Tests
# =============================================================================

class TestTeamShapeFeatureStateIntegration:
    """Test feature state integration for team shape SHADOW logging."""

    def test_team_shape_is_shadow_by_default(self):
        """team_shape feature should be SHADOW by default."""
        registry = FeatureRegistry()
        assert registry.get_state("team_shape") == FeatureState.SHADOW

    def test_team_shape_is_enabled_when_shadow(self):
        """team_shape should be enabled (computed) when SHADOW."""
        registry = FeatureRegistry()
        assert registry.is_enabled("team_shape") is True

    def test_team_shape_is_not_active_when_shadow(self):
        """team_shape should not affect routing when SHADOW."""
        registry = FeatureRegistry()
        assert registry.is_active("team_shape") is False

    def test_can_promote_team_shape_to_active(self):
        """team_shape can be promoted to ACTIVE after SHADOW period."""
        registry = FeatureRegistry()
        registry.set_state("team_shape", FeatureState.ACTIVE)
        assert registry.is_active("team_shape") is True

    def test_env_override_for_team_shape(self):
        """Environment variable can override team_shape state."""
        with patch.dict(os.environ, {"FEATURE_TEAM_SHAPE": "off"}):
            registry = FeatureRegistry()
            assert registry.get_state("team_shape") == FeatureState.OFF
            assert registry.is_enabled("team_shape") is False


# =============================================================================
# Full Flow Integration Tests
# =============================================================================

class TestTeamShapeFullFlow:
    """Test the complete flow from contributor data to shadow log."""

    @pytest_asyncio.fixture
    async def signal_store(self):
        """Create a real SignalStore with in-memory SQLite."""
        from storage.signal_store import SignalStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = SignalStore(db_path=db_path)
            await store.initialize()
            yield store
            await store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_full_flow_analyze_and_log(self, signal_store):
        """Test full flow: analyze contributor data and log to shadow."""
        # 1. Create contributor data (simulating GitHub API response)
        contributor_data = [
            {
                "login": "founder1",
                "total": 150,
                "weeks": [
                    {"w": 1735689600, "c": 50},
                    {"w": 1738368000, "c": 50},
                    {"w": 1740960000, "c": 50},
                ]
            },
            {
                "login": "founder2",
                "total": 100,
                "weeks": [
                    {"w": 1735689600, "c": 35},
                    {"w": 1738368000, "c": 35},
                    {"w": 1740960000, "c": 30},
                ]
            },
        ]

        # 2. Analyze team shape
        analyzer = TeamShapeAnalyzer()
        metrics = analyzer.analyze_from_contributor_stats(contributor_data)

        # 3. Verify analysis results
        assert metrics.contributor_count == 2
        assert metrics.core_contributor_count == 2
        assert metrics.is_startup_team is True

        # 4. Log to shadow
        log_id = await signal_store.log_shadow_computation(
            feature_name="team_shape",
            canonical_key="domain:startup.com",
            computed_value=metrics.to_dict(),
        )

        # 5. Verify log was stored
        assert log_id > 0

        # 6. Retrieve and verify
        logs = await signal_store.get_shadow_logs(
            feature_name="team_shape",
            canonical_key="domain:startup.com",
        )

        assert len(logs) == 1
        log = logs[0]
        assert log["computed_value"]["is_startup_team"] is True
        assert log["computed_value"]["contributor_count"] == 2

    @pytest.mark.asyncio
    async def test_flow_respects_feature_state(self, signal_store):
        """Test that logging respects feature state (only when enabled)."""
        registry = FeatureRegistry()

        # When SHADOW, should be enabled
        assert registry.is_enabled("team_shape") is True

        # When OFF, should not be enabled
        with patch.dict(os.environ, {"FEATURE_TEAM_SHAPE": "off"}):
            registry_off = FeatureRegistry()
            assert registry_off.is_enabled("team_shape") is False

    @pytest.mark.asyncio
    async def test_non_startup_team_logged(self, signal_store):
        """Test that non-startup teams are also logged for analysis."""
        # Solo developer project
        contributor_data = [
            {
                "login": "solo_dev",
                "total": 500,
                "weeks": [
                    {"w": 1735689600, "c": 200},
                    {"w": 1738368000, "c": 200},
                    {"w": 1740960000, "c": 100},
                ]
            }
        ]

        analyzer = TeamShapeAnalyzer()
        metrics = analyzer.analyze_from_contributor_stats(contributor_data)

        # Should be single contributor (not startup team)
        assert metrics.contributor_count == 1
        assert metrics.is_startup_team is False
        assert metrics.concentration_score == 1.0

        # Still log to shadow for analysis
        log_id = await signal_store.log_shadow_computation(
            feature_name="team_shape",
            canonical_key="github_user:solo_dev",
            computed_value=metrics.to_dict(),
        )

        assert log_id > 0

        # Verify logged correctly
        logs = await signal_store.get_shadow_logs(feature_name="team_shape")
        assert len(logs) == 1
        assert logs[0]["computed_value"]["is_startup_team"] is False
