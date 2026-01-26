"""Test enrichment boost integration with pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from storage.signal_store import StoredSignal
from utils.signal_consolidator import ConsolidatedSignal
from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentBoost


class TestPipelineEnrichmentBoostIntegration:
    """Test that pipeline uses EnrichmentBoostCalculator."""

    @pytest.mark.asyncio
    async def test_pipeline_config_has_use_enrichment_boost_flag(self):
        """PipelineConfig should have use_enrichment_boost flag."""
        from workflows.pipeline import PipelineConfig

        # Default should be True
        config = PipelineConfig()
        assert hasattr(config, "use_enrichment_boost")
        assert config.use_enrichment_boost is True

        # Should be configurable
        config_off = PipelineConfig(use_enrichment_boost=False)
        assert config_off.use_enrichment_boost is False

    @pytest.mark.asyncio
    async def test_pipeline_creates_enrichment_calculator_when_enabled(self):
        """Pipeline should create EnrichmentBoostCalculator when use_enrichment_boost=True."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(use_enrichment_boost=True, use_consolidation=True)
        pipeline = DiscoveryPipeline(config)

        # Should have enrichment calculator attribute
        assert hasattr(pipeline, "_enrichment_calculator")
        assert isinstance(pipeline._enrichment_calculator, EnrichmentBoostCalculator)

    @pytest.mark.asyncio
    async def test_pipeline_no_calculator_when_disabled(self):
        """Pipeline should not create EnrichmentBoostCalculator when use_enrichment_boost=False."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(use_enrichment_boost=False)
        pipeline = DiscoveryPipeline(config)

        # Should not have calculator
        assert hasattr(pipeline, "_enrichment_calculator")
        assert pipeline._enrichment_calculator is None

    @pytest.mark.asyncio
    async def test_enrichment_boost_passed_to_gate(self):
        """Pipeline should pass enrichment_boost to gate.evaluate()."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)
        # Create a signal with enrichment data (old company with social proof)
        founding_date = now - timedelta(days=800)  # ~2 years old

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={
                    "description": "AI tool",
                    "stars": 1500,  # High social proof
                    "founding_date": founding_date.isoformat(),
                },
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_queued = AsyncMock()
        store.mark_rejected = AsyncMock()
        store.mark_pushed = AsyncMock()

        # Mock the gate to capture the enrichment_boost parameter
        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=True,
            use_consolidation=True,
            use_founder_scoring=False,  # Disable to isolate test
            use_velocity_tracking=False,  # Disable to isolate test
            use_thesis_filter=False,  # Disable to isolate test
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        # Mock the consolidator to return a signal with enrichment data
        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
            founding_date=founding_date,  # 2+ years old
            social_proof={"stars": 1500},  # High stars
        )

        # Mock the consolidator
        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = consolidated
        pipeline._consolidator = mock_consolidator

        # Run processing
        await pipeline._process_signals_stage(dry_run=True)

        # Verify gate.evaluate was called with enrichment_boost parameter
        mock_gate.evaluate.assert_called_once()
        call_kwargs = mock_gate.evaluate.call_args.kwargs

        # enrichment_boost should be passed
        assert "enrichment_boost" in call_kwargs, "enrichment_boost not passed to gate.evaluate()"

        # With 2+ year old company and 1500 stars, we should get a boost
        # Age boost: 0.03 (>= 730 days)
        # Social boost: 0.02 (stars >= 1000)
        # Total: 0.05 (capped)
        assert call_kwargs["enrichment_boost"] > 0, "enrichment_boost should be > 0 for old company with stars"
        assert call_kwargs["enrichment_boost"] <= 0.05, "enrichment_boost should be capped at 0.05"

    @pytest.mark.asyncio
    async def test_enrichment_boost_zero_when_no_enrichment_data(self):
        """Enrichment boost should be 0 when no enrichment data is available."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={"description": "AI tool"},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=True,
            use_consolidation=True,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        # Mock consolidator with no enrichment data
        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
            founding_date=None,  # No founding date
            social_proof={},  # No social proof
        )

        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = consolidated
        pipeline._consolidator = mock_consolidator

        await pipeline._process_signals_stage(dry_run=True)

        call_kwargs = mock_gate.evaluate.call_args.kwargs
        assert call_kwargs["enrichment_boost"] == 0.0, "enrichment_boost should be 0 with no enrichment data"

    @pytest.mark.asyncio
    async def test_enrichment_boost_not_calculated_when_disabled(self):
        """Enrichment boost should not be calculated when use_enrichment_boost=False."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=800)

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=False,  # Disabled
            use_consolidation=True,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        # Even with enrichment data, calculator should not be used
        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
            founding_date=founding_date,
            social_proof={"stars": 1500},
        )

        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = consolidated
        pipeline._consolidator = mock_consolidator

        await pipeline._process_signals_stage(dry_run=True)

        # Should still pass enrichment_boost=0.0 to gate (not calculated)
        call_kwargs = mock_gate.evaluate.call_args.kwargs
        assert call_kwargs["enrichment_boost"] == 0.0, "enrichment_boost should be 0 when disabled"

    @pytest.mark.asyncio
    async def test_enrichment_calculator_handles_errors_gracefully(self):
        """Pipeline should handle enrichment calculation errors gracefully."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=True,
            use_consolidation=True,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        # Mock consolidator
        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
        )

        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = consolidated
        pipeline._consolidator = mock_consolidator

        # Mock the enrichment calculator to raise an error
        mock_calculator = MagicMock(spec=EnrichmentBoostCalculator)
        mock_calculator.calculate.side_effect = Exception("Calculation error")
        pipeline._enrichment_calculator = mock_calculator

        # Should not raise - errors are handled gracefully
        await pipeline._process_signals_stage(dry_run=True)

        # Gate should still be called with enrichment_boost=0.0 (fallback)
        call_kwargs = mock_gate.evaluate.call_args.kwargs
        assert call_kwargs["enrichment_boost"] == 0.0, "enrichment_boost should fallback to 0 on error"

    @pytest.mark.asyncio
    async def test_enrichment_boost_logged_when_nonzero(self):
        """Pipeline should log enrichment boost when it's non-zero."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=800)

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=True,
            use_consolidation=True,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
            founding_date=founding_date,
            social_proof={"stars": 1500},
        )

        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = consolidated
        pipeline._consolidator = mock_consolidator

        with patch("workflows.pipeline.logger") as mock_logger:
            await pipeline._process_signals_stage(dry_run=True)

            # Should have logged info about enrichment boost
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            enrichment_logs = [call for call in info_calls if "enrichment" in call.lower() or "Enrichment" in call]
            assert len(enrichment_logs) > 0, "Expected enrichment boost to be logged"


