"""Test signal consolidation integration with pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from storage.signal_store import StoredSignal
from utils.signal_consolidator import SignalConsolidator, ConsolidatedSignal


class TestPipelineConsolidationIntegration:
    """Test that pipeline uses SignalConsolidator."""

    @pytest.mark.asyncio
    async def test_pipeline_config_has_use_consolidation_flag(self):
        """PipelineConfig should have use_consolidation flag."""
        from workflows.pipeline import PipelineConfig

        # Default should be True
        config = PipelineConfig()
        assert hasattr(config, "use_consolidation")
        assert config.use_consolidation is True

        # Should be configurable
        config_off = PipelineConfig(use_consolidation=False)
        assert config_off.use_consolidation is False

    @pytest.mark.asyncio
    async def test_pipeline_creates_consolidator_when_enabled(self):
        """Pipeline should create SignalConsolidator when use_consolidation=True."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config)

        # Should have consolidator attribute
        assert hasattr(pipeline, "_consolidator")
        assert isinstance(pipeline._consolidator, SignalConsolidator)

    @pytest.mark.asyncio
    async def test_pipeline_no_consolidator_when_disabled(self):
        """Pipeline should not create SignalConsolidator when use_consolidation=False."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(use_consolidation=False)
        pipeline = DiscoveryPipeline(config)

        # Should not have consolidator
        assert hasattr(pipeline, "_consolidator")
        assert pipeline._consolidator is None

    @pytest.mark.asyncio
    async def test_pipeline_consolidates_signals_before_processing(self):
        """Pipeline should consolidate signals before _process_company."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import PushDecision

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="acme-ai",
                confidence=0.8, raw_data={"description": "AI tool", "stars": 100},
                detected_at=now, created_at=now,
            ),
            StoredSignal(
                id=2, signal_type="incorporation", source_api="companies_house",
                canonical_key="domain:acme.ai", company_name="Acme AI Ltd",
                confidence=0.7, raw_data={"founding_date": "2023-06-15"},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_queued = AsyncMock()
        store.mark_rejected = AsyncMock()
        store.mark_pushed = AsyncMock()
        store.enqueue_notion_write = AsyncMock(return_value="outbox-123")

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None  # No Notion, so dry-run behavior
        pipeline._initialized = True

        # Track if consolidation was performed using a spy
        consolidator_called = False
        original_consolidate = SignalConsolidator.consolidate

        def tracking_consolidate(self_consolidator, sigs):
            nonlocal consolidator_called
            consolidator_called = True
            return original_consolidate(self_consolidator, sigs)

        with patch.object(SignalConsolidator, 'consolidate', tracking_consolidate):
            # Run processing
            await pipeline._process_signals_stage(dry_run=True)

        # Verify consolidator was called
        assert consolidator_called, "SignalConsolidator.consolidate() was not called"

    @pytest.mark.asyncio
    async def test_consolidated_data_passed_to_push_to_notion(self):
        """Pipeline should pass consolidated data to _push_to_notion."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationResult, PushDecision, VerificationStatus, VerificationGate
        )

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="acme-ai",
                confidence=0.8, raw_data={"description": "AI tool", "stars": 100},
                detected_at=now, created_at=now,
            ),
            StoredSignal(
                id=2, signal_type="incorporation", source_api="companies_house",
                canonical_key="domain:acme.ai", company_name="Acme AI Ltd",
                confidence=0.7, raw_data={"founding_date": "2023-06-15"},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_queued = AsyncMock()
        store.mark_pushed = AsyncMock()
        store.enqueue_notion_write = AsyncMock(return_value="outbox-123")

        notion = AsyncMock()

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = notion
        pipeline._gate = VerificationGate()  # Need the gate for verification
        pipeline._initialized = True

        # Track what consolidated data is passed to _push_to_notion
        captured_consolidated = None

        async def capture_push(signals, verification, consolidated=None):
            nonlocal captured_consolidated
            captured_consolidated = consolidated
            return {"status": "queued", "outbox_id": "test", "idempotency_key": "key"}

        pipeline._push_to_notion = capture_push

        # Run processing (not dry_run to trigger actual push)
        await pipeline._process_signals_stage(dry_run=False)

        # Verify consolidated was passed
        assert captured_consolidated is not None, "consolidated parameter was not passed to _push_to_notion"
        assert isinstance(captured_consolidated, ConsolidatedSignal)
        # Companies House has higher priority, so should use "Acme AI Ltd"
        assert captured_consolidated.company_name == "Acme AI Ltd"

    @pytest.mark.asyncio
    async def test_push_to_notion_uses_consolidated_company_name(self):
        """_push_to_notion should use consolidated company_name when available."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationResult, PushDecision, VerificationStatus
        )

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="acme-ai-repo",
                confidence=0.8, raw_data={"description": "AI tool"},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.enqueue_notion_write = AsyncMock(return_value="outbox-123")

        notion = AsyncMock()

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = notion
        pipeline._initialized = True

        # Create a consolidated signal with a different (better) company name
        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI Inc",  # From Companies House or SEC
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["Recent GitHub activity"],
        )

        verification = VerificationResult(
            decision=PushDecision.AUTO_PUSH,
            verification_status=VerificationStatus.MULTI_SOURCE,
            confidence_score=0.8,
            confidence_breakdown={},
            reason="High confidence",
            suggested_status="Source",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )

        # Call _push_to_notion directly with consolidated
        result = await pipeline._push_to_notion(signals, verification, consolidated=consolidated)

        # Verify enqueue was called
        assert store.enqueue_notion_write.called

        # Get the payload that was enqueued
        call_args = store.enqueue_notion_write.call_args
        payload = call_args.kwargs.get("payload") or call_args[1].get("payload")

        # The company_name in the payload should be from consolidated
        assert payload["prospect"]["company_name"] == "Acme AI Inc"

    @pytest.mark.asyncio
    async def test_consolidation_conflict_logging(self):
        """Pipeline should log when consolidation detects conflicts."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        import logging

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme AI",
                confidence=0.8, raw_data={},
                detected_at=now, created_at=now,
            ),
            StoredSignal(
                id=2, signal_type="incorporation", source_api="companies_house",
                canonical_key="domain:acme.ai", company_name="Acme Limited",
                confidence=0.7, raw_data={},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_pushed = AsyncMock()

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = None
        pipeline._initialized = True

        # Capture log output
        with patch("workflows.pipeline.logger") as mock_logger:
            await pipeline._process_signals_stage(dry_run=True)

            # Should have logged a warning about conflicts
            warning_calls = [call for call in mock_logger.warning.call_args_list]
            conflict_warnings = [
                call for call in warning_calls
                if "conflict" in str(call).lower()
            ]
            assert len(conflict_warnings) > 0, "Expected conflict warning to be logged"

    @pytest.mark.asyncio
    async def test_push_to_notion_uses_consolidated_why_now(self):
        """_push_to_notion should use consolidated why_now_parts when available."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig
        from verification.verification_gate_v2 import (
            VerificationResult, PushDecision, VerificationStatus
        )

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1, signal_type="github_spike", source_api="github",
                canonical_key="domain:acme.ai", company_name="Acme",
                confidence=0.8, raw_data={"why_now": "GitHub trending"},
                detected_at=now, created_at=now,
            ),
        ]

        store = AsyncMock()
        store.enqueue_notion_write = AsyncMock(return_value="outbox-123")

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = AsyncMock()
        pipeline._initialized = True

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=now,
            latest_detected_at=now,
            why_now_parts=["Trending on GitHub", "New SEC filing", "Product Hunt launch"],
        )

        verification = VerificationResult(
            decision=PushDecision.AUTO_PUSH,
            verification_status=VerificationStatus.MULTI_SOURCE,
            confidence_score=0.8,
            confidence_breakdown={},
            reason="High confidence",
            suggested_status="Source",
            signals_used=["1"],
            sources_checked=["github"],
            verification_details=[],
        )

        await pipeline._push_to_notion(signals, verification, consolidated=consolidated)

        call_args = store.enqueue_notion_write.call_args
        payload = call_args.kwargs.get("payload") or call_args[1].get("payload")

        # Why now should be from consolidated (joined with "; ")
        assert "Trending on GitHub" in payload["prospect"]["why_now"]
        assert "New SEC filing" in payload["prospect"]["why_now"]
