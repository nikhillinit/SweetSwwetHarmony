"""
Tests for Exit Predictions Storage Layer.

TDD: Tests for migration 6 and storage methods.
"""

import json
import pytest
from datetime import datetime, timedelta

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


class TestMigration6:
    """Tests for migration 6 - exit_predictions table."""

    @pytest.mark.asyncio
    async def test_migration_creates_exit_predictions_table(self, tmp_path):
        """Migration 6 creates exit_predictions table."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            # Check table exists
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='exit_predictions'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "exit_predictions"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_migration_creates_indexes(self, tmp_path):
        """Migration 6 creates required indexes."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_exit_pred_%'"
            )
            indexes = {row[0] for row in await cursor.fetchall()}

            expected = {
                "idx_exit_pred_canonical",
                "idx_exit_pred_deal_quality",
                "idx_exit_pred_recommendation",
                "idx_exit_pred_percentile",
            }
            assert expected.issubset(indexes)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_schema_version_at_least_6(self, tmp_path):
        """Schema version should be at least 6 (when exit_predictions was added)."""
        assert CURRENT_SCHEMA_VERSION >= 6


class TestStoreExitPrediction:
    """Tests for store_exit_prediction method."""

    @pytest.mark.asyncio
    async def test_store_prediction(self, tmp_path):
        """Can store an exit prediction."""
        from utils.exit_predictor import ExitPrediction, ExitEvidence

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            prediction = ExitPrediction(
                canonical_key="domain:test.com",
                thesis_fit=0.8,
                founder_score=0.7,
                traction_score=0.6,
                funding_score=0.5,
                velocity_score=0.5,
                age_score=0.9,
                investor_centrality=0.5,
                patent_count=0.0,
                deal_quality_score=0.65,
                percentile_rank=None,
                exit_probability=0.35,
                confidence="medium",
                recommendation="tracking",
                evidence=[
                    ExitEvidence(signal_id=1, factor="thesis_fit", value=0.8),
                    ExitEvidence(signal_id=1, factor="age_score", value=0.9),
                ],
            )

            row_id = await store.store_exit_prediction(prediction)
            assert row_id > 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_store_prediction_upsert(self, tmp_path):
        """Storing prediction with same canonical_key updates existing."""
        from utils.exit_predictor import ExitPrediction

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            # First prediction
            pred1 = ExitPrediction(
                canonical_key="domain:test.com",
                thesis_fit=0.6,
                founder_score=0.5,
                traction_score=0.5,
                funding_score=0.5,
                velocity_score=0.5,
                age_score=0.5,
                investor_centrality=0.5,
                patent_count=0.0,
                deal_quality_score=0.5,
                percentile_rank=None,
                exit_probability=0.3,
                confidence="low",
                recommendation="hold",
            )
            await store.store_exit_prediction(pred1)

            # Updated prediction
            pred2 = ExitPrediction(
                canonical_key="domain:test.com",  # Same key
                thesis_fit=0.9,  # Changed
                founder_score=0.5,
                traction_score=0.5,
                funding_score=0.5,
                velocity_score=0.5,
                age_score=0.5,
                investor_centrality=0.5,
                patent_count=0.0,
                deal_quality_score=0.7,  # Changed
                percentile_rank=None,
                exit_probability=0.4,
                confidence="high",  # Changed
                recommendation="source",  # Changed
            )
            await store.store_exit_prediction(pred2)

            # Verify only one row and it's updated
            result = await store.get_exit_prediction("domain:test.com")
            assert result is not None
            assert result["thesis_fit"] == 0.9
            assert result["confidence"] == "high"
            assert result["recommendation"] == "source"
        finally:
            await store.close()


