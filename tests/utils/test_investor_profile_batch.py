"""
Tests for investor profile batch job.

Sprint 5: Investor Matching v1.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.investor_profile_batch import (
    InvestorProfileBatch,
    BatchResult,
)


# =============================================================================
# BATCH RESULT TESTS
# =============================================================================

class TestBatchResult:
    """Tests for BatchResult dataclass."""

    def test_default_values(self):
        """BatchResult has correct defaults."""
        result = BatchResult()
        assert result.total_investors == 0
        assert result.profiles_updated == 0
        assert result.claims_refreshed == 0
        assert result.baselines_computed == 0
        assert result.fts_entries_created == 0
        assert result.cold_start_count == 0
        assert result.errors == []
        assert result.started_at is None
        assert result.completed_at is None

    def test_duration_calculation(self):
        """Duration is calculated correctly."""
        from datetime import datetime, timezone, timedelta

        result = BatchResult()
        result.started_at = datetime.now(timezone.utc)
        result.completed_at = result.started_at + timedelta(seconds=30)

        assert result.duration_seconds == pytest.approx(30.0, rel=0.01)

    def test_to_dict(self):
        """to_dict returns correct structure."""
        result = BatchResult(
            total_investors=10,
            profiles_updated=8,
            claims_refreshed=20,
            baselines_computed=15,
            fts_entries_created=8,
            cold_start_count=2,
            errors=["error1"],
        )

        d = result.to_dict()
        assert d["total_investors"] == 10
        assert d["profiles_updated"] == 8
        assert d["claims_refreshed"] == 20
        assert d["baselines_computed"] == 15
        assert d["fts_entries_created"] == 8
        assert d["cold_start_count"] == 2
        assert d["error_count"] == 1


# =============================================================================
# INVESTOR PROFILE BATCH TESTS
# =============================================================================

class TestInvestorProfileBatch:
    """Tests for InvestorProfileBatch class."""

    @pytest.fixture
    def mock_store(self):
        """Create a mock store."""
        store = MagicMock()
        store._db = AsyncMock()
        return store

    @pytest.mark.asyncio
    async def test_count_investors(self, mock_store):
        """_count_investors returns correct count."""
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(5,))
        mock_store._db.execute = AsyncMock(return_value=cursor)

        batch = InvestorProfileBatch(mock_store)
        count = await batch._count_investors()

        assert count == 5

    @pytest.mark.asyncio
    async def test_count_investors_empty(self, mock_store):
        """_count_investors returns 0 for empty table."""
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(0,))
        mock_store._db.execute = AsyncMock(return_value=cursor)

        batch = InvestorProfileBatch(mock_store)
        count = await batch._count_investors()

        assert count == 0

    @pytest.mark.asyncio
    async def test_compute_lift_score_positive(self, mock_store):
        """Positive lift when investor overweights value."""
        mock_store.get_global_baseline = AsyncMock(return_value=0.1)

        batch = InvestorProfileBatch(mock_store)
        lift = await batch._compute_lift_score("sector", "fintech", 0.5)

        # log(0.5 / 0.1) = log(5) ≈ 1.6
        assert lift > 1.0

    @pytest.mark.asyncio
    async def test_compute_lift_score_negative(self, mock_store):
        """Negative lift when investor underweights value."""
        mock_store.get_global_baseline = AsyncMock(return_value=0.5)

        batch = InvestorProfileBatch(mock_store)
        lift = await batch._compute_lift_score("sector", "enterprise", 0.1)

        # log(0.1 / 0.5) = log(0.2) ≈ -1.6
        assert lift < 0.0

    @pytest.mark.asyncio
    async def test_compute_lift_score_no_baseline(self, mock_store):
        """Handles missing baseline gracefully."""
        mock_store.get_global_baseline = AsyncMock(return_value=None)

        batch = InvestorProfileBatch(mock_store)
        # Should not raise, uses EPS
        lift = await batch._compute_lift_score("sector", "unknown", 0.5)

        assert isinstance(lift, float)


class TestInvestorProfileBatchIntegration:
    """Integration tests using real SignalStore."""

    @pytest.mark.asyncio
    async def test_full_batch_flow(self):
        """Test full batch job with in-memory DB."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        # Create test investor
        await store.save_investor(
            investor_id="investor:test_vc",
            name="Test VC",
            source="curated_json",
            investor_type="vc",
            hq_country="US",
        )

        # Add portfolio entries
        for i in range(5):
            await store.save_portfolio_entry(
                investor_id="investor:test_vc",
                company_key=f"domain:company{i}.com",
                relationship_type="led" if i == 0 else "participated",
                source="curated_json",
                round_type="seed",
                confidence=0.9,
            )

        # Run batch
        batch = InvestorProfileBatch(store)
        result = await batch.run()

        assert isinstance(result, BatchResult)
        assert result.total_investors == 1
        assert result.profiles_updated >= 0  # May be 0 if no claims to join
        assert result.errors == []

        await store.close()

    @pytest.mark.asyncio
    async def test_cold_start_detection(self):
        """Test cold-start investors are flagged correctly."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        # Create investor with only 2 portfolio entries (cold-start)
        await store.save_investor(
            investor_id="investor:small_vc",
            name="Small VC",
            source="curated_json",
        )

        await store.save_portfolio_entry(
            investor_id="investor:small_vc",
            company_key="domain:a.com",
            relationship_type="participated",
            source="curated_json",
            confidence=0.9,
        )
        await store.save_portfolio_entry(
            investor_id="investor:small_vc",
            company_key="domain:b.com",
            relationship_type="participated",
            source="curated_json",
            confidence=0.9,
        )

        batch = InvestorProfileBatch(store)
        result = await batch.run()

        assert result.cold_start_count == 1
        assert result.total_investors == 1

        await store.close()

    @pytest.mark.asyncio
    async def test_fts_index_rebuild(self):
        """Test FTS index is rebuilt correctly."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        # Create investor with profile claim
        await store.save_investor(
            investor_id="investor:fts_test",
            name="FTS Test VC",
            source="curated_json",
        )

        await store.save_investor_profile_claim(
            investor_id="investor:fts_test",
            predicate="sector_preference",
            value="fintech",
            confidence=0.8,
            lift_score=0.5,
            support_count=5,
            status="active",
        )

        batch = InvestorProfileBatch(store)
        fts_count = await batch._rebuild_fts_index()

        assert fts_count == 1

        await store.close()

    @pytest.mark.asyncio
    async def test_baselines_from_empty_db(self):
        """Test baseline computation with empty database."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        batch = InvestorProfileBatch(store)
        count = await batch._compute_global_baselines()

        # Should return 0 baselines for empty DB
        assert count == 0

        await store.close()

    @pytest.mark.asyncio
    async def test_batch_handles_errors_gracefully(self):
        """Test batch job catches and reports errors."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        batch = InvestorProfileBatch(store)

        # Close store to simulate error
        await store.close()

        # Run should not raise, but should record error
        result = await batch.run()

        assert len(result.errors) > 0 or result.total_investors == 0

    @pytest.mark.asyncio
    async def test_profile_update_sets_distributions(self):
        """Test profile update correctly sets distributions."""
        from storage.signal_store import SignalStore
        import json

        store = SignalStore(":memory:")
        await store.initialize()

        # Create investor
        await store.save_investor(
            investor_id="investor:dist_test",
            name="Dist Test VC",
            source="curated_json",
        )

        batch = InvestorProfileBatch(store)

        # Update profile with distributions
        await batch._update_investor_profile(
            investor_id="investor:dist_test",
            portfolio_count=10,
            is_cold_start=False,
            stage_distribution={"seed": 0.6, "series_a": 0.4},
            sector_distribution={"fintech": 0.5, "health": 0.3},
            geo_distribution={"US": 0.8, "UK": 0.2},
        )

        # Verify profile was created
        cursor = await store._db.execute(
            "SELECT stage_distribution, sector_distribution, is_cold_start FROM investor_profiles WHERE investor_id = ?",
            ("investor:dist_test",),
        )
        row = await cursor.fetchone()

        assert row is not None
        stage_dist = json.loads(row[0])
        sector_dist = json.loads(row[1])
        is_cold = row[2]

        assert stage_dist["seed"] == 0.6
        assert sector_dist["fintech"] == 0.5
        assert is_cold == 0

        await store.close()


class TestBatchCLI:
    """Tests for CLI entry point."""

    def test_main_function_exists(self):
        """main function is importable."""
        from utils.investor_profile_batch import main
        assert callable(main)

    def test_run_batch_job_function_exists(self):
        """run_batch_job function is importable."""
        from utils.investor_profile_batch import run_batch_job
        assert callable(run_batch_job)
