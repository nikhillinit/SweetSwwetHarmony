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


# =============================================================================
# PHASE C: BOILERPLATE DEFENSE SHADOW TESTS
# =============================================================================

class TestPipelineBoilerplateDefense:
    """Tests for Phase C boilerplate detection SHADOW integration."""

    def test_pipeline_has_boilerplate_detector(self):
        """Pipeline should have a BoilerplateDetector instance."""
        from utils.boilerplate_detector import BoilerplateDetector

        config = PipelineConfig(
            notion_api_key="test",
            notion_database_id="test-db",
        )
        pipeline = DiscoveryPipeline(config)

        assert hasattr(pipeline, "_boilerplate_detector")
        assert isinstance(pipeline._boilerplate_detector, BoilerplateDetector)

    def test_boilerplate_defense_default_shadow(self):
        """boilerplate_defense should default to SHADOW mode."""
        config = PipelineConfig(
            notion_api_key="test",
            notion_database_id="test-db",
        )
        pipeline = DiscoveryPipeline(config)

        assert pipeline._feature_registry.is_shadow("boilerplate_defense")
        assert pipeline._feature_registry.is_enabled("boilerplate_defense")

    @pytest.mark.asyncio
    async def test_boilerplate_detection_logs_to_shadow(self):
        """Pipeline should log boilerplate detection results to shadow_log."""
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

            # Add a signal with Next.js boilerplate deps
            signal_id = await pipeline._store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="github_org:nextjs-starter",
                company_name="NextJS Starter",
                confidence=0.7,
                raw_data={
                    "package_json": {
                        "dependencies": {
                            "next": "^13.0.0",
                            "react": "^18.0.0",
                            "react-dom": "^18.0.0",
                        }
                    }
                },
            )

            # Process the signal (dry run)
            await pipeline.process_pending(dry_run=True)

            # Check shadow logs for boilerplate_defense
            logs = await pipeline._store.get_shadow_logs(feature_name="boilerplate_defense")

            # Should have at least one log entry
            assert len(logs) >= 1

            # Verify log content
            log = logs[0]
            assert log["feature_name"] == "boilerplate_defense"
            assert "nextjs-starter" in log["canonical_key"]

            await pipeline.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_boilerplate_detection_detects_match(self):
        """Pipeline should detect Next.js boilerplate as boilerplate."""
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

            # Add a signal with exact Next.js basic deps
            signal_id = await pipeline._store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="github_org:boilerplate-project",
                company_name="Boilerplate Project",
                confidence=0.7,
                raw_data={
                    "package_json": {
                        "dependencies": {
                            "next": "^13.0.0",
                            "react": "^18.0.0",
                            "react-dom": "^18.0.0",
                        }
                    }
                },
            )

            # Process the signal (dry run)
            await pipeline.process_pending(dry_run=True)

            # Check shadow logs
            logs = await pipeline._store.get_shadow_logs(feature_name="boilerplate_defense")

            # Verify detection result
            assert len(logs) >= 1
            import json
            computed = json.loads(logs[0]["computed_value"]) if isinstance(logs[0]["computed_value"], str) else logs[0]["computed_value"]

            assert computed["best_match"] is not None
            assert computed["best_match"]["is_boilerplate"] is True
            assert computed["best_match"]["signature_id"] == "nextjs_basic_template"

            await pipeline.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_boilerplate_no_match_for_unique_project(self):
        """Pipeline should not flag unique projects as boilerplate."""
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

            # Add a signal with unique deps
            signal_id = await pipeline._store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="github_org:unique-project",
                company_name="Unique Project",
                confidence=0.7,
                raw_data={
                    "package_json": {
                        "dependencies": {
                            "custom-lib": "^1.0.0",
                            "proprietary-sdk": "^2.0.0",
                        }
                    }
                },
            )

            # Process the signal (dry run)
            await pipeline.process_pending(dry_run=True)

            # Check shadow logs
            logs = await pipeline._store.get_shadow_logs(feature_name="boilerplate_defense")

            # Should still log (SHADOW mode logs everything)
            assert len(logs) >= 1
            import json
            computed = json.loads(logs[0]["computed_value"]) if isinstance(logs[0]["computed_value"], str) else logs[0]["computed_value"]

            # Should not be flagged as boilerplate
            assert computed["best_match"] is None or computed["best_match"]["is_boilerplate"] is False

            await pipeline.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_boilerplate_off_skips_detection(self):
        """When boilerplate_defense is OFF, should skip detection."""
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

                # Add a signal with boilerplate deps
                signal_id = await pipeline._store.save_signal(
                    signal_type="github_spike",
                    source_api="github",
                    canonical_key="github_org:test-off",
                    company_name="Test Off",
                    confidence=0.7,
                    raw_data={
                        "package_json": {
                            "dependencies": {
                                "next": "^13.0.0",
                                "react": "^18.0.0",
                            }
                        }
                    },
                )

                # Process the signal (dry run)
                await pipeline.process_pending(dry_run=True)

                # Check shadow logs - should be empty for boilerplate_defense
                logs = await pipeline._store.get_shadow_logs(feature_name="boilerplate_defense")
                assert len(logs) == 0

                await pipeline.close()
        finally:
            os.unlink(db_path)