class TestEnrichmentMetrics:
    """Test enrichment metrics in pipeline stats."""

    def test_stats_include_enrichment_metrics(self):
        """Pipeline stats should include enrichment boost metrics."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(use_enrichment_boost=True)
        pipeline = DiscoveryPipeline(config=config)

        # Verify the stats keys exist (actual pipeline run would populate them)
        # For unit test, we just verify the structure is correct
        assert hasattr(pipeline, '_enrichment_calculator')
        assert pipeline._enrichment_calculator is not None

    @pytest.mark.asyncio
    async def test_enrichment_metrics_tracked_during_processing(self):
        """Pipeline should track enrichment boost metrics during signal processing."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=800)  # ~2 years old

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={},
                detected_at=now, created_at=now,
            ),
            StoredSignal(
                id=2, signal_type="product_launch", source_api="product_hunt",
                canonical_key="domain:beta.io", company_name="Beta Corp",
                confidence=0.7, raw_data={},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=True,
            use_consolidation=True,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        # Create two consolidated signals - one with boost, one without
        consolidated_with_boost = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
            founding_date=founding_date,  # Old company = boost
            social_proof={"stars": 1500},  # High stars = boost
        )

        consolidated_no_boost = ConsolidatedSignal(
            canonical_key="domain:beta.io",
            company_name="Beta Corp",
            contributing_signal_ids=[2],
            signal_types=["product_launch"],
            source_apis=["product_hunt"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["Product Hunt launch"],
            founding_date=None,  # No founding date = no boost
            social_proof={},  # No social proof = no boost
        )

        # Mock consolidator to return appropriate consolidated signal
        consolidate_returns = {
            "domain:acme.ai": consolidated_with_boost,
            "domain:beta.io": consolidated_no_boost,
        }
        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.side_effect = lambda sigs: consolidate_returns[sigs[0].canonical_key]
        pipeline._consolidator = mock_consolidator

        # Run processing
        stats = await pipeline._process_signals_stage(dry_run=True)

        # Verify enrichment metrics are tracked
        assert "enrichment_boosts_applied" in stats, "Stats should include enrichment_boosts_applied"
        assert "total_enrichment_boost" in stats, "Stats should include total_enrichment_boost"
        assert "avg_enrichment_boost" in stats, "Stats should include avg_enrichment_boost"

        # One company had boost (acme.ai), one did not (beta.io)
        assert stats["enrichment_boosts_applied"] == 1, "Expected 1 enrichment boost applied"
        assert stats["total_enrichment_boost"] > 0, "Expected total boost > 0"
        assert stats["avg_enrichment_boost"] > 0, "Expected avg boost > 0"

    @pytest.mark.asyncio
    async def test_enrichment_metrics_zero_when_no_boosts(self):
        """Enrichment metrics should be zero when no boosts are applied."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationGate, PushDecision, VerificationStatus, VerificationResult
        )

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        mock_gate = MagicMock(spec=VerificationGate)
        mock_result = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.5,
            confidence_breakdown={},
            reason="Test",
            suggested_status="",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )
        mock_gate.evaluate.return_value = mock_result

        config = PipelineConfig(
            use_enrichment_boost=True,
            use_consolidation=True,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._gate = mock_gate
        pipeline._initialized = True

        # No enrichment data = no boost
        consolidated_no_boost = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["GitHub trending"],
            founding_date=None,
            social_proof={},
        )

        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = consolidated_no_boost
        pipeline._consolidator = mock_consolidator

        stats = await pipeline._process_signals_stage(dry_run=True)

        assert stats["enrichment_boosts_applied"] == 0
        assert stats["total_enrichment_boost"] == 0.0
        assert stats["avg_enrichment_boost"] == 0.0


class TestEnrichedProspectPayload:
    """Test enriched fields in ProspectPayload."""

    def test_prospect_payload_has_founding_date_field(self):
        """ProspectPayload should have founding_date field."""
        from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage
        from datetime import datetime, timezone

        payload = ProspectPayload(
            discovery_id="test-123",
            company_name="Acme Inc",
            canonical_key="domain:acme.ai",
            stage=InvestmentStage.PRE_SEED,
            founding_date=datetime.now(timezone.utc),
        )
        assert payload.founding_date is not None

    def test_prospect_payload_has_social_proof_score_field(self):
        """ProspectPayload should have social_proof_score field."""
        from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage

        payload = ProspectPayload(
            discovery_id="test-123",
            company_name="Acme Inc",
            canonical_key="domain:acme.ai",
            stage=InvestmentStage.PRE_SEED,
            social_proof_score=1500,
        )
        assert payload.social_proof_score == 1500

    def test_prospect_payload_defaults(self):
        """Enrichment fields should have sensible defaults."""
        from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage

        payload = ProspectPayload(
            discovery_id="test-123",
            company_name="Acme Inc",
            canonical_key="domain:acme.ai",
            stage=InvestmentStage.PRE_SEED,
        )
        assert payload.founding_date is None
        assert payload.social_proof_score == 0
