"""
Tests for SignalStore CRUD operations.

Covers:
- save_signal: Create new signals
- get_signal: Retrieve by ID
- get_pending_signals: Get unprocessed signals
- get_signals_for_company: Get by canonical key
- get_signals_for_company_by_name: Get by company name
- is_duplicate: Check for duplicates
- save_pipeline_run: Track pipeline runs
- get_pipeline_runs: Retrieve run history
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, StoredSignal


# =============================================================================
# SAVE SIGNAL TESTS
# =============================================================================

class TestSaveSignal:
    """Tests for save_signal method."""

    @pytest.mark.asyncio
    async def test_save_signal_returns_id(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """save_signal should return integer ID."""
        signal_id = await store.save_signal(**sample_signal_data)

        assert isinstance(signal_id, int)
        assert signal_id > 0

    @pytest.mark.asyncio
    async def test_save_signal_stores_all_fields(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """All fields should be persisted correctly."""
        signal_id = await store.save_signal(**sample_signal_data)

        signal = await store.get_signal(signal_id)

        assert signal is not None
        assert signal.signal_type == sample_signal_data["signal_type"]
        assert signal.source_api == sample_signal_data["source_api"]
        assert signal.canonical_key == sample_signal_data["canonical_key"]
        assert signal.company_name == sample_signal_data["company_name"]
        assert signal.confidence == sample_signal_data["confidence"]
        assert signal.raw_data == sample_signal_data["raw_data"]

    @pytest.mark.asyncio
    async def test_save_signal_with_minimal_data(self, store: SignalStore):
        """Save with only required fields should work."""
        signal_id = await store.save_signal(
            signal_type="funding",
            source_api="sec_edgar",
            canonical_key="ein:999999999",
            confidence=0.5,
            raw_data={},
        )

        signal = await store.get_signal(signal_id)
        assert signal is not None
        assert signal.company_name is None

    @pytest.mark.asyncio
    async def test_save_signal_with_raw_data_json(self, store: SignalStore):
        """JSON serialization for raw_data should work."""
        complex_data = {
            "nested": {"key": "value"},
            "array": [1, 2, 3],
            "unicode": "Hello \u4e16\u754c",
        }

        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test_api",
            canonical_key="domain:test.com",
            confidence=0.6,
            raw_data=complex_data,
        )

        signal = await store.get_signal(signal_id)
        assert signal.raw_data == complex_data

    @pytest.mark.asyncio
    async def test_save_signal_creates_processing_record(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Saving a signal should also create a pending processing record."""
        signal_id = await store.save_signal(**sample_signal_data)

        signal = await store.get_signal(signal_id)
        assert signal.processing_status == "pending"

    @pytest.mark.asyncio
    async def test_save_signal_with_detected_at(self, store: SignalStore):
        """Custom detected_at should be used."""
        custom_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

        signal_id = await store.save_signal(
            signal_type="funding",
            source_api="sec_edgar",
            canonical_key="ein:111111111",
            confidence=0.7,
            raw_data={},
            detected_at=custom_time,
        )

        signal = await store.get_signal(signal_id)
        assert signal.detected_at == custom_time

    @pytest.mark.asyncio
    async def test_save_signal_duplicate_requires_new_detected_at(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Duplicate signals should require different detected_at timestamp."""
        # First save
        id1 = await store.save_signal(**sample_signal_data)

        # Second save with same data gets new auto-generated detected_at (different millisecond)
        # This tests that we need different timestamps for same company/type combo
        id2 = await store.save_signal(**sample_signal_data)

        # Both should succeed since detected_at differs
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_save_signal_different_detected_at_allowed(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Same signal with different detected_at should be allowed."""
        signal_id1 = await store.save_signal(**sample_signal_data)

        # Different detected_at should work
        sample_signal_data["detected_at"] = datetime.now(timezone.utc) + timedelta(days=1)
        signal_id2 = await store.save_signal(**sample_signal_data)

        assert signal_id1 != signal_id2


# =============================================================================
# GET SIGNAL TESTS
# =============================================================================

class TestGetSignal:
    """Tests for get_signal method."""

    @pytest.mark.asyncio
    async def test_get_signal_returns_stored_signal(self, store_with_signals: SignalStore):
        """get_signal should return StoredSignal object."""
        signal = await store_with_signals.get_signal(1)

        assert signal is not None
        assert isinstance(signal, StoredSignal)

    @pytest.mark.asyncio
    async def test_get_signal_not_found_returns_none(self, store: SignalStore):
        """Non-existent ID should return None."""
        signal = await store.get_signal(99999)

        assert signal is None

    @pytest.mark.asyncio
    async def test_get_signal_includes_processing_info(self, store_with_signals: SignalStore):
        """Signal should include processing status info."""
        signal = await store_with_signals.get_signal(1)

        assert signal.processing_status is not None
        assert signal.processing_status == "pending"

    @pytest.mark.asyncio
    async def test_get_signal_parses_raw_data(self, store_with_signals: SignalStore):
        """raw_data should be parsed from JSON."""
        signal = await store_with_signals.get_signal(1)

        assert isinstance(signal.raw_data, dict)


# =============================================================================
# GET PENDING SIGNALS TESTS
# =============================================================================

class TestGetPendingSignals:
    """Tests for get_pending_signals method."""

    @pytest.mark.asyncio
    async def test_get_pending_signals_returns_pending_only(self, store_with_signals: SignalStore):
        """Should only return signals with pending status."""
        pending = await store_with_signals.get_pending_signals()

        assert len(pending) == 2
        for signal in pending:
            assert signal.processing_status == "pending"

    @pytest.mark.asyncio
    async def test_get_pending_signals_respects_limit(self, store_with_signals: SignalStore):
        """Limit parameter should be respected."""
        pending = await store_with_signals.get_pending_signals(limit=1)

        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_get_pending_signals_empty_db(self, store: SignalStore):
        """Empty DB should return empty list."""
        pending = await store.get_pending_signals()

        assert pending == []

    @pytest.mark.asyncio
    async def test_get_pending_signals_excludes_pushed(self, store_with_signals: SignalStore):
        """Pushed signals should not be returned."""
        await store_with_signals.mark_pushed(1, "notion-page-123")

        pending = await store_with_signals.get_pending_signals()

        assert len(pending) == 1
        assert pending[0].id != 1

    @pytest.mark.asyncio
    async def test_get_pending_signals_excludes_rejected(self, store_with_signals: SignalStore):
        """Rejected signals should not be returned."""
        await store_with_signals.mark_rejected(1, "Not a fit")

        pending = await store_with_signals.get_pending_signals()

        assert len(pending) == 1
        assert pending[0].id != 1

    @pytest.mark.asyncio
    async def test_get_pending_signals_filter_by_type(self, store_with_signals: SignalStore):
        """Should filter by signal_type."""
        pending = await store_with_signals.get_pending_signals(signal_type="funding")

        assert len(pending) == 1
        assert pending[0].signal_type == "funding"


# =============================================================================
# GET SIGNALS FOR COMPANY TESTS
# =============================================================================

class TestGetSignalsForCompany:
    """Tests for get_signals_for_company method."""

    @pytest.mark.asyncio
    async def test_get_signals_for_company_by_key(self, store_with_signals: SignalStore):
        """Should return signals matching canonical key."""
        signals = await store_with_signals.get_signals_for_company("ein:123456789")

        assert len(signals) == 1
        assert signals[0].canonical_key == "ein:123456789"

    @pytest.mark.asyncio
    async def test_get_signals_for_company_not_found(self, store_with_signals: SignalStore):
        """Non-existent key should return empty list."""
        signals = await store_with_signals.get_signals_for_company("domain:nonexistent.com")

        assert signals == []

    @pytest.mark.asyncio
    async def test_get_signals_for_company_multiple(self, store: SignalStore):
        """Multiple signals for same company should all be returned."""
        await store.save_signal(
            signal_type="funding",
            source_api="sec_edgar",
            canonical_key="domain:multi.com",
            company_name="Multi Inc",
            confidence=0.7,
            raw_data={},
        )
        await store.save_signal(
            signal_type="launch",
            source_api="product_hunt",
            canonical_key="domain:multi.com",
            company_name="Multi Inc",
            confidence=0.6,
            raw_data={},
        )

        signals = await store.get_signals_for_company("domain:multi.com")

        assert len(signals) == 2


class TestGetSignalsForCompanyByName:
    """Tests for get_signals_for_company_by_name method."""

    @pytest.mark.asyncio
    async def test_get_signals_for_company_by_name(self, store_with_signals: SignalStore):
        """Should return signals matching company name."""
        signals = await store_with_signals.get_signals_for_company_by_name("Acme Corp")

        assert len(signals) == 1
        assert signals[0]["company_name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_get_signals_for_company_by_name_case_sensitive(self, store_with_signals: SignalStore):
        """Name search should be case-sensitive."""
        signals = await store_with_signals.get_signals_for_company_by_name("acme corp")

        assert signals == []  # No match - case sensitive


# =============================================================================
# IS DUPLICATE TESTS
# =============================================================================

class TestIsDuplicate:
    """Tests for is_duplicate method."""

    @pytest.mark.asyncio
    async def test_is_duplicate_true_for_existing_key(self, store_with_signals: SignalStore):
        """Should return True for existing canonical key."""
        is_dup = await store_with_signals.is_duplicate("ein:123456789")

        assert is_dup is True

    @pytest.mark.asyncio
    async def test_is_duplicate_false_for_new_key(self, store_with_signals: SignalStore):
        """Should return False for non-existent key."""
        is_dup = await store_with_signals.is_duplicate("domain:newcompany.com")

        assert is_dup is False

    @pytest.mark.asyncio
    async def test_is_duplicate_empty_db(self, store: SignalStore):
        """Should return False on empty database."""
        is_dup = await store.is_duplicate("domain:anything.com")

        assert is_dup is False


# =============================================================================
# PIPELINE RUN TESTS
# =============================================================================

class TestPipelineRuns:
    """Tests for pipeline run tracking."""

    @pytest.mark.asyncio
    async def test_save_pipeline_run_stores_stats(self, store: SignalStore):
        """Pipeline run should be stored with stats."""
        from workflows.pipeline import PipelineStats

        stats = PipelineStats(
            collectors_run=3,
            collectors_succeeded=3,
            collectors_failed=0,
            signals_collected=25,
            signals_stored=20,
            signals_deduplicated=5,
        )
        stats.complete()  # Mark as completed

        run_id = await store.save_pipeline_run(stats)

        # Verify stored
        runs = await store.get_pipeline_runs(limit=1)
        assert len(runs) == 1
        assert runs[0]["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_get_pipeline_runs_ordered_by_date(self, store: SignalStore):
        """Runs should be ordered by date, most recent first."""
        from workflows.pipeline import PipelineStats
        import asyncio

        run_ids = []
        for i in range(3):
            stats = PipelineStats(
                started_at=datetime.now(timezone.utc),
                collectors_run=1,
            )
            stats.complete()
            run_id = await store.save_pipeline_run(stats)
            run_ids.append(run_id)
            await asyncio.sleep(0.01)  # Small delay to ensure different timestamps

        runs = await store.get_pipeline_runs(limit=3)

        # Most recent should be first
        assert len(runs) == 3
        assert runs[0]["run_id"] == run_ids[2]  # Last saved is most recent


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and unusual inputs."""

    @pytest.mark.asyncio
    async def test_save_signal_with_unicode_company_name(self, store: SignalStore):
        """Unicode company names should be handled."""
        signal_id = await store.save_signal(
            signal_type="funding",
            source_api="sec_edgar",
            canonical_key="domain:unicode.com",
            company_name="\u4e2d\u6587\u516c\u53f8",  # Chinese characters
            confidence=0.7,
            raw_data={},
        )

        signal = await store.get_signal(signal_id)
        assert signal.company_name == "\u4e2d\u6587\u516c\u53f8"

    @pytest.mark.asyncio
    async def test_save_signal_with_large_raw_data(self, store: SignalStore):
        """Large raw_data payloads should be handled."""
        large_data = {"items": [f"item_{i}" for i in range(1000)]}

        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test_api",
            canonical_key="domain:large.com",
            confidence=0.5,
            raw_data=large_data,
        )

        signal = await store.get_signal(signal_id)
        assert len(signal.raw_data["items"]) == 1000

    @pytest.mark.asyncio
    async def test_save_signal_with_null_fields(self, store: SignalStore):
        """Null optional fields should be handled."""
        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test_api",
            canonical_key="domain:nulls.com",
            company_name=None,
            confidence=0.5,
            raw_data={},
        )

        signal = await store.get_signal(signal_id)
        assert signal.company_name is None

    @pytest.mark.asyncio
    async def test_confidence_bounds_preserved(self, store: SignalStore):
        """Confidence values at boundaries should work."""
        # Minimum confidence
        id1 = await store.save_signal(
            signal_type="test",
            source_api="test_api",
            canonical_key="domain:min.com",
            confidence=0.0,
            raw_data={},
        )

        # Maximum confidence
        id2 = await store.save_signal(
            signal_type="test",
            source_api="test_api",
            canonical_key="domain:max.com",
            confidence=1.0,
            raw_data={},
        )

        signal1 = await store.get_signal(id1)
        signal2 = await store.get_signal(id2)

        assert signal1.confidence == 0.0
        assert signal2.confidence == 1.0

    @pytest.mark.asyncio
    async def test_empty_raw_data(self, store: SignalStore):
        """Empty raw_data dict should be handled."""
        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test_api",
            canonical_key="domain:empty.com",
            confidence=0.5,
            raw_data={},
        )

        signal = await store.get_signal(signal_id)
        assert signal.raw_data == {}
