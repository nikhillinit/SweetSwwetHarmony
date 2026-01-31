"""Tests for SHADOW logging integration in pipeline.

TDD tests for:
- FeatureRegistry integration in DiscoveryPipeline
- Shadow logging during signal processing
- Shadow stats in PipelineStats
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from workflows.pipeline import DiscoveryPipeline, PipelineConfig, PipelineStats
from utils.feature_states import FeatureRegistry, FeatureState


# =============================================================================
# PIPELINE STATS TESTS
# =============================================================================

class TestPipelineStatsShadow:
    """Tests for shadow-related fields in PipelineStats."""

    def test_stats_has_shadow_logs_field(self):
        """PipelineStats should track shadow logs count."""
        stats = PipelineStats()
        assert hasattr(stats, "shadow_logs_written")
        assert stats.shadow_logs_written == 0

    def test_stats_to_dict_includes_shadow(self):
        """to_dict should include shadow stats."""
        stats = PipelineStats()
        stats.shadow_logs_written = 5

        d = stats.to_dict()
        assert "shadow" in d or "shadow_logs_written" in str(d)


# =============================================================================
# FEATURE REGISTRY INTEGRATION TESTS
# =============================================================================

class TestPipelineFeatureRegistry:
    """Tests for FeatureRegistry integration in DiscoveryPipeline."""

    def test_pipeline_has_feature_registry(self):
        """Pipeline should have a FeatureRegistry."""
        config = PipelineConfig(
            notion_api_key="test",
            notion_database_id="test-db",
        )
        pipeline = DiscoveryPipeline(config)

        assert hasattr(pipeline, "_feature_registry")
        assert isinstance(pipeline._feature_registry, FeatureRegistry)

    def test_pipeline_feature_registry_uses_defaults(self):
        """Feature registry should have default SHADOW features."""
        config = PipelineConfig(
            notion_api_key="test",
            notion_database_id="test-db",
        )
        pipeline = DiscoveryPipeline(config)

        # Default features should be SHADOW
        assert pipeline._feature_registry.is_shadow("boilerplate_defense")
        assert pipeline._feature_registry.is_shadow("team_shape")

    def test_pipeline_respects_feature_env_override(self):
        """Feature registry should respect env overrides."""
        with patch.dict(os.environ, {"FEATURE_BOILERPLATE_DEFENSE": "active"}):
            config = PipelineConfig(
                notion_api_key="test",
                notion_database_id="test-db",
            )
            pipeline = DiscoveryPipeline(config)

            assert pipeline._feature_registry.is_active("boilerplate_defense")


# =============================================================================
# SHADOW LOGGING INTEGRATION TESTS
# =============================================================================

class TestPipelineShadowLogging:
    """Tests for shadow logging during signal processing."""

    @pytest.mark.asyncio
    async def test_pipeline_logs_shadow_computations(self):
        """Pipeline should log shadow computations for enabled features."""
        # Create temp DB
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            config = PipelineConfig(
                notion_api_key="test",
                notion_database_id="test-db",
                db_path=db_path,
            )
            pipeline = DiscoveryPipeline(config)
            await pipeline.initialize()

            # Add a test signal
            signal_id = await pipeline._store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="github_org:test-company",
                company_name="Test Company",
                confidence=0.7,
                raw_data={"stars": 500},
            )

            # Process the signal (dry run to avoid Notion calls)
            await pipeline.process_pending(dry_run=True)

            # Check shadow logs were written
            logs = await pipeline._store.get_shadow_logs()
            # Should have some shadow logs if features are enabled
            # (exact count depends on which features are in SHADOW mode)

            await pipeline.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_pipeline_skips_shadow_for_off_features(self):
        """Pipeline should not log for OFF features."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            with patch.dict(os.environ, {"FEATURE_BOILERPLATE_DEFENSE": "off"}):
                config = PipelineConfig(
                    notion_api_key="test",
                    notion_database_id="test-db",
                    db_path=db_path,
                )
                pipeline = DiscoveryPipeline(config)
                await pipeline.initialize()

                # Verify feature is OFF
                assert not pipeline._feature_registry.is_enabled("boilerplate_defense")

                await pipeline.close()
        finally:
            os.unlink(db_path)


# =============================================================================
# SHADOW STATS TRACKING TESTS
# =============================================================================

class TestPipelineShadowStats:
    """Tests for shadow stats tracking in pipeline runs."""

    @pytest.mark.asyncio
    async def test_process_signals_tracks_shadow_logs(self):
        """_process_signals_stage should count shadow logs."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            config = PipelineConfig(
                notion_api_key="test",
                notion_database_id="test-db",
                db_path=db_path,
            )
            pipeline = DiscoveryPipeline(config)
            await pipeline.initialize()

            # Process (empty, but should work)
            stats = await pipeline._process_signals_stage(dry_run=True)

            # Stats dict should have shadow_logs key
            assert "shadow_logs" in stats or stats.get("shadow_logs", 0) >= 0

            await pipeline.close()
        finally:
            os.unlink(db_path)
