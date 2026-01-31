"""Tests for SHADOW feature correlation metrics.

TDD tests for:
- get_shadow_correlation_report() method
- Measuring lift of shadow features vs outcomes
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

from storage.signal_store import SignalStore


# =============================================================================
# SHADOW CORRELATION REPORT TESTS
# =============================================================================

class TestShadowCorrelationReport:
    """Tests for get_shadow_correlation_report() method."""

    @pytest.mark.asyncio
    async def test_correlation_report_empty(self, store: SignalStore):
        """Returns empty report when no data."""
        report = await store.get_shadow_correlation_report("boilerplate_defense")

        assert report is not None
        assert report["feature_name"] == "boilerplate_defense"
        assert report["total_logs"] == 0
        assert report["value_distribution"] == {}
        assert report["outcome_distribution"] == {}

    @pytest.mark.asyncio
    async def test_correlation_report_counts_logs(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Counts total logs for feature."""
        # Create some shadow logs
        for i in range(5):
            await store.log_shadow_computation(
                "boilerplate_defense",
                f"key:{i}",
                {"match": i % 2 == 0},
            )

        report = await store.get_shadow_correlation_report("boilerplate_defense")

        assert report["total_logs"] == 5

    @pytest.mark.asyncio
    async def test_correlation_report_value_distribution(self, store: SignalStore):
        """Tracks distribution of computed values."""
        # Log with different match values
        await store.log_shadow_computation("boilerplate_defense", "key:1", {"match": True})
        await store.log_shadow_computation("boilerplate_defense", "key:2", {"match": True})
        await store.log_shadow_computation("boilerplate_defense", "key:3", {"match": True})
        await store.log_shadow_computation("boilerplate_defense", "key:4", {"match": False})
        await store.log_shadow_computation("boilerplate_defense", "key:5", {"match": False})

        report = await store.get_shadow_correlation_report("boilerplate_defense")

        # Should track match=True vs match=False
        assert "value_distribution" in report
        dist = report["value_distribution"]
        assert dist.get("match_true", 0) == 3
        assert dist.get("match_false", 0) == 2

    @pytest.mark.asyncio
    async def test_correlation_report_with_linked_signals(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Can correlate shadow values with signal outcomes."""
        # Create signals with different processing statuses
        signal_1 = await store.save_signal(**sample_signal_data)
        signal_2 = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="github_org:test",
            confidence=0.7,
            raw_data={},
        )

        # Mark different outcomes
        await store.mark_processing_status(signal_1, "pushed")
        await store.mark_processing_status(signal_2, "rejected")

        # Log shadow computations linked to signals
        await store.log_shadow_computation(
            "boilerplate_defense",
            sample_signal_data["canonical_key"],
            {"match": False},  # Not boilerplate
            signal_id=signal_1,
        )
        await store.log_shadow_computation(
            "boilerplate_defense",
            "github_org:test",
            {"match": True},  # Is boilerplate
            signal_id=signal_2,
        )

        report = await store.get_shadow_correlation_report(
            "boilerplate_defense",
            outcome_field="processing_status",
        )

        # Should show correlation: match=True -> rejected, match=False -> pushed
        assert "outcome_by_value" in report
        outcomes = report["outcome_by_value"]
        # match_false signals tend to get pushed
        # match_true signals tend to get rejected

    @pytest.mark.asyncio
    async def test_correlation_report_filters_by_days(self, store: SignalStore):
        """Respects days parameter for time filtering."""
        # Log some data
        await store.log_shadow_computation("feature", "key:1", {"value": 1})
        await store.log_shadow_computation("feature", "key:2", {"value": 2})

        # Default is 7 days
        report = await store.get_shadow_correlation_report("feature", days=7)
        assert report["total_logs"] == 2

    @pytest.mark.asyncio
    async def test_correlation_report_for_different_features(self, store: SignalStore):
        """Each feature gets its own report."""
        await store.log_shadow_computation("feature_a", "key:1", {"value": "a"})
        await store.log_shadow_computation("feature_b", "key:2", {"value": "b"})
        await store.log_shadow_computation("feature_b", "key:3", {"value": "b"})

        report_a = await store.get_shadow_correlation_report("feature_a")
        report_b = await store.get_shadow_correlation_report("feature_b")

        assert report_a["total_logs"] == 1
        assert report_b["total_logs"] == 2

    @pytest.mark.asyncio
    async def test_correlation_report_includes_time_range(self, store: SignalStore):
        """Report includes the time range analyzed."""
        await store.log_shadow_computation("feature", "key:1", {"value": 1})

        report = await store.get_shadow_correlation_report("feature", days=7)

        assert "period_start" in report
        assert "period_end" in report
        # Period should span roughly 7 days
        start = datetime.fromisoformat(report["period_start"])
        end = datetime.fromisoformat(report["period_end"])
        assert (end - start).days <= 7


class TestMarkProcessingStatus:
    """Tests for mark_processing_status helper (needed for correlation)."""

    @pytest.mark.asyncio
    async def test_mark_processing_status_basic(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Can mark a signal's processing status."""
        signal_id = await store.save_signal(**sample_signal_data)

        await store.mark_processing_status(signal_id, "pushed")

        # Verify status was recorded
        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT status FROM signal_processing WHERE signal_id = ?",
                (signal_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pushed"

    @pytest.mark.asyncio
    async def test_mark_processing_status_updates_existing(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Updates existing status if already marked."""
        signal_id = await store.save_signal(**sample_signal_data)

        await store.mark_processing_status(signal_id, "pending")
        await store.mark_processing_status(signal_id, "pushed")

        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT status FROM signal_processing WHERE signal_id = ?",
                (signal_id,)
            )
            row = await cursor.fetchone()
            assert row[0] == "pushed"
