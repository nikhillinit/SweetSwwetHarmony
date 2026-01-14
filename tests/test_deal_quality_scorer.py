"""
Tests for Deal Quality Scorer - unified scoring combining all Deal Intelligence factors.

TDD Approach: Tests written before implementation.

Deal Quality Score combines (PitchBook-inspired):
- Thesis fit (30%) - from thesis_matcher
- Traction momentum (25%) - from traction_calculator
- Investor quality (20%) - from investor_network
- Founder score (25%) - from founder_intent + signal_correlator

Routing recommendations:
- percentile >= 80: SOURCE (auto-push)
- percentile >= 50: TRACKING (needs review)
- percentile >= 20: HOLD
- percentile < 20: PASS
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from typing import List, Dict, Any


class TestGetComponentScores:
    """Test retrieval of component scores for a company."""

    @pytest.mark.asyncio
    async def test_get_thesis_fit_score(self):
        """Should retrieve thesis fit score from storage."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()
        mock_store.get_thesis_classification = AsyncMock(return_value={
            "canonical_key": "domain:startup.ai",
            "thesis_fit": 0.85,
            "category": "consumer_health_tech",
        })

        scorer = DealQualityScorer(mock_store)
        thesis_score = await scorer.get_thesis_fit_score("domain:startup.ai")

        assert thesis_score == 0.85
        mock_store.get_thesis_classification.assert_called_once_with("domain:startup.ai")

    @pytest.mark.asyncio
    async def test_get_traction_score(self):
        """Should retrieve traction momentum from storage."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()
        mock_store.get_traction_score = AsyncMock(return_value={
            "canonical_key": "domain:startup.ai",
            "composite_momentum": 0.72,
            "percentile": 0.80,
        })

        scorer = DealQualityScorer(mock_store)
        traction_score = await scorer.get_traction_score("domain:startup.ai")

        assert traction_score == 0.72
        mock_store.get_traction_score.assert_called_once_with("domain:startup.ai")

    @pytest.mark.asyncio
    async def test_get_investor_quality_score(self):
        """Should retrieve investor quality from storage."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()
        mock_store.get_company_investor_score = AsyncMock(return_value={
            "canonical_key": "domain:startup.ai",
            "investor_quality_score": 0.65,
            "top_investor": "Sequoia",
        })

        scorer = DealQualityScorer(mock_store)
        investor_score = await scorer.get_investor_quality_score("domain:startup.ai")

        assert investor_score == 0.65
        mock_store.get_company_investor_score.assert_called_once_with("domain:startup.ai")

    @pytest.mark.asyncio
    async def test_get_founder_score(self):
        """Should calculate founder score from intent signals."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()
        # Founder intent signals with high confidence
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[
            {"intent_type": "new_venture", "confidence": 0.8},
            {"intent_type": "activity_spike", "confidence": 0.6},
        ])

        scorer = DealQualityScorer(mock_store)
        founder_score = await scorer.get_founder_score("domain:startup.ai")

        # Score should be average confidence of intent signals
        assert founder_score == 0.7  # (0.8 + 0.6) / 2

    @pytest.mark.asyncio
    async def test_missing_score_returns_zero(self):
        """Should return 0.0 for missing component scores."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()
        mock_store.get_thesis_classification = AsyncMock(return_value=None)

        scorer = DealQualityScorer(mock_store)
        thesis_score = await scorer.get_thesis_fit_score("domain:unknown.ai")

        assert thesis_score == 0.0


