"""
Tests for Exit Predictor Phase 1 MVP.

TDD: These tests define the expected behavior.
"""

import math
from datetime import datetime, timedelta

import pytest

from utils.exit_predictor import ExitEvidence, ExitPrediction, ExitPredictor


class TestExitEvidence:
    """Tests for ExitEvidence dataclass."""

    def test_create_evidence(self):
        evidence = ExitEvidence(signal_id=1, factor="founder_score", value=0.8)
        assert evidence.signal_id == 1
        assert evidence.factor == "founder_score"
        assert evidence.value == 0.8


class TestExitPrediction:
    """Tests for ExitPrediction dataclass."""

    def test_create_prediction_minimal(self):
        prediction = ExitPrediction(
            canonical_key="domain:test.com",
            thesis_fit=0.7,
            founder_score=0.6,
            traction_score=0.5,
            funding_score=0.4,
            velocity_score=0.5,
            age_score=0.8,
            investor_centrality=0.5,
            patent_count=0.0,
            deal_quality_score=0.65,
            percentile_rank=None,
            exit_probability=0.25,
            confidence="medium",
            recommendation="tracking",
        )
        assert prediction.canonical_key == "domain:test.com"
        assert prediction.exit_timeline == "unknown"
        assert prediction.exit_type_probabilities == {}
        assert prediction.model_version == "heuristic_v1"

    def test_prediction_has_evidence_list(self):
        prediction = ExitPrediction(
            canonical_key="domain:test.com",
            thesis_fit=0.7,
            founder_score=0.6,
            traction_score=0.5,
            funding_score=0.4,
            velocity_score=0.5,
            age_score=0.8,
            investor_centrality=0.5,
            patent_count=0.0,
            deal_quality_score=0.65,
            percentile_rank=None,
            exit_probability=0.25,
            confidence="medium",
            recommendation="tracking",
        )
        assert isinstance(prediction.evidence, list)
        assert len(prediction.evidence) == 0


class TestComputeTractionScore:
    """Tests for _compute_traction_score method."""

    def test_traction_score_with_1000_stars(self):
        """1000 stars should yield approximately 1.0."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"stars": 1000})
        assert 0.95 <= score <= 1.0

    def test_traction_score_with_100_stars(self):
        """100 stars should yield ~0.67 (log10(101)/3)."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"stars": 100})
        expected = math.log10(101) / 3  # ~0.67
        assert abs(score - expected) < 0.01

    def test_traction_score_with_10_stars(self):
        """10 stars should yield ~0.35 (log10(11)/3)."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"stars": 10})
        expected = math.log10(11) / 3  # ~0.35
        assert abs(score - expected) < 0.01

    def test_traction_score_no_data_returns_default(self):
        """Empty social proof returns 0.3 default."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({})
        assert score == 0.3

    def test_traction_score_zero_stars_returns_default(self):
        """Zero stars returns 0.3 default."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"stars": 0})
        assert score == 0.3

    def test_traction_score_with_votes(self):
        """Votes use log scale with 2.5 divisor."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"votes": 100})
        expected = math.log10(101) / 2.5  # ~0.80
        assert abs(score - expected) < 0.01

    def test_traction_score_with_upvotes(self):
        """Upvotes use same scale as votes."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"upvotes": 100})
        expected = math.log10(101) / 2.5
        assert abs(score - expected) < 0.01

    def test_traction_score_takes_max(self):
        """When multiple metrics, take the highest."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"stars": 10, "votes": 100})
        # votes(100) = 0.80 > stars(10) = 0.35
        assert score > 0.7

    def test_traction_score_capped_at_1(self):
        """Score cannot exceed 1.0."""
        predictor = ExitPredictor()
        score = predictor._compute_traction_score({"stars": 100000})
        assert score == 1.0


