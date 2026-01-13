"""
ThesisFilter - Two-stage thesis classification for pipeline integration.

Stage 1: Fast keyword matching (free)
Stage 2: Gemini LLM semantic classification (free tier)

Routes signals to QUALIFIED, HELD, or REJECTED based on thesis fit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from utils.thesis_matcher import ThesisMatcher

logger = logging.getLogger(__name__)


class RoutingDecision(str, Enum):
    """Routing decision for signals."""
    QUALIFIED = "qualified"  # Passes gates, awaiting user push
    HELD = "held"            # Low fit, needs batch review
    REJECTED = "rejected"    # Excluded from thesis


@dataclass
class ThesisFilterConfig:
    """Configuration for thesis filter."""
    hold_threshold: float = 0.3           # Below this = HELD
    skip_llm_if_keyword_below: float = 0.2  # Skip LLM if obvious non-fit
    keyword_high_threshold: float = 0.7   # Keyword score for positive boost
    keyword_low_threshold: float = 0.4    # Keyword score for negative penalty
    high_boost: float = 0.08              # Confidence boost for high keyword fit
    low_penalty: float = -0.08            # Confidence penalty for low keyword fit
    negative_keyword_penalty: float = -0.12  # Extra penalty for negative keywords


@dataclass
class ThesisFilterResult:
    """Result of thesis filtering."""
    routing: RoutingDecision
    keyword_score: float = 0.0
    keyword_category: Optional[str] = None
    keyword_matches: List[str] = field(default_factory=list)
    negative_keywords: List[str] = field(default_factory=list)
    llm_score: Optional[float] = None
    llm_category: Optional[str] = None
    llm_rationale: Optional[str] = None
    llm_skipped: bool = False
    confidence_adjustment: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "routing": self.routing.value,
            "keyword_score": self.keyword_score,
            "keyword_category": self.keyword_category,
            "keyword_matches": self.keyword_matches,
            "negative_keywords": self.negative_keywords,
            "llm_score": self.llm_score,
            "llm_category": self.llm_category,
            "llm_rationale": self.llm_rationale,
            "llm_skipped": self.llm_skipped,
            "confidence_adjustment": self.confidence_adjustment,
        }


class ThesisFilter:
    """
    Two-stage thesis filter for discovery pipeline.

    Stage 1: Fast keyword matching using ThesisMatcher
    Stage 2: Gemini LLM semantic classification (optional)

    Usage:
        filter = ThesisFilter(ThesisFilterConfig())
        result = await filter.classify("Meal kit delivery startup")
        if result.routing == RoutingDecision.QUALIFIED:
            # Proceed to verification gate
    """

    def __init__(self, config: Optional[ThesisFilterConfig] = None):
        """
        Initialize thesis filter.

        Args:
            config: Filter configuration (uses defaults if not provided)
        """
        self.config = config or ThesisFilterConfig()
        self._keyword_matcher = ThesisMatcher()
        self._llm_classifier = None  # Lazy load

    @property
    def llm_classifier(self):
        """Lazy-load LLM classifier."""
        if self._llm_classifier is None:
            try:
                from consumer.thesis_filter.llm_classifier import LLMClassifier
                self._llm_classifier = LLMClassifier()
            except ImportError:
                logger.warning("LLM classifier not available")
        return self._llm_classifier

    async def classify(
        self,
        text: str,
        company_name: Optional[str] = None,
        skip_llm: bool = False,
    ) -> ThesisFilterResult:
        """
        Classify text through two-stage thesis filter.

        Args:
            text: Description or combined signal text
            company_name: Optional company name for context
            skip_llm: If True, only run keyword matching

        Returns:
            ThesisFilterResult with routing decision
        """
        # Stage 1: Keyword matching
        keyword_fit = self._keyword_matcher.score(text, company_name)

        # Check if we should skip LLM (obvious non-fit or explicit skip)
        if skip_llm or keyword_fit.score < self.config.skip_llm_if_keyword_below:
            adjustment = self._calculate_adjustment(
                keyword_fit.score,
                keyword_fit.negative_keywords,
            )

            # Route based on keyword score alone
            if keyword_fit.negative_keywords:
                routing = RoutingDecision.REJECTED
            elif keyword_fit.score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED

            return ThesisFilterResult(
                routing=routing,
                keyword_score=keyword_fit.score,
                keyword_category=keyword_fit.thesis.value,
                keyword_matches=keyword_fit.matched_keywords,
                negative_keywords=keyword_fit.negative_keywords,
                llm_skipped=True,
                confidence_adjustment=adjustment,
            )

        # Stage 2: LLM classification
        llm_result = None
        if self._llm_classifier:
            try:
                signal_data = {
                    "title": company_name or "Unknown",
                    "source_context": text,
                    "source_api": "pipeline",
                }
                llm_result = await self._llm_classifier.classify(signal_data)
            except Exception as e:
                logger.error(f"LLM classification failed: {e}")

        # Calculate confidence adjustment
        adjustment = self._calculate_adjustment(
            keyword_fit.score,
            keyword_fit.negative_keywords,
        )

        # Determine routing
        if llm_result:
            if llm_result.category == "excluded":
                routing = RoutingDecision.REJECTED
            elif llm_result.thesis_fit_score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED
        else:
            # Fallback to keyword-only routing
            if keyword_fit.negative_keywords:
                routing = RoutingDecision.REJECTED
            elif keyword_fit.score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED

        return ThesisFilterResult(
            routing=routing,
            keyword_score=keyword_fit.score,
            keyword_category=keyword_fit.thesis.value,
            keyword_matches=keyword_fit.matched_keywords,
            negative_keywords=keyword_fit.negative_keywords,
            llm_score=llm_result.thesis_fit_score if llm_result else None,
            llm_category=llm_result.category if llm_result else None,
            llm_rationale=llm_result.rationale if llm_result else None,
            llm_skipped=False,
            confidence_adjustment=adjustment,
        )

    def _calculate_adjustment(
        self,
        keyword_score: float,
        negative_keywords: List[str],
    ) -> float:
        """
        Calculate confidence adjustment based on keyword results.

        Args:
            keyword_score: Score from keyword matcher (0.0-1.0)
            negative_keywords: List of matched negative keywords

        Returns:
            Confidence adjustment value (-0.12 to +0.08)
        """
        adjustment = 0.0

        # Keyword score adjustment
        if keyword_score >= self.config.keyword_high_threshold:
            adjustment = self.config.high_boost
        elif keyword_score < self.config.keyword_low_threshold:
            adjustment = self.config.low_penalty

        # Negative keyword penalty (replaces score adjustment if present)
        if negative_keywords:
            adjustment = self.config.negative_keyword_penalty

        return adjustment
