"""
Tests for ExitPredictor integration with PDFProfiler/ClaimStore

Following TDD pattern:
- RED: Write failing tests first
- GREEN: Implement minimal code to pass
- REFACTOR: Improve while keeping tests green
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, Mock
from utils.exit_predictor import ExitPredictor


class TestExitPredictorClaimStoreIntegration:
    """Test ExitPredictor reading finance claims from ClaimStore"""

    @pytest.mark.asyncio
    async def test_compute_funding_score_reads_from_claim_store(self):
        """ExitPredictor should read finance claims from ClaimStore when available"""
        mock_claim_store = AsyncMock()

        # Mock ClaimStore returning finance claims
        mock_claim_store.get_extractions_by_entity.return_value = [
            {
                "predicate_hint": "cash_on_hand_usd",
                "raw_text": "900000",
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
            {
                "predicate_hint": "burn_rate_usd_monthly",
                "raw_text": "50000",
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
            {
                "predicate_hint": "runway_months",
                "raw_text": "18",
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
        ]

        predictor = ExitPredictor(claim_store=mock_claim_store)

        # This should fail - ExitPredictor doesn't accept claim_store yet
        canonical_key = "domain:acme.ai"
        score = await predictor.compute_funding_score_from_claims(canonical_key)

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_compute_funding_score_uses_valuation_data(self):
        """Should use pre/post money valuation to compute funding score"""
        mock_claim_store = AsyncMock()

        mock_claim_store.get_extractions_by_entity.return_value = [
            {
                "predicate_hint": "valuation_pre_money_usd",
                "raw_text": "5000000",
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
            {
                "predicate_hint": "round_size_usd",
                "raw_text": "2000000",
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
        ]

        predictor = ExitPredictor(claim_store=mock_claim_store)
        canonical_key = "domain:acme.ai"

        score = await predictor.compute_funding_score_from_claims(canonical_key)

        # Higher valuation should yield higher score
        assert score > 0.3  # Above default

    @pytest.mark.asyncio
    async def test_compute_funding_score_handles_no_claims(self):
        """Should return default score when no finance claims exist"""
        mock_claim_store = AsyncMock()
        mock_claim_store.get_extractions_by_entity.return_value = []

        predictor = ExitPredictor(claim_store=mock_claim_store)
        canonical_key = "domain:acme.ai"

        score = await predictor.compute_funding_score_from_claims(canonical_key)

        # Should return default funding score
        assert score == 0.3

    @pytest.mark.asyncio
    async def test_compute_funding_score_without_claim_store_returns_default(self):
        """Should work without ClaimStore (backward compatibility)"""
        predictor = ExitPredictor()  # No claim_store

        canonical_key = "domain:acme.ai"

        # Should handle missing claim_store gracefully
        score = await predictor.compute_funding_score_from_claims(canonical_key)

        assert score == 0.3  # Default when no claim_store

    @pytest.mark.asyncio
    async def test_enhanced_predict_uses_claim_store_data(self):
        """ExitPredictor with claim_store can compute enhanced funding scores"""
        mock_claim_store = AsyncMock()

        mock_claim_store.get_extractions_by_entity.return_value = [
            {
                "predicate_hint": "valuation_pre_money_usd",
                "raw_text": "10000000",  # $10M valuation
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
        ]

        predictor = ExitPredictor(claim_store=mock_claim_store)

        # Directly test compute_funding_score_from_claims
        # (predict() integration is separate - no parallel pathway per plan)
        canonical_key = "domain:acme.ai"
        funding_score = await predictor.compute_funding_score_from_claims(canonical_key)

        # Should have enhanced funding score from $10M valuation
        assert funding_score > 0.3  # Above default
        assert funding_score <= 1.0


class TestExitPredictorEnhancedScoring:
    """Test enhanced funding score calculations with finance metrics"""

    @pytest.mark.asyncio
    async def test_funding_score_considers_burn_rate_and_runway(self):
        """Funding score should consider burn rate and runway for sustainability"""
        mock_claim_store = AsyncMock()

        # High burn, low runway = risky
        mock_claim_store.get_extractions_by_entity.return_value = [
            {"predicate_hint": "burn_rate_usd_monthly", "raw_text": "200000"},
            {"predicate_hint": "runway_months", "raw_text": "6"},
            {"predicate_hint": "cash_on_hand_usd", "raw_text": "1200000"},
        ]

        predictor = ExitPredictor(claim_store=mock_claim_store)
        score = await predictor.compute_funding_score_from_claims("domain:risky.ai")

        # Should factor in runway risk
        assert isinstance(score, float)

    @pytest.mark.asyncio
    async def test_funding_score_prefers_recent_extractions(self):
        """Should prefer more recent PDF extractions over old data"""
        mock_claim_store = AsyncMock()

        # Simulate multiple extractions with different timestamps
        mock_claim_store.get_extractions_by_entity.return_value = [
            {
                "predicate_hint": "cash_on_hand_usd",
                "raw_text": "2000000",
                "created_at": "2026-01-27T10:00:00",  # Recent
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
            {
                "predicate_hint": "cash_on_hand_usd",
                "raw_text": "500000",
                "created_at": "2025-06-01T10:00:00",  # Old
                "extractor_name": "PDFProfiler.extract_finance_metrics",
            },
        ]

        predictor = ExitPredictor(claim_store=mock_claim_store)
        score = await predictor.compute_funding_score_from_claims("domain:acme.ai")

        # Should use more recent value (2M, not 500K)
        assert score > 0.4  # Higher than if using 500K