class TestComputeFundingScore:
    """Tests for _compute_funding_score method."""

    def test_funding_score_10m(self):
        """$10M should yield approximately 1.0."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({"total_funding": 10_000_000})
        assert 0.95 <= score <= 1.0

    def test_funding_score_1m(self):
        """$1M should yield ~0.86 (log10(1M)/7)."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({"total_funding": 1_000_000})
        expected = math.log10(1_000_001) / 7  # ~0.86
        assert abs(score - expected) < 0.01

    def test_funding_score_100k(self):
        """$100k should yield ~0.71 (log10(100k)/7)."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({"total_funding": 100_000})
        expected = math.log10(100_001) / 7  # ~0.71
        assert abs(score - expected) < 0.01

    def test_funding_score_no_data_returns_default(self):
        """No funding data returns 0.3 default."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({})
        assert score == 0.3

    def test_funding_score_zero_returns_default(self):
        """Zero funding returns 0.3 default."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({"total_funding": 0})
        assert score == 0.3

    def test_funding_score_nested_data(self):
        """Can extract from nested funding.total."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({"funding": {"total": 1_000_000}})
        expected = math.log10(1_000_001) / 7
        assert abs(score - expected) < 0.01

    def test_funding_score_capped_at_1(self):
        """Score cannot exceed 1.0."""
        predictor = ExitPredictor()
        score = predictor._compute_funding_score({"total_funding": 1_000_000_000})
        assert score == 1.0


class TestComputeAgeScore:
    """Tests for _compute_age_score method."""

    def test_age_score_no_date_returns_default(self):
        """No founding date returns 0.5 default."""
        predictor = ExitPredictor()
        score = predictor._compute_age_score(None)
        assert score == 0.5

    def test_age_score_very_new(self):
        """Company < 6 months old scores low (ramping up)."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=90)  # 3 months
        score = predictor._compute_age_score(founding_date)
        # age_years = 0.25, score = 0.3 + (0.25/0.5) * 0.4 = 0.5
        assert 0.45 <= score <= 0.55

    def test_age_score_peak_at_2_years(self):
        """Company at 2 years should score ~1.0 (peak)."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=730)  # 2 years
        score = predictor._compute_age_score(founding_date)
        assert 0.95 <= score <= 1.0

    def test_age_score_1_year(self):
        """Company at 1 year is in sweet spot."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=365)
        score = predictor._compute_age_score(founding_date)
        assert 0.75 <= score <= 0.90

    def test_age_score_3_years(self):
        """Company at 3 years starts decay."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=1095)  # 3 years
        score = predictor._compute_age_score(founding_date)
        # decay: 1.0 - (1/3)*0.3 = 0.9
        assert 0.85 <= score <= 0.95

    def test_age_score_5_years(self):
        """Company at 5 years further decay."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=1825)  # 5 years
        score = predictor._compute_age_score(founding_date)
        # decay: 1.0 - (3/3)*0.3 = 0.7
        assert 0.65 <= score <= 0.75

    def test_age_score_10_years(self):
        """Company at 10 years scores low."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=3650)
        score = predictor._compute_age_score(founding_date)
        # 0.7 - (5/5)*0.4 = 0.3
        assert 0.25 <= score <= 0.40

    def test_age_score_minimum_is_03(self):
        """Age score never goes below 0.3."""
        predictor = ExitPredictor()
        founding_date = datetime.utcnow() - timedelta(days=7300)  # 20 years
        score = predictor._compute_age_score(founding_date)
        assert score >= 0.3