class TestGetExitPrediction:
    """Tests for get_exit_prediction method."""

    @pytest.mark.asyncio
    async def test_get_existing_prediction(self, tmp_path):
        """Can retrieve stored prediction."""
        from utils.exit_predictor import ExitPrediction

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            prediction = ExitPrediction(
                canonical_key="domain:acme.ai",
                thesis_fit=0.75,
                founder_score=0.8,
                traction_score=0.65,
                funding_score=0.7,
                velocity_score=0.6,
                age_score=0.85,
                investor_centrality=0.5,
                patent_count=0.0,
                deal_quality_score=0.72,
                percentile_rank=None,
                exit_probability=0.38,
                confidence="high",
                recommendation="source",
            )
            await store.store_exit_prediction(prediction)

            result = await store.get_exit_prediction("domain:acme.ai")

            assert result is not None
            assert result["canonical_key"] == "domain:acme.ai"
            assert result["thesis_fit"] == 0.75
            assert result["confidence"] == "high"
            assert result["recommendation"] == "source"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_nonexistent_prediction(self, tmp_path):
        """Returns None for nonexistent canonical key."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            result = await store.get_exit_prediction("domain:nonexistent.com")
            assert result is None
        finally:
            await store.close()


class TestGetAllExitPredictions:
    """Tests for get_all_exit_predictions method."""

    @pytest.mark.asyncio
    async def test_get_all_predictions_ordered(self, tmp_path):
        """Returns predictions ordered by deal_quality_score DESC."""
        from utils.exit_predictor import ExitPrediction

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            # Store 3 predictions with different quality scores
            for name, quality in [("low", 0.3), ("high", 0.8), ("medium", 0.5)]:
                pred = ExitPrediction(
                    canonical_key=f"domain:{name}.com",
                    thesis_fit=0.5,
                    founder_score=0.5,
                    traction_score=0.5,
                    funding_score=0.5,
                    velocity_score=0.5,
                    age_score=0.5,
                    investor_centrality=0.5,
                    patent_count=0.0,
                    deal_quality_score=quality,
                    percentile_rank=None,
                    exit_probability=0.3,
                    confidence="medium",
                    recommendation="tracking",
                )
                await store.store_exit_prediction(pred)

            results = await store.get_all_exit_predictions()

            assert len(results) == 3
            # Should be ordered by deal_quality_score DESC
            assert results[0]["deal_quality_score"] == 0.8
            assert results[1]["deal_quality_score"] == 0.5
            assert results[2]["deal_quality_score"] == 0.3
        finally:
            await store.close()


class TestUpdatePercentileRank:
    """Tests for update_exit_prediction_percentile method."""

    @pytest.mark.asyncio
    async def test_update_percentile(self, tmp_path):
        """Can update percentile rank."""
        from utils.exit_predictor import ExitPrediction

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            prediction = ExitPrediction(
                canonical_key="domain:test.com",
                thesis_fit=0.7,
                founder_score=0.5,
                traction_score=0.5,
                funding_score=0.5,
                velocity_score=0.5,
                age_score=0.5,
                investor_centrality=0.5,
                patent_count=0.0,
                deal_quality_score=0.6,
                percentile_rank=None,
                exit_probability=0.35,
                confidence="medium",
                recommendation="tracking",
            )
            await store.store_exit_prediction(prediction)

            # Update percentile
            success = await store.update_exit_prediction_percentile(
                "domain:test.com", 75
            )
            assert success is True

            # Verify update
            result = await store.get_exit_prediction("domain:test.com")
            assert result["percentile_rank"] == 75
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_percentile_nonexistent(self, tmp_path):
        """Returns False for nonexistent canonical key."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            success = await store.update_exit_prediction_percentile(
                "domain:nonexistent.com", 50
            )
            assert success is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_unique_constraint_on_canonical_key(self, tmp_path):
        """Canonical key has unique constraint."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            # Check table schema for UNIQUE constraint
            cursor = await store._db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='exit_predictions'"
            )
            row = await cursor.fetchone()
            assert "UNIQUE" in row[0] or "canonical_key TEXT NOT NULL UNIQUE" in row[0]
        finally:
            await store.close()


class TestExitPredictorBatch:
    """Tests for ExitPredictorBatch percentile computation."""

    @pytest.mark.asyncio
    async def test_compute_percentiles_empty(self, tmp_path):
        """Returns 0 for empty database."""
        from utils.exit_predictor_batch import ExitPredictorBatch

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            batch = ExitPredictorBatch(store)
            updated = await batch.compute_percentiles()
            assert updated == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_compute_percentiles_single(self, tmp_path):
        """Single prediction gets 50th percentile."""
        from utils.exit_predictor import ExitPrediction
        from utils.exit_predictor_batch import ExitPredictorBatch

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            pred = ExitPrediction(
                canonical_key="domain:only.com",
                thesis_fit=0.5,
                founder_score=0.5,
                traction_score=0.5,
                funding_score=0.5,
                velocity_score=0.5,
                age_score=0.5,
                investor_centrality=0.5,
                patent_count=0.0,
                deal_quality_score=0.5,
                percentile_rank=None,
                exit_probability=0.3,
                confidence="medium",
                recommendation="tracking",
            )
            await store.store_exit_prediction(pred)

            batch = ExitPredictorBatch(store)
            updated = await batch.compute_percentiles()

            assert updated == 1

            result = await store.get_exit_prediction("domain:only.com")
            assert result["percentile_rank"] == 50
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_compute_percentiles_multiple(self, tmp_path):
        """Multiple predictions ranked by deal_quality_score."""
        from utils.exit_predictor import ExitPrediction
        from utils.exit_predictor_batch import ExitPredictorBatch

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            # Create 5 predictions with different quality scores
            for i, quality in enumerate([0.9, 0.7, 0.5, 0.3, 0.1]):
                pred = ExitPrediction(
                    canonical_key=f"domain:test{i}.com",
                    thesis_fit=0.5,
                    founder_score=0.5,
                    traction_score=0.5,
                    funding_score=0.5,
                    velocity_score=0.5,
                    age_score=0.5,
                    investor_centrality=0.5,
                    patent_count=0.0,
                    deal_quality_score=quality,
                    percentile_rank=None,
                    exit_probability=0.3,
                    confidence="medium",
                    recommendation="tracking",
                )
                await store.store_exit_prediction(pred)

            batch = ExitPredictorBatch(store)
            updated = await batch.compute_percentiles()

            assert updated == 5

            # Verify percentiles are assigned correctly
            # Highest quality (0.9) should get highest percentile
            high = await store.get_exit_prediction("domain:test0.com")
            assert high["percentile_rank"] == 99

            # Lowest quality (0.1) should get lowest percentile
            low = await store.get_exit_prediction("domain:test4.com")
            assert low["percentile_rank"] == 1

            # Middle quality should be around 50
            mid = await store.get_exit_prediction("domain:test2.com")
            assert 40 <= mid["percentile_rank"] <= 60
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_compute_percentiles_is_idempotent(self, tmp_path):
        """Running batch multiple times produces same results."""
        from utils.exit_predictor import ExitPrediction
        from utils.exit_predictor_batch import ExitPredictorBatch

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            for i, quality in enumerate([0.8, 0.4]):
                pred = ExitPrediction(
                    canonical_key=f"domain:test{i}.com",
                    thesis_fit=0.5,
                    founder_score=0.5,
                    traction_score=0.5,
                    funding_score=0.5,
                    velocity_score=0.5,
                    age_score=0.5,
                    investor_centrality=0.5,
                    patent_count=0.0,
                    deal_quality_score=quality,
                    percentile_rank=None,
                    exit_probability=0.3,
                    confidence="medium",
                    recommendation="tracking",
                )
                await store.store_exit_prediction(pred)

            batch = ExitPredictorBatch(store)

            # Run twice
            await batch.compute_percentiles()
            await batch.compute_percentiles()

            # Results should be same
            high = await store.get_exit_prediction("domain:test0.com")
            low = await store.get_exit_prediction("domain:test1.com")

            assert high["percentile_rank"] == 99
            assert low["percentile_rank"] == 1
        finally:
            await store.close()
