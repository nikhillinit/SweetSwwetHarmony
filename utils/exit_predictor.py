"""
Exit Predictor - Phase 1 MVP

Heuristic-based exit prediction using weighted factors from academic research.
See: docs/plans/2026-01-15-exit-predictor-phase1-design.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.founder_store import FounderStore
    from utils.signal_velocity import SignalVelocityTracker
    from storage.signal_store import SignalStore
    from utils.signal_consolidator import ConsolidatedSignal


@dataclass
class ExitEvidence:
    """Simplified evidence for MVP - no quote field."""

    signal_id: int
    factor: str  # e.g., "founder_score", "thesis_fit"
    value: float  # The computed score for this factor


@dataclass
class ExitPrediction:
    """Exit prediction result for a company."""

    canonical_key: str

    # Component scores (0-1 each)
    thesis_fit: float
    founder_score: float
    traction_score: float
    funding_score: float
    velocity_score: float
    age_score: float
    investor_centrality: float  # Stubbed at 0.5 until Phase 2
    patent_count: float  # Stubbed at 0 until Phase 3

    # Computed outputs
    deal_quality_score: float  # Weighted sum (0-1)
    percentile_rank: Optional[int]  # NULL until nightly batch
    exit_probability: float  # Heuristic (0-1)
    confidence: Literal["high", "medium", "low"]
    recommendation: Literal["source", "tracking", "hold", "pass"]

    # Placeholders for Phase 3
    exit_timeline: str = "unknown"
    exit_type_probabilities: Dict[str, float] = field(default_factory=dict)

    # Evidence trail
    evidence: List[ExitEvidence] = field(default_factory=list)

    # Metadata
    predicted_at: datetime = field(default_factory=datetime.utcnow)
    model_version: str = "heuristic_v1"


class ExitPredictor:
    """
    Heuristic exit predictor using weighted scoring.

    Weights derived from academic research:
    - Gompers et al. (Skill vs Luck)
    - Hochberg et al. (Network Effects)
    - NBER working papers on startup outcomes
    """

    # Academic-validated weights
    WEIGHTS = {
        "founder_prior_exit": 0.25,
        "investor_centrality": 0.20,  # Stubbed at 0.5
        "thesis_fit": 0.20,
        "traction_velocity": 0.15,
        "patent_count": 0.10,  # Stubbed at 0
        "team_size_optimal": 0.05,
        "company_age": 0.05,
    }

    # Adjusted weights for available features
    ADJUSTED_WEIGHTS = {
        "founder_score": 0.25,
        "thesis_fit": 0.20,
        "investor_centrality": 0.20,
        "traction_score": 0.075,
        "velocity_score": 0.075,
        "patent_count": 0.10,
        "age_score": 0.05,
        "funding_score": 0.05,
    }

    HIGH_CONFIDENCE_THRESHOLD = 0.70
    MEDIUM_CONFIDENCE_THRESHOLD = 0.40

    def __init__(
        self,
        founder_store: Optional["FounderStore"] = None,
        velocity_tracker: Optional["SignalVelocityTracker"] = None,
        signal_store: Optional["SignalStore"] = None,
    ):
        self._founder_store = founder_store
        self._velocity_tracker = velocity_tracker
        self._signal_store = signal_store

    def _compute_traction_score(self, social_proof: Dict[str, int]) -> float:
        """
        Compute traction score from social proof metrics.

        Log-scale normalization: 1000 stars ≈ 1.0
        Returns 0.3 if no data available.
        """
        stars = social_proof.get("stars", 0)
        votes = social_proof.get("votes", 0)
        upvotes = social_proof.get("upvotes", 0)

        # Log scale: log10(1001) / 3 ≈ 1.0
        star_score = min(1.0, math.log10(stars + 1) / 3) if stars > 0 else 0
        vote_score = min(1.0, math.log10(votes + 1) / 2.5) if votes > 0 else 0
        upvote_score = min(1.0, math.log10(upvotes + 1) / 2.5) if upvotes > 0 else 0

        # Take max of available signals, default to 0.3 if no data
        best_score = max(star_score, vote_score, upvote_score)
        return best_score if best_score > 0 else 0.3

    def _compute_funding_score(self, raw_data: Dict[str, Any]) -> float:
        """
        Compute funding score from raw data.

        Log-scale: $10M ≈ 1.0
        Returns 0.3 if no funding data available.
        """
        total_funding = raw_data.get("total_funding", 0)
        if not total_funding:
            # Try to extract from nested data
            funding_data = raw_data.get("funding", {})
            total_funding = funding_data.get("total", 0)

        if total_funding <= 0:
            return 0.3  # Default for unknown

        # Log scale: log10(10_000_001) / 7 ≈ 1.0
        return min(1.0, math.log10(total_funding + 1) / 7)

    def _compute_age_score(self, founding_date: Optional[datetime]) -> float:
        """
        Compute company age score.

        Inverted U curve: peak at 2 years, decay after 5.
        Returns 0.5 if founding date unknown.
        """
        if not founding_date:
            return 0.5  # Default for unknown

        age_days = (datetime.utcnow() - founding_date).days
        age_years = age_days / 365.25

        if age_years < 0.5:
            # Too new - ramp up
            return 0.3 + (age_years / 0.5) * 0.4
        elif age_years <= 2:
            # Sweet spot - peak
            return 0.7 + (age_years - 0.5) / 1.5 * 0.3
        elif age_years <= 5:
            # Gradual decay
            return 1.0 - (age_years - 2) / 3 * 0.3
        else:
            # Old company - lower score
            return max(0.3, 0.7 - (age_years - 5) / 5 * 0.4)

    def _compute_deal_quality(self, scores: Dict[str, float]) -> float:
        """
        Compute weighted deal quality score.

        Uses ADJUSTED_WEIGHTS to handle stubbed features.
        Returns value between 0 and 1.
        """
        total = sum(
            scores.get(key, 0.5) * weight
            for key, weight in self.ADJUSTED_WEIGHTS.items()
        )
        return round(total, 4)

    def _compute_exit_probability(self, deal_quality: float) -> float:
        """
        Map deal quality to exit probability using sigmoid-like curve.

        Base rate: ~15% (typical VC-backed startup)
        Max rate: ~50% (exceptional deals)
        """
        base_rate = 0.15
        max_rate = 0.50

        # Sigmoid-ish curve centered at 0.5
        scaled = (deal_quality - 0.5) * 4
        probability = base_rate + (max_rate - base_rate) / (1 + math.exp(-scaled))

        return round(min(max(probability, 0.05), 0.95), 3)

    def _compute_confidence(
        self,
        scores: Dict[str, float],
        deal_quality: float,
    ) -> Literal["high", "medium", "low"]:
        """
        Determine prediction confidence based on data completeness.

        High: 5+ non-default scores AND clear signal (far from 0.5)
        Medium: 3+ non-default scores OR moderate signal
        Low: Sparse data, ambiguous score
        """
        # Count non-default scores (not 0.5, 0.3, or 0.0)
        defaults = {0.5, 0.3, 0.0}
        non_default_count = sum(
            1 for v in scores.values()
            if v not in defaults
        )

        # Score clarity: distance from 0.5
        clarity = abs(deal_quality - 0.5)

        if non_default_count >= 5 and clarity >= 0.15:
            return "high"
        elif non_default_count >= 3 or clarity >= 0.10:
            return "medium"
        else:
            return "low"

    def _compute_recommendation(
        self,
        deal_quality: float,
        confidence: Literal["high", "medium", "low"],
    ) -> Literal["source", "tracking", "hold", "pass"]:
        """
        Map deal quality and confidence to pipeline recommendation.

        Source: High quality (0.70+) with sufficient confidence
        Tracking: Medium quality (0.50-0.70)
        Hold: Low quality (0.30-0.50)
        Pass: Very low quality (<0.30)
        """
        if deal_quality >= 0.70 and confidence in ("high", "medium"):
            return "source"
        elif deal_quality >= 0.50:
            return "tracking"
        elif deal_quality >= 0.30:
            return "hold"
        else:
            return "pass"

    async def _compute_component_scores(
        self,
        consolidated: "ConsolidatedSignal",
        thesis_classification: Optional[Any] = None,
    ) -> Dict[str, float]:
        """
        Compute all component scores from available data sources.

        Returns dict with all score components needed for deal quality calculation.
        """
        # Thesis fit from classification or default
        thesis_fit = 0.5
        if thesis_classification is not None:
            thesis_fit = getattr(thesis_classification, "thesis_fit_score", 0.5)

        # Founder score from store (stubbed for now if store unavailable)
        founder_score = 0.5
        if self._founder_store and consolidated.canonical_key:
            try:
                founder_data = await self._founder_store.get_aggregate_founder_score(
                    consolidated.canonical_key
                )
                if founder_data is not None:
                    founder_score = founder_data
            except Exception:
                pass  # Keep default on error

        # Traction from social proof
        traction_score = self._compute_traction_score(
            consolidated.social_proof if consolidated.social_proof else {}
        )

        # Funding from raw data
        funding_score = self._compute_funding_score(
            consolidated.merged_raw_data if consolidated.merged_raw_data else {}
        )

        # Velocity from tracker (stubbed if unavailable)
        velocity_score = 0.5
        if self._velocity_tracker and consolidated.canonical_key:
            try:
                velocity = await self._velocity_tracker.get_velocity(
                    consolidated.canonical_key
                )
                if velocity is not None:
                    velocity_score = getattr(velocity, "momentum_score", 0.5)
            except Exception:
                pass  # Keep default on error

        # Age score from founding date
        age_score = self._compute_age_score(consolidated.founding_date)

        # Stubbed values for Phase 2 and 3
        investor_centrality = 0.5  # Phase 2: investor network analysis
        patent_count = 0.0  # Phase 3: USPTO integration

        return {
            "thesis_fit": thesis_fit,
            "founder_score": founder_score,
            "traction_score": traction_score,
            "funding_score": funding_score,
            "velocity_score": velocity_score,
            "age_score": age_score,
            "investor_centrality": investor_centrality,
            "patent_count": patent_count,
        }

    def _build_evidence(
        self,
        consolidated: "ConsolidatedSignal",
        scores: Dict[str, float],
    ) -> List[ExitEvidence]:
        """
        Build evidence trail from contributing signals and computed scores.

        Each evidence entry links a factor score to its source signal(s).
        """
        evidence = []

        # Use first contributing signal as primary source
        primary_signal_id = (
            consolidated.contributing_signal_ids[0]
            if consolidated.contributing_signal_ids
            else 0
        )

        # Add evidence for each non-default score
        defaults = {0.5, 0.3, 0.0}
        for factor, value in scores.items():
            if value not in defaults:
                evidence.append(
                    ExitEvidence(
                        signal_id=primary_signal_id,
                        factor=factor,
                        value=round(value, 4),
                    )
                )

        return evidence

    async def predict(
        self,
        consolidated: "ConsolidatedSignal",
        thesis_classification: Optional[Any] = None,
    ) -> ExitPrediction:
        """
        Generate exit prediction for a consolidated signal.

        Args:
            consolidated: Merged signal data from multiple sources
            thesis_classification: Optional thesis fit classification result

        Returns:
            ExitPrediction with scores, confidence, and recommendation
        """
        # Compute all component scores
        scores = await self._compute_component_scores(
            consolidated, thesis_classification
        )

        # Compute aggregated metrics
        deal_quality = self._compute_deal_quality(scores)
        exit_prob = self._compute_exit_probability(deal_quality)
        confidence = self._compute_confidence(scores, deal_quality)
        recommendation = self._compute_recommendation(deal_quality, confidence)

        # Build evidence trail
        evidence = self._build_evidence(consolidated, scores)

        return ExitPrediction(
            canonical_key=consolidated.canonical_key,
            thesis_fit=scores["thesis_fit"],
            founder_score=scores["founder_score"],
            traction_score=scores["traction_score"],
            funding_score=scores["funding_score"],
            velocity_score=scores["velocity_score"],
            age_score=scores["age_score"],
            investor_centrality=scores["investor_centrality"],
            patent_count=scores["patent_count"],
            deal_quality_score=deal_quality,
            percentile_rank=None,  # Set by nightly batch
            exit_probability=exit_prob,
            confidence=confidence,
            recommendation=recommendation,
            evidence=evidence,
        )