class TestComputeDealQuality:
    """Tests for _compute_deal_quality method."""

    def test_deal_quality_all_ones(self):
        """All perfect scores should yield ~1.0."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 1.0,
            "thesis_fit": 1.0,
            "investor_centrality": 1.0,
            "traction_score": 1.0,
            "velocity_score": 1.0,
            "patent_count": 1.0,
            "age_score": 1.0,
            "funding_score": 1.0,
        }
        quality = predictor._compute_deal_quality(scores)
        assert quality == 1.0

    def test_deal_quality_all_zeros(self):
        """All zero scores should yield 0."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 0.0,
            "thesis_fit": 0.0,
            "investor_centrality": 0.0,
            "traction_score": 0.0,
            "velocity_score": 0.0,
            "patent_count": 0.0,
            "age_score": 0.0,
            "funding_score": 0.0,
        }
        quality = predictor._compute_deal_quality(scores)
        assert quality == 0.0

    def test_deal_quality_all_defaults(self):
        """Default scores (0.5) should yield 0.5."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 0.5,
            "thesis_fit": 0.5,
            "investor_centrality": 0.5,
            "traction_score": 0.5,
            "velocity_score": 0.5,
            "patent_count": 0.5,
            "age_score": 0.5,
            "funding_score": 0.5,
        }
        quality = predictor._compute_deal_quality(scores)
        assert quality == 0.5

    def test_deal_quality_weighted_sum(self):
        """Verify weights are applied correctly."""
        predictor = ExitPredictor()
        # Only founder_score = 1.0, rest = 0
        scores = {
            "founder_score": 1.0,
            "thesis_fit": 0.0,
            "investor_centrality": 0.0,
            "traction_score": 0.0,
            "velocity_score": 0.0,
            "patent_count": 0.0,
            "age_score": 0.0,
            "funding_score": 0.0,
        }
        quality = predictor._compute_deal_quality(scores)
        # founder_score weight is 0.25
        assert quality == 0.25

    def test_deal_quality_missing_key_uses_default(self):
        """Missing keys should use 0.5 default."""
        predictor = ExitPredictor()
        scores = {"founder_score": 1.0}  # Only one score
        quality = predictor._compute_deal_quality(scores)
        # founder_score: 1.0 * 0.25 = 0.25
        # others: 0.5 * (0.20+0.20+0.075+0.075+0.10+0.05+0.05) = 0.5 * 0.75 = 0.375
        expected = 0.25 + 0.375
        assert abs(quality - expected) < 0.01


class TestComputeExitProbability:
    """Tests for _compute_exit_probability method."""

    def test_exit_probability_at_05_deal_quality(self):
        """Deal quality 0.5 should yield ~32.5% (midpoint)."""
        predictor = ExitPredictor()
        prob = predictor._compute_exit_probability(0.5)
        # sigmoid(0) = 0.5, so prob = 0.15 + 0.35*0.5 = 0.325
        assert 0.30 <= prob <= 0.35

    def test_exit_probability_at_high_deal_quality(self):
        """Deal quality 0.9 should yield high probability."""
        predictor = ExitPredictor()
        prob = predictor._compute_exit_probability(0.9)
        assert prob >= 0.40

    def test_exit_probability_at_low_deal_quality(self):
        """Deal quality 0.2 should yield low probability."""
        predictor = ExitPredictor()
        prob = predictor._compute_exit_probability(0.2)
        assert prob <= 0.25

    def test_exit_probability_capped_at_095(self):
        """Exit probability never exceeds 0.95."""
        predictor = ExitPredictor()
        prob = predictor._compute_exit_probability(1.0)
        assert prob <= 0.95

    def test_exit_probability_minimum_005(self):
        """Exit probability never below 0.05."""
        predictor = ExitPredictor()
        prob = predictor._compute_exit_probability(0.0)
        assert prob >= 0.05


class TestComputeConfidence:
    """Tests for _compute_confidence method."""

    def test_confidence_high_when_complete_and_clear(self):
        """High confidence: 5+ non-default scores AND clear signal."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 0.9,
            "thesis_fit": 0.8,
            "investor_centrality": 0.7,
            "traction_score": 0.85,
            "velocity_score": 0.75,
            "patent_count": 0.6,
            "age_score": 0.8,
            "funding_score": 0.7,
        }
        confidence = predictor._compute_confidence(scores, deal_quality=0.78)
        assert confidence == "high"

    def test_confidence_medium_with_3_scores(self):
        """Medium confidence: 3+ non-default scores."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 0.9,
            "thesis_fit": 0.8,
            "investor_centrality": 0.7,
            "traction_score": 0.5,  # default
            "velocity_score": 0.5,  # default
            "patent_count": 0.0,  # default
            "age_score": 0.5,  # default
            "funding_score": 0.3,  # default
        }
        confidence = predictor._compute_confidence(scores, deal_quality=0.55)
        assert confidence == "medium"

    def test_confidence_medium_with_moderate_clarity(self):
        """Medium confidence: moderate signal clarity."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 0.5,
            "thesis_fit": 0.5,
            "investor_centrality": 0.5,
            "traction_score": 0.5,
            "velocity_score": 0.5,
            "patent_count": 0.5,
            "age_score": 0.5,
            "funding_score": 0.5,
        }
        # deal_quality far from 0.5 = clear signal
        confidence = predictor._compute_confidence(scores, deal_quality=0.65)
        assert confidence == "medium"

    def test_confidence_low_sparse_and_ambiguous(self):
        """Low confidence: sparse data AND ambiguous score."""
        predictor = ExitPredictor()
        scores = {
            "founder_score": 0.5,
            "thesis_fit": 0.5,
            "investor_centrality": 0.5,
            "traction_score": 0.3,
            "velocity_score": 0.5,
            "patent_count": 0.0,
            "age_score": 0.5,
            "funding_score": 0.3,
        }
        # deal_quality close to 0.5 = ambiguous
        confidence = predictor._compute_confidence(scores, deal_quality=0.48)
        assert confidence == "low"


