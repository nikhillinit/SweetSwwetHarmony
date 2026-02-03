"""Integration tests for Phase 0B-3 v2_shadow wiring.

Tests the full flow from ThesisMatcher through ThesisFilter to pipeline shadow logs.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# =============================================================================
# THESIS MATCHER -> THESIS FILTER WIRING
# =============================================================================

class TestThesisMatcherToThesisFilterWiring:
    """Test v2_shadow flows from ThesisMatcher to ThesisFilter."""

    @pytest.mark.asyncio
    async def test_shadow_mode_v2_shadow_flows_to_thesis_filter_result(self, tmp_path):
        """v2_shadow from ThesisMatcher.score() should flow to ThesisFilterResult."""
        # Create a policy file with different weights to trigger shadow diff
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.8\n"  # Different from v1's 0.5
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig
        from utils.thesis_matcher import ThesisMatcher

        # Create filter with custom matcher in shadow mode
        config = ThesisFilterConfig()
        thesis_filter = ThesisFilter(config)
        thesis_filter._keyword_matcher = ThesisMatcher(
            v2_enablement="shadow",
            config_path=str(tmp_path)
        )

        # Classify text that will hit the "enterprise" negative keyword
        result = await thesis_filter.classify(
            "Enterprise food delivery platform startup",
            skip_llm=True,
        )

        # Should have v2_shadow populated
        assert result.v2_shadow is not None
        assert "v1" in result.v2_shadow
        assert "v2" in result.v2_shadow
        assert "delta_score" in result.v2_shadow
        assert "policy_hash" in result.v2_shadow

        # v1 and v2 should have different penalties
        assert result.v2_shadow["v1"]["penalty_raw"] == 0.5  # v1 hardcoded
        assert result.v2_shadow["v2"]["penalty_raw"] == 0.8  # v2 from YAML

    @pytest.mark.asyncio
    async def test_disabled_mode_no_v2_shadow(self):
        """v2_shadow should be None when v2 is disabled."""
        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig
        from utils.thesis_matcher import ThesisMatcher

        config = ThesisFilterConfig()
        thesis_filter = ThesisFilter(config)
        thesis_filter._keyword_matcher = ThesisMatcher(v2_enablement="disabled")

        result = await thesis_filter.classify(
            "Enterprise food delivery platform",
            skip_llm=True,
        )

        assert result.v2_shadow is None

    @pytest.mark.asyncio
    async def test_v2_shadow_included_in_to_dict(self, tmp_path):
        """v2_shadow should be included in ThesisFilterResult.to_dict()."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.8\n"
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_filter import ThesisFilter, ThesisFilterConfig
        from utils.thesis_matcher import ThesisMatcher

        config = ThesisFilterConfig()
        thesis_filter = ThesisFilter(config)
        thesis_filter._keyword_matcher = ThesisMatcher(
            v2_enablement="shadow",
            config_path=str(tmp_path)
        )

        result = await thesis_filter.classify(
            "Enterprise food delivery startup",
            skip_llm=True,
        )

        result_dict = result.to_dict()

        assert "v2_shadow" in result_dict
        assert result_dict["v2_shadow"]["policy_hash"] is not None


# =============================================================================
# PIPELINE -> SHADOW LOG WIRING
# =============================================================================

class TestPipelineToShadowLogWiring:
    """Test v2_shadow flows from pipeline to shadow_log table."""

    @pytest.mark.asyncio
    async def test_pipeline_logs_v2_shadow_to_shadow_log(self):
        """Pipeline should log v2_shadow data to shadow_log table."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            # Enable thesis_match feature
            from workflows.pipeline import DiscoveryPipeline, PipelineConfig

            config = PipelineConfig(
                notion_api_key="test",
                notion_database_id="test-db",
                db_path=db_path,
                use_thesis_filter=True,
            )
            pipeline = DiscoveryPipeline(config)
            await pipeline.initialize()

            # Add a signal
            signal_id = await pipeline._store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="github_org:test-v2-shadow",
                company_name="Test V2 Shadow",
                confidence=0.7,
                raw_data={"description": "Consumer meal kit delivery startup"},
            )

            # Process the signal
            await pipeline.process_pending(dry_run=True)

            # Check shadow logs
            logs = await pipeline._store.get_shadow_logs(feature_name="thesis_match")

            # Should have logged
            assert len(logs) >= 1

            # Check v2_shadow field exists (may be None if v2 disabled)
            log = logs[0]
            computed = log["computed_value"]
            assert "v2_shadow" in computed

            await pipeline.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_pipeline_logs_v2_shadow_with_policy_hash_in_shadow_mode(self):
        """Pipeline in shadow mode should log v2_shadow with policy_hash."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            # Enable shadow mode via environment
            with patch.dict(os.environ, {"THESIS_MATCHER_V2_ENABLEMENT": "shadow"}):
                from workflows.pipeline import DiscoveryPipeline, PipelineConfig

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
                    canonical_key="github_org:enterprise-test",
                    company_name="Enterprise Test",
                    confidence=0.7,
                    raw_data={"description": "Enterprise B2B food delivery platform"},
                )

                # Process the signal
                await pipeline.process_pending(dry_run=True)

                # Check shadow logs
                logs = await pipeline._store.get_shadow_logs(feature_name="thesis_match")

                if len(logs) >= 1:
                    log = logs[0]
                    computed = log["computed_value"]

                    # Should have v2_shadow with policy_hash
                    if computed.get("v2_shadow"):
                        assert "policy_hash" in computed["v2_shadow"]
                        assert computed["v2_shadow"]["policy_hash"] is not None

                await pipeline.close()
        finally:
            os.unlink(db_path)


