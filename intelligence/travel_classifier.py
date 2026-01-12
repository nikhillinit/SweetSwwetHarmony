"""
TravelClassifier: Domain-specific LLM classifier for travel & hospitality signals.

Classifies travel signals with expertise in:
- Hotel tech (property management, guest experience, revenue management)
- Booking platforms (OTAs, direct booking, channel managers)
- Experiential travel (tours, activities, experiences)
- Travel infrastructure (payments, distribution, data)
- Rental tech (vacation rentals, property management)

Uses weighted thesis scoring based on configurable rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import re

from intelligence.thesis_config import load_thesis_config, ThesisConfig


TRAVEL_CLASSIFIER_SYSTEM_PROMPT = """You are an expert investment analyst specializing in travel and hospitality technology.

Your task is to evaluate signals about companies and determine their fit for a travel tech investment thesis.

## Investment Thesis Focus

IN SCOPE (evaluate positively):
- Hotel tech: property management systems, guest experience platforms, revenue management
- Booking platforms: OTAs, direct booking engines, channel managers, reservation systems
- Experiential travel: tour operators, activity platforms, experience marketplaces
- Travel infrastructure: payments, distribution APIs, travel data platforms
- Rental tech: vacation rental management, short-term rental platforms

OUT OF SCOPE (mark as out_of_scope):
- Legacy GDS systems without tech differentiation
- Traditional travel agencies without tech platform
- Brick and mortar only businesses
- Late-stage companies (Series C+, public, acquired)

## Investment Stage Focus

Target stages:
- Pre-seed and Seed stage companies
- Series A companies

Excluded stages:
- Series B and later
- Public companies
- Acquired companies

## Classification Output

For each signal, provide:
1. fit_score (0-1): How well does this match our travel tech thesis? (0.0 = no fit, 1.0 = perfect fit)
2. category: hotel_tech, booking_platform, experiential, travel_infrastructure, rental_tech, or out_of_scope
3. sub_category: More specific categorization (e.g., "property_management", "channel_manager")
4. thesis_alignment: 2-3 sentences explaining your assessment
5. signals: List of keywords/signals detected in the content
6. confidence (0-1): How confident are you in this classification? (0.0 = uncertain, 1.0 = certain)
7. is_tech_enabled: true if software/platform/SaaS, false if traditional business
8. investment_stage_fit: seed, series_a, stage_mismatch, or not_fit
9. regulatory_stage: travel-specific regulatory status if applicable

