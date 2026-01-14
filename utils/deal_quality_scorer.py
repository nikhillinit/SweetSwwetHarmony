"""
Deal Quality Scorer - unified scoring combining all Deal Intelligence factors.

Part of Deal Intelligence Engine (Phase 5).

This module provides:
- Weighted combination of component scores (thesis, traction, investor, founder)
- Percentile ranking against historical scores
- Routing recommendations based on quality percentile (PitchBook-inspired)

Default Weights:
    - Thesis fit: 30%
    - Traction momentum: 25%
    - Investor quality: 20%
    - Founder score: 25%

Routing Recommendations:
    - percentile >= 0.80: SOURCE (auto-push to CRM)
    - percentile >= 0.50: TRACKING (needs review)
    - percentile >= 0.20: HOLD (batch review later)
    - percentile < 0.20: PASS (not a fit)

Usage:
    scorer = DealQualityScorer(signal_store)

    # Score a single company
    score = await scorer.calculate_score("domain:startup.ai")

    # Score multiple companies
    scores = await scorer.calculate_scores_batch(["domain:a.ai", "domain:b.ai"])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

class RoutingRecommendation(str, Enum):
    """Routing recommendation based on deal quality percentile."""
    SOURCE = "source"       # Auto-push to CRM (80th+ percentile)
    TRACKING = "tracking"   # Needs review (50-80th percentile)
    HOLD = "hold"           # Batch review later (20-50th percentile)
    PASS = "pass"           # Not a fit (<20th percentile)


@dataclass
class ScoringWeights:
    """Configurable weights for deal quality scoring."""
    thesis_fit: float = 0.30
    traction: float = 0.25
    investor_quality: float = 0.20
    founder: float = 0.25

    def __post_init__(self):
        """Validate weights sum to 1.0."""
        total = self.thesis_fit + self.traction + self.investor_quality + self.founder
        if abs(total - 1.0) > 0.01:
            logger.warning(f"Scoring weights sum to {total}, not 1.0. Normalizing.")
            # Normalize
            self.thesis_fit /= total
            self.traction /= total
            self.investor_quality /= total
            self.founder /= total


@dataclass
class DealQualityScore:
    """Unified deal quality score for a company."""
    canonical_key: str
    raw_score: float           # Weighted combination [0, 1]
    percentile: float          # Percentile rank vs historical [0, 1]

    # Component scores
    thesis_fit: float
    traction: float
    investor_quality: float
    founder: float

    # Routing
    routing_recommendation: RoutingRecommendation

    # Audit
    calculated_at: datetime


# =============================================================================
# ROUTING THRESHOLDS
# =============================================================================

ROUTING_THRESHOLDS = {
    RoutingRecommendation.SOURCE: 0.80,    # 80th+ percentile
    RoutingRecommendation.TRACKING: 0.50,  # 50-80th percentile
    RoutingRecommendation.HOLD: 0.20,      # 20-50th percentile
    # Below 20th percentile = PASS
}


# =============================================================================
# DEAL QUALITY SCORER
# =============================================================================

class DealQualityScorer:
    """
    Unified deal quality scoring combining all Deal Intelligence factors.

    Inspired by PitchBook's approach to deal prioritization through
    multi-factor scoring and percentile ranking.
    """

    def __init__(
        self,
        store,
        weights: Optional[ScoringWeights] = None,
    ):
        """
        Initialize with storage layer and optional custom weights.

        Args:
            store: Storage layer with component score access methods
            weights: Optional custom scoring weights (defaults to standard weights)
        """
        self.store = store
        self.weights = weights or ScoringWeights()

    # =========================================================================
    # COMPONENT SCORE RETRIEVAL
    # =========================================================================

    async def get_thesis_fit_score(self, canonical_key: str) -> float:
        """
        Get thesis fit score for a company.

        Args:
            canonical_key: Company identifier

        Returns:
            Thesis fit score [0, 1], or 0.0 if not found
        """
        try:
            classification = await self.store.get_thesis_classification(canonical_key)
            if classification and "thesis_fit" in classification:
                return float(classification["thesis_fit"])
        except Exception as e:
            logger.debug(f"Failed to get thesis fit for {canonical_key}: {e}")

        return 0.0

    async def get_traction_score(self, canonical_key: str) -> float:
        """
        Get traction momentum score for a company.

        Args:
            canonical_key: Company identifier

        Returns:
            Composite momentum [0, 1], or 0.0 if not found
        """
        try:
            traction = await self.store.get_traction_score(canonical_key)
            if traction and "composite_momentum" in traction:
                return float(traction["composite_momentum"])
        except Exception as e:
            logger.debug(f"Failed to get traction for {canonical_key}: {e}")

        return 0.0

    async def get_investor_quality_score(self, canonical_key: str) -> float:
        """
        Get investor quality score for a company.

        Args:
            canonical_key: Company identifier

        Returns:
            Investor quality score [0, 1], or 0.0 if not found
        """
        try:
            investor_score = await self.store.get_company_investor_score(canonical_key)
            if investor_score and "investor_quality_score" in investor_score:
                return float(investor_score["investor_quality_score"])
        except Exception as e:
            logger.debug(f"Failed to get investor quality for {canonical_key}: {e}")

        return 0.0

    async def get_founder_score(self, canonical_key: str) -> float:
        """
        Get founder score based on intent signals.

        Calculates average confidence of detected founder intent signals.

        Args:
            canonical_key: Company identifier

        Returns:
            Founder score [0, 1], or 0.0 if no signals
        """
        try:
            intent_signals = await self.store.get_founder_intent_signals(canonical_key)
            if intent_signals and len(intent_signals) > 0:
                confidences = [
                    s.get("confidence", 0.0) for s in intent_signals
                    if isinstance(s, dict) and "confidence" in s
                ]
                if confidences:
                    return sum(confidences) / len(confidences)
        except Exception as e:
            logger.debug(f"Failed to get founder score for {canonical_key}: {e}")

        return 0.0

    # =========================================================================
    # WEIGHTED SCORE CALCULATION
    # =========================================================================

    async def calculate_score(self, canonical_key: str) -> DealQualityScore:
        """
        Calculate unified deal quality score for a company.

        Combines component scores with weighted average, calculates
        percentile rank, and determines routing recommendation.

        Args:
            canonical_key: Company identifier

        Returns:
            DealQualityScore with all metrics and routing recommendation
        """
        # Get component scores
        thesis_fit = await self.get_thesis_fit_score(canonical_key)
        traction = await self.get_traction_score(canonical_key)
        investor_quality = await self.get_investor_quality_score(canonical_key)
        founder = await self.get_founder_score(canonical_key)

        # Calculate weighted raw score
        raw_score = (
            thesis_fit * self.weights.thesis_fit +
            traction * self.weights.traction +
            investor_quality * self.weights.investor_quality +
            founder * self.weights.founder
        )

        # Cap at 1.0
        raw_score = min(raw_score, 1.0)

        # Calculate percentile rank
        percentile = await self._calculate_percentile(raw_score)

        # Determine routing recommendation
        routing = self._get_routing_recommendation(percentile)

        return DealQualityScore(
            canonical_key=canonical_key,
            raw_score=raw_score,
            percentile=percentile,
            thesis_fit=thesis_fit,
            traction=traction,
            investor_quality=investor_quality,
            founder=founder,
            routing_recommendation=routing,
            calculated_at=datetime.now(timezone.utc),
        )

    async def calculate_scores_batch(
        self,
        canonical_keys: List[str],
    ) -> List[DealQualityScore]:
        """
        Calculate deal quality scores for multiple companies.

        Args:
            canonical_keys: List of company identifiers

        Returns:
            List of DealQualityScore objects
        """
        results = []
        for key in canonical_keys:
            try:
                score = await self.calculate_score(key)
                results.append(score)
            except Exception as e:
                logger.warning(f"Failed to score {key}: {e}")

        return results

    # =========================================================================
    # PERCENTILE CALCULATION
    # =========================================================================

    async def _calculate_percentile(self, raw_score: float) -> float:
        """
        Calculate percentile rank against historical scores.

        Args:
            raw_score: Current raw score

        Returns:
            Percentile [0, 1], defaults to 0.5 if no history
        """
        try:
            historical = await self.store.get_historical_deal_quality_scores()

            if not historical or not isinstance(historical, (list, tuple)):
                return 0.5  # Default to median

            if len(historical) == 0:
                return 0.5

            # Count how many scores are below current
            scores_below = sum(
                1 for h in historical
                if isinstance(h, dict) and h.get("raw_score", 0) < raw_score
            )

            # Percentile = proportion of scores below current
            return scores_below / len(historical)

        except Exception as e:
            logger.debug(f"Failed to calculate percentile: {e}")
            return 0.5

    # =========================================================================
    # ROUTING RECOMMENDATION
    # =========================================================================

    def _get_routing_recommendation(self, percentile: float) -> RoutingRecommendation:
        """
        Determine routing recommendation based on percentile.

        Args:
            percentile: Percentile rank [0, 1]

        Returns:
            RoutingRecommendation enum value
        """
        if percentile >= ROUTING_THRESHOLDS[RoutingRecommendation.SOURCE]:
            return RoutingRecommendation.SOURCE
        elif percentile >= ROUTING_THRESHOLDS[RoutingRecommendation.TRACKING]:
            return RoutingRecommendation.TRACKING
        elif percentile >= ROUTING_THRESHOLDS[RoutingRecommendation.HOLD]:
            return RoutingRecommendation.HOLD
        else:
            return RoutingRecommendation.PASS