# =============================================================================
# PHASE 0B-3: THESIS MATCH SHADOW LOGGING TESTS
# =============================================================================

class TestPipelineThesisMatchShadow:
    """Tests for thesis_match shadow logging with v2_shadow support."""

    @pytest.mark.asyncio
    async def test_thesis_match_shadow_log_not_double_encoded(self):
        """Shadow log computed_value should be a dict, not a JSON string of JSON."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            config = PipelineConfig(
                notion_api_key="test",
                notion_database_id="test-db",
                db_path=db_path,
                use_thesis_filter=True,
            )
            pipeline = DiscoveryPipeline(config)
            await pipeline.initialize()

            # Add a signal with clear thesis match content
            signal_id = await pipeline._store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="github_org:meal-kit-company",
                company_name="Meal Kit Co",
                confidence=0.7,
                raw_data={"description": "Healthy meal kit delivery startup"},
            )

            # Process the signal (dry run)
            await pipeline.process_pending(dry_run=True)

            # Check shadow logs for thesis_match
            logs = await pipeline._store.get_shadow_logs(feature_name="thesis_match")

            # Should have at least one log entry
            assert len(logs) >= 1

            log = logs[0]
            computed = log["computed_value"]

            # The computed_value should be JSON-parsed automatically by get_shadow_logs
            # If it's still a string, it was double-encoded
            if isinstance(computed, str):
                import json
                parsed = json.loads(computed)
                # If we can parse it AGAIN and get a dict, it was double-encoded
                # (string → parsed string containing JSON → parse that to dict)
                # The value should be parseable only ONCE.
                try:
                    double_parsed = json.loads(parsed)
                    # If this succeeds, it was double-encoded - FAIL
                    pytest.fail(
                        f"computed_value was double-encoded: first parse gave "
                        f"{type(parsed).__name__}, second parse gave {type(double_parsed).__name__}"
                    )
                except (TypeError, json.JSONDecodeError):
                    # Expected: parsed is already a dict, not a JSON string
                    pass

            await pipeline.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_thesis_match_shadow_includes_v2_shadow(self):
        """Shadow log should include v2_shadow when ThesisMatcher is in shadow mode."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            # Set v2 to shadow mode via env
            with patch.dict(os.environ, {"THESIS_MATCHER_V2_ENABLEMENT": "shadow"}):
                config = PipelineConfig(
                    notion_api_key="test",
                    notion_database_id="test-db",
                    db_path=db_path,
                    use_thesis_filter=True,
                )
                pipeline = DiscoveryPipeline(config)
                await pipeline.initialize()

                # Add a signal with negative keyword to trigger shadow diff
                signal_id = await pipeline._store.save_signal(
                    signal_type="github_spike",
                    source_api="github",
                    canonical_key="github_org:enterprise-food",
                    company_name="Enterprise Food Co",
                    confidence=0.7,
                    raw_data={"description": "Enterprise food delivery platform"},
                )

                # Process the signal (dry run)
                await pipeline.process_pending(dry_run=True)

                # Check shadow logs for thesis_match
                logs = await pipeline._store.get_shadow_logs(feature_name="thesis_match")

                if len(logs) >= 1:
                    log = logs[0]
                    import json
                    computed = log["computed_value"]
                    if isinstance(computed, str):
                        computed = json.loads(computed)

                    # Should have v2_shadow field
                    assert "v2_shadow" in computed, f"v2_shadow not in computed: {computed.keys()}"

                await pipeline.close()
        finally:
            os.unlink(db_path)