class TestComputeRecommendation:
    """Tests for _compute_recommendation method."""

    def test_recommendation_source_high_quality_high_confidence(self):
        """Source: deal_quality >= 0.70 and high confidence."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.75, "high")
        assert rec == "source"

    def test_recommendation_source_high_quality_medium_confidence(self):
        """Source: deal_quality >= 0.70 and medium confidence."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.72, "medium")
        assert rec == "source"

    def test_recommendation_tracking_high_quality_low_confidence(self):
        """Tracking: high quality but low confidence."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.75, "low")
        # Low confidence doesn't qualify for source
        assert rec == "tracking"

    def test_recommendation_tracking_medium_quality(self):
        """Tracking: deal_quality 0.50-0.70."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.60, "high")
        assert rec == "tracking"

    def test_recommendation_hold_low_quality(self):
        """Hold: deal_quality 0.30-0.50."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.40, "medium")
        assert rec == "hold"

    def test_recommendation_pass_very_low_quality(self):
        """Pass: deal_quality < 0.30."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.20, "high")
        assert rec == "pass"

    def test_recommendation_boundary_070(self):
        """Boundary: exactly 0.70 with medium confidence."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.70, "medium")
        assert rec == "source"

    def test_recommendation_boundary_050(self):
        """Boundary: exactly 0.50."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.50, "high")
        assert rec == "tracking"

    def test_recommendation_boundary_030(self):
        """Boundary: exactly 0.30."""
        predictor = ExitPredictor()
        rec = predictor._compute_recommendation(0.30, "high")
        assert rec == "hold"