Focus on identifying genuine travel technology companies with scalable software platforms."""


class TravelCategory(Enum):
    """Categories within the travel & hospitality vertical."""
    HOTEL_TECH = "hotel_tech"
    BOOKING_PLATFORM = "booking_platform"
    EXPERIENTIAL = "experiential"
    TRAVEL_INFRASTRUCTURE = "travel_infrastructure"
    RENTAL_TECH = "rental_tech"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass
class TravelClassifierConfig:
    """Configuration for TravelClassifier."""
    model: str = "claude-3-haiku-20240307"
    min_confidence: float = 0.7
    temperature: float = 0.2
    max_tokens: int = 500
    api_key: Optional[str] = None


@dataclass
class TravelClassificationResult:
    """Result of travel signal classification."""
    fit_score: float  # 0-1 scale
    category: TravelCategory
    sub_category: Optional[str]
    thesis_alignment: str
    signals: List[str]
    confidence: float  # 0-1 scale
    is_tech_enabled: bool = True
    investment_stage_fit: str = "not_fit"
    regulatory_stage: Optional[str] = None


class TravelClassifier:
    """Domain-specific classifier for travel & hospitality vertical signals."""

    # Keywords for category detection
    HOTEL_KEYWORDS = ["hotel", "hospitality", "property management", "guest experience", "revenue management"]
    BOOKING_KEYWORDS = ["booking", "reservation", "ota", "channel manager"]
    EXPERIENTIAL_KEYWORDS = ["tour", "experience", "activity", "adventure", "concierge"]
    RENTAL_KEYWORDS = ["rental", "vacation rental", "vrbo", "airbnb", "short-term rental"]
    INFRASTRUCTURE_KEYWORDS = ["distribution", "gds", "travel data", "travel api"]

    # Tech-enabled keywords
    TECH_KEYWORDS = ["software", "platform", "app", "tech", "ai", "saas", "api", "digital", "cloud", "machine learning"]
    NON_TECH_KEYWORDS = ["brick and mortar", "traditional", "legacy", "non-tech", "offline only"]

    # Stage keywords
    SEED_KEYWORDS = ["seed", "pre-seed", "angel", "early-stage", "seed-stage"]
    SERIES_A_KEYWORDS = ["series a", "series-a"]
    EXCLUDED_STAGE_KEYWORDS = ["series b", "series c", "series d", "ipo", "public company", "acquired"]

    def __init__(self, config: Optional[TravelClassifierConfig] = None):
        """Initialize the travel classifier with optional config."""
        self.config = config or TravelClassifierConfig()
        self._thesis_config: Optional[ThesisConfig] = None

    @property
    def thesis_config(self) -> ThesisConfig:
        """Lazy load thesis configuration."""
        if self._thesis_config is None:
            self._thesis_config = load_thesis_config("travel")
        return self._thesis_config

    def _compute_signal_score(self, content: str, category: str) -> float:
        """
        Compute score for a specific signal category.

        Args:
            content: The text content to analyze
            category: The signal category (distribution, category, traction, founder)

        Returns:
            Score from 0.0 to 1.0 based on signal matches
        """
        if not content:
            return 0.0

        content_lower = content.lower()
        signals = self.thesis_config.positive_signals.get(category, [])

        if not signals:
            return 0.0

        matches = 0
        for signal in signals:
            # Use word boundary matching for more accurate detection
            pattern = r'\b' + re.escape(signal.lower()) + r'\b'
            if re.search(pattern, content_lower):
                matches += 1

        # Normalize score: cap at 1.0, give partial credit for multiple matches
        # Each match contributes, but with diminishing returns
        if matches == 0:
            return 0.0
        elif matches == 1:
            return 0.4
        elif matches == 2:
            return 0.7
        else:
            return min(1.0, 0.7 + (matches - 2) * 0.1)

    def _compute_weighted_fit_score(self, content: str) -> float:
        """
        Compute weighted fit score using thesis config.

        Args:
            content: The text content to analyze

        Returns:
            Weighted score from 0.0 to 1.0
        """
        if not content:
            return 0.0

        weights = self.thesis_config.scoring_weights
        total_score = 0.0

        # Compute score for each weighted category
        for category, weight in weights.items():
            category_score = self._compute_signal_score(content, category)
            total_score += category_score * weight

        # Apply negative signal penalty
        content_lower = content.lower()
        negative_count = 0
        for signal in self.thesis_config.negative_signals:
            pattern = r'\b' + re.escape(signal.lower()) + r'\b'
            if re.search(pattern, content_lower):
                negative_count += 1

        # Penalty: reduce score by 15% for each negative signal, minimum 0
        if negative_count > 0:
            penalty = negative_count * 0.15
            total_score = max(0.0, total_score - penalty)

        return min(1.0, total_score)

    def _detect_category(self, content: str) -> TravelCategory:
        """
        Detect the travel category based on keyword matches.

        Args:
            content: The text content to analyze

        Returns:
            TravelCategory enum value
        """
        content_lower = content.lower()

        # Count matches for each category
        scores = {
            TravelCategory.HOTEL_TECH: sum(1 for kw in self.HOTEL_KEYWORDS if kw in content_lower),
            TravelCategory.BOOKING_PLATFORM: sum(1 for kw in self.BOOKING_KEYWORDS if kw in content_lower),
            TravelCategory.EXPERIENTIAL: sum(1 for kw in self.EXPERIENTIAL_KEYWORDS if kw in content_lower),
            TravelCategory.RENTAL_TECH: sum(1 for kw in self.RENTAL_KEYWORDS if kw in content_lower),
            TravelCategory.TRAVEL_INFRASTRUCTURE: sum(1 for kw in self.INFRASTRUCTURE_KEYWORDS if kw in content_lower),
        }

        # Find the best match
        max_score = max(scores.values())
        if max_score == 0:
            return TravelCategory.OUT_OF_SCOPE

        # Return the category with the highest score
        for category, score in scores.items():
            if score == max_score:
                return category

        return TravelCategory.OUT_OF_SCOPE

    def _detect_tech_enabled(self, content: str) -> bool:
        """
        Detect if the company is tech-enabled.

        Args:
            content: The text content to analyze

        Returns:
            True if tech-enabled, False otherwise
        """
        content_lower = content.lower()

        # Check for non-tech keywords first
        for kw in self.NON_TECH_KEYWORDS:
            if kw in content_lower:
                return False

        # Check for tech keywords
        for kw in self.TECH_KEYWORDS:
            if kw in content_lower:
                return True

        # Default to True if no clear indicators
        return True

    def _detect_investment_stage(self, content: str) -> str:
        """
        Detect the investment stage fit.

        Args:
            content: The text content to analyze

        Returns:
            Stage fit: "seed", "series_a", "stage_mismatch", or "not_fit"
        """
        content_lower = content.lower()

        # Check for excluded stages first
        for kw in self.EXCLUDED_STAGE_KEYWORDS:
            if kw in content_lower:
                return "stage_mismatch"

        # Check for seed stage
        for kw in self.SEED_KEYWORDS:
            if kw in content_lower:
                return "seed"

        # Check for Series A
        for kw in self.SERIES_A_KEYWORDS:
            if kw in content_lower:
                return "series_a"

        return "not_fit"

    def _extract_signals(self, content: str) -> List[str]:
        """
        Extract detected signals from content.

        Args:
            content: The text content to analyze

        Returns:
            List of detected signal strings
        """
        signals = []
        content_lower = content.lower()

        # Check all positive signal categories
        for category, category_signals in self.thesis_config.positive_signals.items():
            for signal in category_signals:
                pattern = r'\b' + re.escape(signal.lower()) + r'\b'
                if re.search(pattern, content_lower):
                    signals.append(signal)

        return signals

    async def classify(
        self,
        content: str,
        company_name: Optional[str] = None,
        source: Optional[str] = None,
    ) -> TravelClassificationResult:
        """
        Classify a travel signal using weighted thesis scoring.

        Args:
            content: Signal text to classify
            company_name: Optional company name for context
            source: Optional source identifier

        Returns:
            TravelClassificationResult with fit score and category
        """
        # Compute weighted fit score
        fit_score = self._compute_weighted_fit_score(content)

        # Detect category
        category = self._detect_category(content)

        # Detect if tech-enabled
        is_tech_enabled = self._detect_tech_enabled(content)

        # Detect investment stage
        investment_stage_fit = self._detect_investment_stage(content)

        # Extract signals
        signals = self._extract_signals(content)

        # Determine sub-category based on main category
        sub_category = self._determine_sub_category(content, category)

        # Generate thesis alignment text
        thesis_alignment = self._generate_thesis_alignment(
            fit_score, category, is_tech_enabled, investment_stage_fit
        )

        # Compute confidence based on signal clarity
        confidence = self._compute_confidence(fit_score, len(signals), category)

        return TravelClassificationResult(
            fit_score=fit_score,
            category=category,
            sub_category=sub_category,
            thesis_alignment=thesis_alignment,
            signals=signals,
            confidence=confidence,
            is_tech_enabled=is_tech_enabled,
            investment_stage_fit=investment_stage_fit,
            regulatory_stage=None,
        )

    def _determine_sub_category(self, content: str, category: TravelCategory) -> Optional[str]:
        """Determine sub-category based on content and main category."""
        content_lower = content.lower()

        sub_category_map = {
            TravelCategory.HOTEL_TECH: {
                "property management": "property_management",
                "guest experience": "guest_experience",
                "revenue management": "revenue_management",
            },
            TravelCategory.BOOKING_PLATFORM: {
                "channel manager": "channel_manager",
                "ota": "online_travel_agency",
                "direct booking": "direct_booking",
            },
            TravelCategory.EXPERIENTIAL: {
                "tour": "tours",
                "activity": "activities",
                "adventure": "adventure_travel",
            },
            TravelCategory.RENTAL_TECH: {
                "vacation rental": "vacation_rental",
                "short-term rental": "short_term_rental",
            },
        }

        if category in sub_category_map:
            for keyword, sub_cat in sub_category_map[category].items():
                if keyword in content_lower:
                    return sub_cat

        return None

    def _generate_thesis_alignment(
        self,
        fit_score: float,
        category: TravelCategory,
        is_tech_enabled: bool,
        investment_stage_fit: str,
    ) -> str:
        """Generate thesis alignment explanation."""
        if category == TravelCategory.OUT_OF_SCOPE:
            return "This company does not appear to fit within the travel tech thesis scope."

        if not is_tech_enabled:
            return "This company operates in the travel space but lacks clear tech differentiation."

        if investment_stage_fit == "stage_mismatch":
            return f"This {category.value} company is at a later stage than our target investment focus."

        if fit_score >= 0.7:
            return f"Strong thesis fit. This {category.value} company shows clear alignment with our travel tech investment focus."
        elif fit_score >= 0.4:
            return f"Moderate thesis fit. This {category.value} company has some alignment with our travel tech thesis."
        else:
            return f"Limited thesis fit. While in the {category.value} space, this company shows minimal alignment with our specific investment criteria."

    def _compute_confidence(
        self,
        fit_score: float,
        signal_count: int,
        category: TravelCategory,
    ) -> float:
        """Compute classification confidence."""
        # Base confidence on signal count
        if signal_count == 0:
            base_confidence = 0.3
        elif signal_count <= 2:
            base_confidence = 0.5
        elif signal_count <= 5:
            base_confidence = 0.7
        else:
            base_confidence = 0.85

        # Adjust based on category clarity
        if category == TravelCategory.OUT_OF_SCOPE and fit_score < 0.2:
            base_confidence += 0.1  # High confidence in out of scope classification

        return min(1.0, base_confidence)
