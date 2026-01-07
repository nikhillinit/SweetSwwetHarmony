"""Tests for _process_company with gating integration."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from workflows.pipeline import DiscoveryPipeline, PipelineConfig
from storage.signal_store import StoredSignal


class TestProcessCompanyWithGating:
    """Test _process_company with gating enabled."""

    def _make_test_signal(
        self,
        signal_id: int = 1,
        canonical_key: str = "domain:test.com",
        has_previous: bool = False,
    ) -> StoredSignal:
        """Create a test StoredSignal."""
        raw_data = {
            "description": "Test company description",
            "name": "Test Company",
        }
        if has_previous:
            raw_data["_previous_snapshot"] = {
                "description": "Old description",
                "name": "Test Company",
            }

        now = datetime.utcnow()  # Use naive datetime to match verification gate
        return StoredSignal(
            id=signal_id,
            canonical_key=canonical_key,
            signal_type="github_trending",
            source_api="github",
            confidence=0.7,
            detected_at=now,
            created_at=now,
            raw_data=raw_data,
            company_name="Test Company",
        )

    @pytest.mark.asyncio
    async def test_process_company_uses_signal_processor_when_gating_enabled(self):
        """When use_gating=True, _process_company should use SignalProcessor."""
        config = PipelineConfig(
            use_gating=True,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Verify SignalProcessor is initialized
        assert pipeline._signal_processor is not None

        # Mock the store methods
        pipeline._store.check_suppression = AsyncMock(return_value=None)
        pipeline._store.mark_pushed = AsyncMock()

        # Create test signal
        signal = self._make_test_signal(has_previous=True)

        # Process with dry_run=True
        result = await pipeline._process_company([signal], dry_run=True)

        # Should get a result (not crash)
        assert "decision" in result

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_process_company_works_without_gating(self):
        """When use_gating=False, _process_company should work without SignalProcessor."""
        config = PipelineConfig(
            use_gating=False,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Verify SignalProcessor is NOT initialized
        assert pipeline._signal_processor is None

        # Mock the store methods
        pipeline._store.check_suppression = AsyncMock(return_value=None)
        pipeline._store.mark_pushed = AsyncMock()

        # Create test signal
        signal = self._make_test_signal()

        # Process with dry_run=True
        result = await pipeline._process_company([signal], dry_run=True)

        # Should get a result
        assert "decision" in result

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_gating_result_included_in_metadata_when_enabled(self):
        """When use_gating=True, gating result should be in result."""
        config = PipelineConfig(
            use_gating=True,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Mock store methods
        pipeline._store.check_suppression = AsyncMock(return_value=None)

        captured_metadata = {}
        async def capture_mark_pushed(signal_id, notion_page_id, metadata=None):
            captured_metadata.update(metadata or {})
        pipeline._store.mark_pushed = AsyncMock(side_effect=capture_mark_pushed)

        # Create test signal with previous snapshot (so gating can run)
        signal = self._make_test_signal(has_previous=True)

        # Process with dry_run=True
        result = await pipeline._process_company([signal], dry_run=True)

        # Check that result contains decision
        assert "decision" in result
        # When gating is enabled, result should indicate gating was applied
        assert result.get("gating_applied") is True

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_gating_not_applied_when_disabled(self):
        """When use_gating=False, gating_applied should be False."""
        config = PipelineConfig(
            use_gating=False,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        # Mock store methods
        pipeline._store.check_suppression = AsyncMock(return_value=None)
        pipeline._store.mark_pushed = AsyncMock()

        signal = self._make_test_signal()
        result = await pipeline._process_company([signal], dry_run=True)

        # Gating should not be applied
        assert result.get("gating_applied") is False

        await pipeline.close()