class TestBuildEvidence:
    """Tests for _build_evidence method."""

    def test_build_evidence_non_default_scores(self):
        """Evidence created only for non-default scores."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[101, 102],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        scores = {
            "thesis_fit": 0.8,  # non-default
            "founder_score": 0.5,  # default
            "traction_score": 0.3,  # default
            "funding_score": 0.0,  # default
            "velocity_score": 0.75,  # non-default
            "age_score": 0.9,  # non-default
            "investor_centrality": 0.5,  # default (stubbed)
            "patent_count": 0.0,  # default (stubbed)
        }

        evidence = predictor._build_evidence(consolidated, scores)

        # Only 3 non-default scores
        assert len(evidence) == 3
        factors = {e.factor for e in evidence}
        assert factors == {"thesis_fit", "velocity_score", "age_score"}

    def test_build_evidence_uses_primary_signal_id(self):
        """Evidence uses first contributing signal ID."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[999, 888, 777],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        scores = {"thesis_fit": 0.8}
        evidence = predictor._build_evidence(consolidated, scores)

        assert len(evidence) == 1
        assert evidence[0].signal_id == 999  # First ID

    def test_build_evidence_empty_signals(self):
        """Handle empty contributing_signal_ids."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[],  # Empty
            signal_types=[],
            source_apis=[],
            aggregated_confidence=0.5,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        scores = {"thesis_fit": 0.8}
        evidence = predictor._build_evidence(consolidated, scores)

        assert len(evidence) == 1
        assert evidence[0].signal_id == 0  # Default


class TestStubbedValues:
    """Tests for stubbed Phase 2/3 values."""

    @pytest.mark.asyncio
    async def test_investor_centrality_stubbed_at_05(self):
        """investor_centrality should always be 0.5 in Phase 1."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        prediction = await predictor.predict(consolidated)
        assert prediction.investor_centrality == 0.5

    @pytest.mark.asyncio
    async def test_patent_count_stubbed_at_0(self):
        """patent_count should always be 0.0 in Phase 1."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        prediction = await predictor.predict(consolidated)
        assert prediction.patent_count == 0.0


class TestPredictMethod:
    """Tests for the full predict() method."""

    @pytest.mark.asyncio
    async def test_predict_returns_exit_prediction(self):
        """predict() returns ExitPrediction dataclass."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme AI",
            contributing_signal_ids=[1, 2],
            signal_types=["github_spike", "incorporation"],
            source_apis=["github", "companies_house"],
            aggregated_confidence=0.75,
            earliest_detected_at=datetime.utcnow() - timedelta(days=30),
            latest_detected_at=datetime.utcnow(),
            social_proof={"stars": 500, "upvotes": 150},
            founding_date=datetime.utcnow() - timedelta(days=365),  # 1 year old
        )

        prediction = await predictor.predict(consolidated)

        assert isinstance(prediction, ExitPrediction)
        assert prediction.canonical_key == "domain:acme.ai"
        assert prediction.model_version == "heuristic_v1"

    @pytest.mark.asyncio
    async def test_predict_with_thesis_classification(self):
        """predict() uses thesis classification when provided."""
        from utils.signal_consolidator import ConsolidatedSignal
        from dataclasses import dataclass

        @dataclass
        class MockThesisClassification:
            thesis_fit_score: float = 0.85

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        thesis = MockThesisClassification(thesis_fit_score=0.85)
        prediction = await predictor.predict(consolidated, thesis_classification=thesis)

        assert prediction.thesis_fit == 0.85

    @pytest.mark.asyncio
    async def test_predict_calculates_deal_quality(self):
        """predict() computes deal_quality_score."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
            social_proof={"stars": 1000},  # High traction
            founding_date=datetime.utcnow() - timedelta(days=730),  # 2 years (peak)
        )

        prediction = await predictor.predict(consolidated)

        # With high traction and peak age, should have reasonable quality
        assert 0.4 <= prediction.deal_quality_score <= 0.7
        assert prediction.exit_probability > 0

    @pytest.mark.asyncio
    async def test_predict_generates_evidence(self):
        """predict() populates evidence trail."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[42],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
            social_proof={"stars": 500},  # Non-default traction
            founding_date=datetime.utcnow() - timedelta(days=730),  # Non-default age
        )

        prediction = await predictor.predict(consolidated)

        # Should have evidence for non-default scores
        assert len(prediction.evidence) > 0
        assert all(e.signal_id == 42 for e in prediction.evidence)

    @pytest.mark.asyncio
    async def test_predict_sets_recommendation(self):
        """predict() determines recommendation based on quality."""
        from utils.signal_consolidator import ConsolidatedSignal
        from dataclasses import dataclass

        @dataclass
        class MockThesisClassification:
            thesis_fit_score: float = 0.9

        predictor = ExitPredictor()
        # High quality signals
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.9,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
            social_proof={"stars": 5000, "votes": 500},
            founding_date=datetime.utcnow() - timedelta(days=730),
            merged_raw_data={"total_funding": 5_000_000},
        )

        thesis = MockThesisClassification(thesis_fit_score=0.9)
        prediction = await predictor.predict(consolidated, thesis_classification=thesis)

        # Should be high enough for tracking or source
        assert prediction.recommendation in ("source", "tracking", "hold")

    @pytest.mark.asyncio
    async def test_predict_percentile_rank_is_none(self):
        """percentile_rank is None until batch job runs."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        prediction = await predictor.predict(consolidated)
        assert prediction.percentile_rank is None

    @pytest.mark.asyncio
    async def test_predict_exit_timeline_is_placeholder(self):
        """exit_timeline is 'unknown' placeholder in Phase 1."""
        from utils.signal_consolidator import ConsolidatedSignal

        predictor = ExitPredictor()
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test.com",
            company_name="Test Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=datetime.utcnow(),
            latest_detected_at=datetime.utcnow(),
        )

        prediction = await predictor.predict(consolidated)
        assert prediction.exit_timeline == "unknown"
        assert prediction.exit_type_probabilities == {}
