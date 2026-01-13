"""
Enrichment Boost Calculator for Discovery Engine.

Converts enrichment data (company age, social proof) from ConsolidatedSignal
into confidence boosts using threshold-based scoring.

Usage:
    from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentConfig

    calculator = EnrichmentBoostCalculator()
    boost = calculator.calculate(consolidated_signal)

    # Apply boost to confidence
    final_confidence = consolidated_signal.aggregated_confidence + boost.total_boost
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from utils.signal_consolidator import ConsolidatedSignal


@dataclass
class EnrichmentConfig:
    """
    Configuration for enrichment boost thresholds and values.

    Age thresholds determine company maturity:
    - >= age_high_threshold_days (2 years): established, lower risk
    - >= age_medium_threshold_days (1 year): some track record

    Social proof thresholds indicate traction:
    - Stars: GitHub popularity
    - Upvotes: Product Hunt popularity
    """

    # Age thresholds (in days)
    age_high_threshold_days: int = 730  # 2 years
    age_medium_threshold_days: int = 365  # 1 year

    # Age boosts
    age_high_boost: float = 0.03
    age_medium_boost: float = 0.02

    # Social proof thresholds
    stars_high_threshold: int = 1000
    stars_medium_threshold: int = 500
    upvotes_high_threshold: int = 200
    upvotes_medium_threshold: int = 100

    # Social proof boosts
    social_high_boost: float = 0.02
    social_medium_boost: float = 0.01

    # Maximum total boost from all enrichment factors
    max_total_boost: float = 0.05


@dataclass
class EnrichmentBoost:
    """
    Result of enrichment boost calculation.

    Contains individual component boosts and the capped total.
    Preserves source metrics (age days, social score) for transparency.
    """

    company_age_boost: float
    social_proof_boost: float
    total_boost: float
    company_age_days: int
    social_proof_score: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization/logging."""
        return {
            "company_age_boost": self.company_age_boost,
            "social_proof_boost": self.social_proof_boost,
            "total_boost": self.total_boost,
            "company_age_days": self.company_age_days,
            "social_proof_score": self.social_proof_score,
        }


class EnrichmentBoostCalculator:
    """
    Calculates confidence boosts from enrichment data.

    Uses threshold-based scoring to convert company age and social proof
    metrics into additive confidence boosts. The total boost is capped
    to prevent over-inflation.

    Threshold-based scoring:
    - Company age >= 730 days: +0.03
    - Company age >= 365 days: +0.02
    - Stars >= 1000 OR upvotes >= 200: +0.02
    - Stars >= 500 OR upvotes >= 100: +0.01
    - Total capped at max_total_boost (0.05)
    """

    def __init__(self, config: Optional[EnrichmentConfig] = None):
        """
        Initialize calculator with optional custom config.

        Args:
            config: Custom EnrichmentConfig, or None for defaults
        """
        self.config = config or EnrichmentConfig()

    def calculate(self, consolidated: "ConsolidatedSignal") -> EnrichmentBoost:
        """
        Calculate enrichment boost from consolidated signal.

        Args:
            consolidated: ConsolidatedSignal with founding_date and social_proof

        Returns:
            EnrichmentBoost with component and total boosts
        """
        # Calculate individual boosts
        age_boost, age_days = self._calculate_age_boost(consolidated.founding_date)
        social_boost, social_score = self._calculate_social_proof_boost(
            consolidated.social_proof
        )

        # Calculate capped total
        raw_total = age_boost + social_boost
        capped_total = min(raw_total, self.config.max_total_boost)

        return EnrichmentBoost(
            company_age_boost=age_boost,
            social_proof_boost=social_boost,
            total_boost=capped_total,
            company_age_days=age_days,
            social_proof_score=social_score,
        )

    def _calculate_age_boost(
        self, founding_date: Optional[datetime]
    ) -> Tuple[float, int]:
        """
        Calculate boost based on company age.

        Args:
            founding_date: Company founding/incorporation date, or None

        Returns:
            Tuple of (boost value, age in days)
        """
        if founding_date is None:
            return 0.0, 0

        # Calculate age in days
        now = datetime.now(timezone.utc)

        # Handle timezone-naive dates by assuming UTC
        if founding_date.tzinfo is None:
            founding_date = founding_date.replace(tzinfo=timezone.utc)

        age_delta = now - founding_date
        age_days = age_delta.days

        # Apply threshold-based scoring
        if age_days >= self.config.age_high_threshold_days:
            return self.config.age_high_boost, age_days
        elif age_days >= self.config.age_medium_threshold_days:
            return self.config.age_medium_boost, age_days
        else:
            return 0.0, age_days

    def _calculate_social_proof_boost(
        self, social_proof: Dict[str, int]
    ) -> Tuple[float, int]:
        """
        Calculate boost based on social proof metrics.

        Uses the best tier achieved across all metrics (stars OR upvotes).

        Args:
            social_proof: Dictionary with stars, upvotes, etc.

        Returns:
            Tuple of (boost value, max social score)
        """
        if not social_proof:
            return 0.0, 0

        stars = social_proof.get("stars", 0)
        upvotes = social_proof.get("upvotes", 0)

        # Calculate the score as max of stars and upvotes
        social_score = max(stars, upvotes)

        # Check for high tier (stars >= 1000 OR upvotes >= 200)
        if stars >= self.config.stars_high_threshold or upvotes >= self.config.upvotes_high_threshold:
            return self.config.social_high_boost, social_score

        # Check for medium tier (stars >= 500 OR upvotes >= 100)
        if stars >= self.config.stars_medium_threshold or upvotes >= self.config.upvotes_medium_threshold:
            return self.config.social_medium_boost, social_score

        # Below thresholds
        return 0.0, social_score