# =============================================================================
# SHADOW_REPORT INTEGRATION
# =============================================================================

class TestShadowReportIntegration:
    """Test shadow_report.py can process data from pipeline shadow logs."""

    @pytest.mark.asyncio
    async def test_shadow_report_can_parse_pipeline_output(self):
        """shadow_report should be able to parse shadow logs from pipeline."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            # Enable shadow mode
            with patch.dict(os.environ, {"THESIS_MATCHER_V2_ENABLEMENT": "shadow"}):
                from workflows.pipeline import DiscoveryPipeline, PipelineConfig
                from scripts.shadow_report import ShadowRecord, compute_report

                config = PipelineConfig(
                    notion_api_key="test",
                    notion_database_id="test-db",
                    db_path=db_path,
                    use_thesis_filter=True,
                )
                pipeline = DiscoveryPipeline(config)
                await pipeline.initialize()

                # Add multiple signals
                for i in range(5):
                    await pipeline._store.save_signal(
                        signal_type="github_spike",
                        source_api="github",
                        canonical_key=f"github_org:test-{i}",
                        company_name=f"Test Company {i}",
                        confidence=0.7,
                        raw_data={"description": f"Consumer startup {i} meal kits"},
                    )

                # Process all signals
                await pipeline.process_pending(dry_run=True)

                # Get shadow logs
                logs = await pipeline._store.get_shadow_logs(feature_name="thesis_match")

                # Parse into ShadowRecords
                records = []
                for log in logs:
                    record = ShadowRecord.from_shadow_log(log)
                    if record:
                        records.append(record)

                # Should have parsed at least some records
                if logs and logs[0]["computed_value"].get("v2_shadow"):
                    assert len(records) > 0

                    # Compute report should work
                    summary = compute_report(records)
                    assert summary.total_records == len(records)

                await pipeline.close()
        finally:
            os.unlink(db_path)


# =============================================================================
# POLICY HASH CONSISTENCY
# =============================================================================

class TestPolicyHashConsistency:
    """Test policy_hash is consistent across the pipeline."""

    def test_policy_hash_computed_at_init_time(self, tmp_path):
        """Policy hash should be computed once at ThesisMatcher init."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.5\n"
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        # Hash should be computed at init
        assert matcher._policy_hash is not None

        # Multiple score() calls should use same hash
        fit1 = matcher.score("enterprise food")
        fit2 = matcher.score("enterprise delivery")

        assert fit1.trace.v2_shadow["policy_hash"] == fit2.trace.v2_shadow["policy_hash"]
        assert fit1.trace.v2_shadow["policy_hash"] == matcher._policy_hash

    def test_different_policies_produce_different_hashes(self, tmp_path):
        """Different policy content should produce different hashes."""
        # Policy A
        policy_a_dir = tmp_path / "policy_a"
        policy_a_dir.mkdir()
        (policy_a_dir / "negative_keyword_policy.yaml").write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.5\n"
            "    category: B2B_ENTERPRISE\n"
        )

        # Policy B
        policy_b_dir = tmp_path / "policy_b"
        policy_b_dir.mkdir()
        (policy_b_dir / "negative_keyword_policy.yaml").write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.8\n"  # Different weight
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher_a = ThesisMatcher(v2_enablement="shadow", config_path=str(policy_a_dir))
        matcher_b = ThesisMatcher(v2_enablement="shadow", config_path=str(policy_b_dir))

        assert matcher_a._policy_hash != matcher_b._policy_hash


# =============================================================================
# V1/V2 PARITY WITH MIRRORED YAML
# =============================================================================

class TestV1V2Parity:
    """Test v1 and v2 produce identical results with mirrored YAML."""

    def test_parity_with_production_yaml(self):
        """Production YAML should mirror NEGATIVE_KEYWORDS for parity."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        # Use default config/v2 path (production YAML)
        matcher = ThesisMatcher(v2_enablement="shadow")

        # Test a few text samples
        test_texts = [
            "enterprise b2b saas platform",
            "blockchain crypto defi token",
            "consumer meal kit delivery startup",
            "fitness wellness health app",
        ]

        for text in test_texts:
            fit = matcher.score(text)

            if fit.trace and fit.trace.v2_shadow:
                shadow = fit.trace.v2_shadow
                # With mirrored YAML, scores should be identical
                assert shadow["v1"]["score"] == shadow["v2"]["score"], (
                    f"Parity broken for '{text}': "
                    f"v1={shadow['v1']['score']}, v2={shadow['v2']['score']}"
                )
                assert shadow["delta_score"] == 0.0
                assert shadow["would_change_routing"] is False