class TestCalculateWeightedScore:
    """Test weighted combination of component scores."""

    @pytest.mark.asyncio
    async def test_calculate_weighted_score(self):
        """Should combine components with correct weights."""
        from utils.deal_quality_scorer import DealQualityScorer, DealQualityScore

        mock_store = AsyncMock()

        # Set up component scores
        mock_store.get_thesis_classification = AsyncMock(return_value={
            "thesis_fit": 0.80,
        })
        mock_store.get_traction_score = AsyncMock(return_value={
            "composite_momentum": 0.70,
        })
        mock_store.get_company_investor_score = AsyncMock(return_value={
            "investor_quality_score": 0.60,
        })
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[
            {"confidence": 0.50},
        ])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        # Expected: 0.80*0.30 + 0.70*0.25 + 0.60*0.20 + 0.50*0.25
        # = 0.24 + 0.175 + 0.12 + 0.125 = 0.66
        expected = 0.80 * 0.30 + 0.70 * 0.25 + 0.60 * 0.20 + 0.50 * 0.25

        assert isinstance(result, DealQualityScore)
        assert result.canonical_key == "domain:startup.ai"
        assert abs(result.raw_score - expected) < 0.01
        assert result.thesis_fit == 0.80
        assert result.traction == 0.70
        assert result.investor_quality == 0.60
        assert result.founder == 0.50

    @pytest.mark.asyncio
    async def test_weighted_score_with_missing_components(self):
        """Should handle missing component scores gracefully."""
        from utils.deal_quality_scorer import DealQualityScorer, DealQualityScore

        mock_store = AsyncMock()

        # Only thesis fit available
        mock_store.get_thesis_classification = AsyncMock(return_value={
            "thesis_fit": 0.80,
        })
        mock_store.get_traction_score = AsyncMock(return_value=None)
        mock_store.get_company_investor_score = AsyncMock(return_value=None)
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        # Expected: 0.80*0.30 + 0*0.25 + 0*0.20 + 0*0.25 = 0.24
        expected = 0.80 * 0.30

        assert abs(result.raw_score - expected) < 0.01

    @pytest.mark.asyncio
    async def test_score_is_capped_at_1(self):
        """Raw score should not exceed 1.0."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()

        # All components at max
        mock_store.get_thesis_classification = AsyncMock(return_value={
            "thesis_fit": 1.0,
        })
        mock_store.get_traction_score = AsyncMock(return_value={
            "composite_momentum": 1.0,
        })
        mock_store.get_company_investor_score = AsyncMock(return_value={
            "investor_quality_score": 1.0,
        })
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[
            {"confidence": 1.0},
        ])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.raw_score <= 1.0


class TestPercentileRanking:
    """Test percentile ranking against historical scores."""

    @pytest.mark.asyncio
    async def test_calculate_percentile(self):
        """Should calculate percentile rank vs all historical scores."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()

        # Current company score
        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 0.80})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.70})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.60})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 0.50}])

        # Historical scores: 20 signals with scores from 0.1 to 0.9
        historical_scores = [{"raw_score": 0.1 + i * 0.04} for i in range(20)]
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=historical_scores)

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        # Current raw score is ~0.66, should be around 75th percentile
        assert 0.5 <= result.percentile <= 0.95

    @pytest.mark.asyncio
    async def test_percentile_with_no_history(self):
        """Should return 0.5 (median) when no historical data exists."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 0.50})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.50})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.50})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 0.50}])
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.percentile == 0.5  # Default to median

    @pytest.mark.asyncio
    async def test_percentile_100_when_highest(self):
        """Should return 1.0 percentile when score is highest."""
        from utils.deal_quality_scorer import DealQualityScorer

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 1.0})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 1.0})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 1.0})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 1.0}])

        # Historical scores all below current
        historical_scores = [{"raw_score": 0.3}, {"raw_score": 0.4}, {"raw_score": 0.5}]
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=historical_scores)

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.percentile == 1.0


class TestRoutingRecommendation:
    """Test routing recommendation based on percentile."""

    @pytest.mark.asyncio
    async def test_source_recommendation_high_percentile(self):
        """Should recommend SOURCE for 80th+ percentile."""
        from utils.deal_quality_scorer import DealQualityScorer, RoutingRecommendation

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 0.95})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.90})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.85})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 0.90}])

        # All historical below current
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[
            {"raw_score": 0.3}, {"raw_score": 0.4}, {"raw_score": 0.5}, {"raw_score": 0.6}
        ])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.routing_recommendation == RoutingRecommendation.SOURCE

    @pytest.mark.asyncio
    async def test_tracking_recommendation_medium_percentile(self):
        """Should recommend TRACKING for 50-80th percentile."""
        from utils.deal_quality_scorer import DealQualityScorer, RoutingRecommendation

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 0.60})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.55})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.50})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 0.55}])

        # 60% below current
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[
            {"raw_score": 0.3}, {"raw_score": 0.4}, {"raw_score": 0.45},
            {"raw_score": 0.7}, {"raw_score": 0.8}
        ])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.routing_recommendation == RoutingRecommendation.TRACKING

    @pytest.mark.asyncio
    async def test_hold_recommendation_low_percentile(self):
        """Should recommend HOLD for 20-50th percentile."""
        from utils.deal_quality_scorer import DealQualityScorer, RoutingRecommendation

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 0.40})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.30})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.25})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 0.35}])

        # 30% below current
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[
            {"raw_score": 0.2}, {"raw_score": 0.25}, {"raw_score": 0.28},
            {"raw_score": 0.5}, {"raw_score": 0.6}, {"raw_score": 0.7},
            {"raw_score": 0.75}, {"raw_score": 0.8}, {"raw_score": 0.85}, {"raw_score": 0.9}
        ])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.routing_recommendation == RoutingRecommendation.HOLD

    @pytest.mark.asyncio
    async def test_pass_recommendation_very_low_percentile(self):
        """Should recommend PASS for <20th percentile."""
        from utils.deal_quality_scorer import DealQualityScorer, RoutingRecommendation

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 0.15})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.10})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.05})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[{"confidence": 0.10}])

        # All above current
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[
            {"raw_score": 0.3}, {"raw_score": 0.4}, {"raw_score": 0.5},
            {"raw_score": 0.6}, {"raw_score": 0.7}, {"raw_score": 0.8}
        ])

        scorer = DealQualityScorer(mock_store)
        result = await scorer.calculate_score("domain:startup.ai")

        assert result.routing_recommendation == RoutingRecommendation.PASS


class TestDealQualityScoreDataclass:
    """Test DealQualityScore dataclass structure."""

    def test_deal_quality_score_has_required_fields(self):
        """DealQualityScore should have all required fields."""
        from utils.deal_quality_scorer import DealQualityScore, RoutingRecommendation

        score = DealQualityScore(
            canonical_key="domain:startup.ai",
            raw_score=0.75,
            percentile=0.85,
            thesis_fit=0.80,
            traction=0.70,
            investor_quality=0.60,
            founder=0.65,
            routing_recommendation=RoutingRecommendation.SOURCE,
            calculated_at=datetime.now(timezone.utc),
        )

        assert score.canonical_key == "domain:startup.ai"
        assert score.raw_score == 0.75
        assert score.percentile == 0.85
        assert score.thesis_fit == 0.80
        assert score.traction == 0.70
        assert score.investor_quality == 0.60
        assert score.founder == 0.65
        assert score.routing_recommendation == RoutingRecommendation.SOURCE


class TestBatchScoring:
    """Test batch scoring of multiple companies."""

    @pytest.mark.asyncio
    async def test_calculate_scores_batch(self):
        """Should efficiently score multiple companies."""
        from utils.deal_quality_scorer import DealQualityScorer, DealQualityScore

        mock_store = AsyncMock()

        # Set up returns for two companies
        def get_thesis(key):
            return {"thesis_fit": 0.8 if key == "domain:a.ai" else 0.6}

        def get_traction(key):
            return {"composite_momentum": 0.7 if key == "domain:a.ai" else 0.5}

        def get_investor(key):
            return {"investor_quality_score": 0.6 if key == "domain:a.ai" else 0.4}

        def get_intent(key):
            return [{"confidence": 0.5 if key == "domain:a.ai" else 0.3}]

        mock_store.get_thesis_classification = AsyncMock(side_effect=get_thesis)
        mock_store.get_traction_score = AsyncMock(side_effect=get_traction)
        mock_store.get_company_investor_score = AsyncMock(side_effect=get_investor)
        mock_store.get_founder_intent_signals = AsyncMock(side_effect=get_intent)
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[])

        scorer = DealQualityScorer(mock_store)
        results = await scorer.calculate_scores_batch(["domain:a.ai", "domain:b.ai"])

        assert len(results) == 2
        assert all(isinstance(r, DealQualityScore) for r in results)
        assert results[0].canonical_key == "domain:a.ai"
        assert results[1].canonical_key == "domain:b.ai"
        assert results[0].raw_score > results[1].raw_score


class TestCustomWeights:
    """Test custom weight configuration."""

    @pytest.mark.asyncio
    async def test_custom_weights(self):
        """Should allow custom weight configuration."""
        from utils.deal_quality_scorer import DealQualityScorer, ScoringWeights

        mock_store = AsyncMock()

        mock_store.get_thesis_classification = AsyncMock(return_value={"thesis_fit": 1.0})
        mock_store.get_traction_score = AsyncMock(return_value={"composite_momentum": 0.0})
        mock_store.get_company_investor_score = AsyncMock(return_value={"investor_quality_score": 0.0})
        mock_store.get_founder_intent_signals = AsyncMock(return_value=[])
        mock_store.get_historical_deal_quality_scores = AsyncMock(return_value=[])

        # Custom weights: thesis 100%, everything else 0%
        custom_weights = ScoringWeights(
            thesis_fit=1.0,
            traction=0.0,
            investor_quality=0.0,
            founder=0.0,
        )

        scorer = DealQualityScorer(mock_store, weights=custom_weights)
        result = await scorer.calculate_score("domain:startup.ai")

        # Should be 1.0 * 1.0 = 1.0 (thesis only)
        assert result.raw_score == 1.0
