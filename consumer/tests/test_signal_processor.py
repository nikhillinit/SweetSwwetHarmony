"""Tests for SignalProcessor - orchestrates two-stage signal gating."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from consumer.signal_processor import (
    SignalProcessor,
    ProcessorConfig,
    ProcessingResult,
    ProcessingStats,
)
from consumer.trigger_gate import TriggerResult, ChangeType
from consumer.llm_classifier_v2 import ClassificationResult, ClassificationLabel


class TestSignalProcessor:
    """Test suite for SignalProcessor."""

    @pytest.mark.asyncio
    async def test_processor_skips_signal_without_previous_snapshot(self):
        """Signals without previous snapshot should skip gating."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        signal = {
            "id": "sig_1",
            "signal_type": "github_spike",
            "raw_data": {"description": "New startup"},
        }

        result = await processor.process_signal(signal)

        assert result.gating_skipped is True
        assert result.skip_reason == "no_previous_snapshot"

    @pytest.mark.asyncio
    async def test_processor_uses_trigger_gate(self):
        """Processor should use TriggerGate to filter signals."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        signal = {
            "id": "sig_1",
            "signal_type": "github_spike",
            "raw_data": {
                "description": "Enterprise B2B platform",
                "_previous_snapshot": {"description": "Consumer fitness app"},
            },
        }

        # Mock trigger gate to return should_trigger=True
        with patch.object(processor.trigger_gate, "should_classify") as mock_trigger:
            mock_trigger.return_value = TriggerResult(
                should_trigger=True,
                change_types=[ChangeType.DESCRIPTION_CHANGE],
                trigger_reason="Description changed 45%",
            )

            # Mock classifier to avoid actual LLM call
            with patch.object(processor.classifier, "classify") as mock_classify:
                mock_classify.return_value = ClassificationResult(
                    schema_version="v1",
                    label=ClassificationLabel.PIVOT,
                    confidence=0.85,
                    rationale="B2C to B2B change",
                    input_hash="test",
                )

                result = await processor.process_signal(signal)

        mock_trigger.assert_called_once()
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_processor_skips_llm_when_not_triggered(self):
        """Processor should skip LLM when trigger gate says no."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        signal = {
            "id": "sig_1",
            "signal_type": "github_spike",
            "raw_data": {
                "description": "Fitness application",
                "_previous_snapshot": {"description": "Fitness app"},  # Minor change
            },
        }

        # Mock trigger gate to return should_trigger=False
        with patch.object(processor.trigger_gate, "should_classify") as mock_trigger:
            mock_trigger.return_value = TriggerResult(should_trigger=False)

            # Mock classifier to verify it's NOT called
            with patch.object(processor.classifier, "classify") as mock_classify:
                result = await processor.process_signal(signal)

                mock_classify.assert_not_called()

        assert result.triggered is False
        assert result.classification is None

    @pytest.mark.asyncio
    async def test_processor_enriches_signal_with_classification(self):
        """Processor should add classification to signal."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        signal = {
            "id": "sig_1",
            "signal_type": "github_spike",
            "raw_data": {
                "description": "Enterprise wellness platform",
                "_previous_snapshot": {"description": "Consumer fitness app"},
            },
        }

        with patch.object(processor.trigger_gate, "should_classify") as mock_trigger:
            mock_trigger.return_value = TriggerResult(
                should_trigger=True,
                change_types=[ChangeType.DESCRIPTION_CHANGE],
                trigger_reason="Description changed 50%",
            )

            with patch.object(processor.classifier, "classify") as mock_classify:
                mock_classify.return_value = ClassificationResult(
                    schema_version="v1",
                    label=ClassificationLabel.PIVOT,
                    confidence=0.85,
                    rationale="Shifted from B2C to B2B",
                    input_hash="test",
                )

                result = await processor.process_signal(signal)

        assert result.classification is not None
        assert result.classification.label == ClassificationLabel.PIVOT
        assert result.classification.confidence == 0.85

    @pytest.mark.asyncio
    async def test_process_batch_returns_stats(self):
        """Processing a batch should return aggregated stats."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        signals = [
            {"id": "sig_1", "raw_data": {"description": "App 1"}},  # No previous
            {"id": "sig_2", "raw_data": {"description": "App 2"}},  # No previous
        ]

        stats = await processor.process_batch(signals)

        assert isinstance(stats, ProcessingStats)
        assert stats.total == 2
        assert stats.skipped == 2  # Both skipped (no previous snapshot)

    @pytest.mark.asyncio
    async def test_processor_tracks_llm_calls(self):
        """Processor should track how many LLM calls were made."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        signals = [
            {
                "id": "sig_1",
                "raw_data": {
                    "description": "Enterprise platform",
                    "_previous_snapshot": {"description": "Consumer app"},
                },
            },
            {
                "id": "sig_2",
                "raw_data": {
                    "description": "Tiny change",
                    "_previous_snapshot": {"description": "Tiny update"},  # Minor
                },
            },
        ]

        # First signal triggers, second doesn't
        trigger_responses = [
            TriggerResult(
                should_trigger=True,
                change_types=[ChangeType.DESCRIPTION_CHANGE],
                trigger_reason="Major change",
            ),
            TriggerResult(should_trigger=False),
        ]

        with patch.object(
            processor.trigger_gate, "should_classify", side_effect=trigger_responses
        ):
            with patch.object(processor.classifier, "classify") as mock_classify:
                mock_classify.return_value = ClassificationResult(
                    schema_version="v1",
                    label=ClassificationLabel.PIVOT,
                    confidence=0.9,
                    rationale="Test",
                    input_hash="test",
                )

                stats = await processor.process_batch(signals)

        assert stats.triggered == 1
        assert stats.llm_calls == 1
        assert stats.not_triggered == 1

    @pytest.mark.asyncio
    async def test_process_pending_with_gating_fetches_from_store(self):
        """process_pending_with_gating should fetch signals from store."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        # Create mock store
        mock_store = AsyncMock()
        mock_store.get_pending_signals = AsyncMock(return_value=[])

        stats = await processor.process_pending_with_gating(mock_store)

        mock_store.get_pending_signals.assert_called_once_with(
            limit=None, signal_type=None
        )
        assert stats.total == 0

    @pytest.mark.asyncio
    async def test_process_pending_with_gating_passes_filters(self):
        """process_pending_with_gating should pass limit and signal_type to store."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        mock_store = AsyncMock()
        mock_store.get_pending_signals = AsyncMock(return_value=[])

        await processor.process_pending_with_gating(
            mock_store, limit=50, signal_type="github_spike"
        )

        mock_store.get_pending_signals.assert_called_once_with(
            limit=50, signal_type="github_spike"
        )

    @pytest.mark.asyncio
    async def test_process_pending_with_gating_converts_stored_signals(self):
        """process_pending_with_gating should convert StoredSignal to dict format."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        # Create mock StoredSignal objects
        mock_signal = MagicMock()
        mock_signal.id = 123
        mock_signal.signal_type = "github_spike"
        mock_signal.source_api = "github"
        mock_signal.canonical_key = "domain:example.com"
        mock_signal.company_name = "Example Corp"
        mock_signal.confidence = 0.75
        mock_signal.raw_data = {"description": "Test app"}
        mock_signal.detected_at = datetime(2025, 1, 1)

        mock_store = AsyncMock()
        mock_store.get_pending_signals = AsyncMock(return_value=[mock_signal])

        # Process and verify batch gets called with converted signal
        with patch.object(processor, "process_batch") as mock_batch:
            mock_batch.return_value = ProcessingStats(total=1, skipped=1)

            stats = await processor.process_pending_with_gating(mock_store)

            mock_batch.assert_called_once()
            signal_dicts = mock_batch.call_args[0][0]
            assert len(signal_dicts) == 1
            assert signal_dicts[0]["id"] == "123"
            assert signal_dicts[0]["signal_type"] == "github_spike"
            assert signal_dicts[0]["canonical_key"] == "domain:example.com"

    @pytest.mark.asyncio
    async def test_process_pending_with_gating_returns_stats(self):
        """process_pending_with_gating should return ProcessingStats."""
        config = ProcessorConfig()
        processor = SignalProcessor(config)

        mock_signal = MagicMock()
        mock_signal.id = 1
        mock_signal.signal_type = "github_spike"
        mock_signal.source_api = "github"
        mock_signal.canonical_key = "domain:test.com"
        mock_signal.company_name = "Test"
        mock_signal.confidence = 0.8
        mock_signal.raw_data = {}
        mock_signal.detected_at = datetime.utcnow()

        mock_store = AsyncMock()
        mock_store.get_pending_signals = AsyncMock(return_value=[mock_signal])

        stats = await processor.process_pending_with_gating(mock_store)

        assert isinstance(stats, ProcessingStats)
        assert stats.total == 1
        assert stats.skipped == 1  # No previous snapshot
