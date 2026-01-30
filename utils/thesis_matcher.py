"""
Consumer Thesis Matcher - Keyword-based thesis fit scoring.

Matches companies against Press On Ventures' Consumer investment thesis:
- Consumer CPG: Food, beverage, snacks, beauty, personal care
- Consumer Health Tech: Fitness, wellness, mental health, supplements
- Travel & Hospitality: Travel booking, hospitality tech, restaurants
- Consumer Marketplaces: Consumer-facing two-sided markets

Usage:
    from utils.thesis_matcher import ThesisMatcher, ThesisFit

    matcher = ThesisMatcher()
    fit = matcher.score("Meal kit delivery startup")
    print(f"Thesis: {fit.thesis}, Score: {fit.score}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class ConsumerThesis(str, Enum):
    """Consumer investment thesis categories."""
    CONSUMER_CPG = "consumer_cpg"
    CONSUMER_HEALTH_TECH = "consumer_health_tech"
    TRAVEL_HOSPITALITY = "travel_hospitality"
    CONSUMER_MARKETPLACE = "consumer_marketplace"
    UNKNOWN = "unknown"


# Keyword lists for each thesis (weighted by specificity)
CONSUMER_KEYWORDS: Dict[ConsumerThesis, Dict[str, float]] = {
    ConsumerThesis.CONSUMER_CPG: {
        # High weight - specific CPG terms
        "meal kit": 0.9,
        "meal kits": 0.9,
        "beverage brand": 0.9,
        "food brand": 0.8,
        "snack brand": 0.8,
        "skincare brand": 0.9,
        "beauty brand": 0.8,
        "personal care": 0.8,
        "household products": 0.7,
        "cpg": 0.8,
        "d2c": 0.7,
        "dtc": 0.7,
        "direct to consumer": 0.7,
        # Medium weight - general terms
        "healthy": 0.4,
        "food": 0.4,
        "beverage": 0.5,
        "snack": 0.4,
        "drink": 0.4,
        "grocery": 0.5,
        "organic": 0.4,
        "vegan": 0.5,
        "plant-based": 0.5,
        "beauty": 0.4,
        "skincare": 0.5,
        "cosmetics": 0.5,
    },
    ConsumerThesis.CONSUMER_HEALTH_TECH: {
        # High weight - specific health tech terms
        "fitness app": 0.9,
        "wellness app": 0.9,
        "wellness platform": 0.8,
        "mental health app": 0.9,
        "health tracker": 0.8,
        "meditation app": 0.8,
        "sleep app": 0.8,
        "sleep tracking": 0.8,  # Added: "sleep tracking app" matches this
        "nutrition app": 0.7,
        "personalized workout": 0.8,  # Added for fitness app specificity
        # Medium weight - general terms
        "fitness": 0.5,
        "workout": 0.5,
        "wellness": 0.4,
        "meditation": 0.5,
        "sleep": 0.4,
        "supplements": 0.5,
        "vitamins": 0.4,
        "wearable": 0.5,
        "health app": 0.6,
        "mental health": 0.5,
        "therapy": 0.4,
        "guided relaxation": 0.5,  # Added for meditation/sleep apps
    },
    ConsumerThesis.TRAVEL_HOSPITALITY: {
        # High weight - specific travel terms
        "travel booking": 0.9,
        "hotel tech": 0.8,
        "hospitality tech": 0.8,
        "hospitality platform": 0.8,
        "restaurant tech": 0.8,
        "travel platform": 0.7,
        "vacation rental": 0.8,
        "experience booking": 0.7,
        # Medium weight - general terms
        "travel": 0.5,
        "booking": 0.4,
        "hotel": 0.5,
        "hospitality": 0.5,
        "restaurant": 0.4,
        "vacation": 0.4,
        "experiences": 0.4,
        "tourism": 0.5,
        "lodging": 0.5,
    },
    ConsumerThesis.CONSUMER_MARKETPLACE: {
        # High weight - specific marketplace terms
        "consumer marketplace": 0.9,
        "two-sided market": 0.8,
        "peer-to-peer": 0.7,
        "p2p marketplace": 0.8,
        "c2c marketplace": 0.8,
        "buyer seller": 0.6,
        # Medium weight - general terms
        "marketplace": 0.6,
        "e-commerce": 0.5,
        "delivery": 0.4,
        "subscription": 0.4,
        "shopping": 0.4,
        "resale": 0.5,
        "secondhand": 0.5,
        "rental": 0.4,
    },
}

# Negative signals - exclusions from thesis
NEGATIVE_KEYWORDS: Dict[str, float] = {
    # B2B/Enterprise
    "enterprise": 0.5,
    "b2b": 0.5,
    "saas platform": 0.4,
    "developer tool": 0.5,
    "api platform": 0.4,
    "api management": 0.5,  # Added per plan
    "devops": 0.5,
    "infrastructure": 0.4,
    "logistics platform": 0.5,  # Added: B2B logistics
    "logistics": 0.3,  # Added: general logistics indicator
    "data platform": 0.4,  # Added per plan
    "sdk": 0.4,  # Added per plan
    # Crypto/Web3
    "blockchain": 0.5,
    "crypto": 0.5,
    "web3": 0.5,
    "nft": 0.5,
    "defi": 0.5,
    "token": 0.3,
    # Other exclusions
    "consulting": 0.4,
    "agency": 0.4,
    "services firm": 0.4,
    "series b": 0.3,
    "series c": 0.4,
    "series d": 0.5,
    "aggregator": 0.2,  # Added: weak signal for price aggregators
}


@dataclass
class ThesisFit:
    """Result of thesis matching."""
    thesis: ConsumerThesis
    score: float  # 0.0-1.0
    matched_keywords: List[str]
    negative_keywords: List[str]
    all_scores: Dict[str, float]  # Score per thesis
    confidence: str  # HIGH, MEDIUM, LOW

    @property
    def is_fit(self) -> bool:
        """Returns True if score indicates good thesis fit."""
        return self.score >= 0.4

    def to_dict(self) -> Dict:
        return {
            "thesis": self.thesis.value,
            "score": round(self.score, 3),
            "matched_keywords": self.matched_keywords,
            "negative_keywords": self.negative_keywords,
            "all_scores": {k: round(v, 3) for k, v in self.all_scores.items()},
            "confidence": self.confidence,
            "is_fit": self.is_fit,
        }


class ThesisMatcher:
    """
    Matches company descriptions against Consumer investment thesis.

    Uses keyword matching with weights to score thesis fit.
    Returns the best-matching thesis with a confidence score.
    """

    def __init__(
        self,
        custom_keywords: Optional[Dict[ConsumerThesis, Dict[str, float]]] = None,
    ):
        self.keywords = {k: dict(v) for k, v in CONSUMER_KEYWORDS.items()}
        if custom_keywords:
            for thesis, kws in custom_keywords.items():
                if thesis in self.keywords:
                    self.keywords[thesis].update(kws)
                else:
                    self.keywords[thesis] = kws

    def score(
        self,
        text: str,
        company_name: Optional[str] = None,
    ) -> ThesisFit:
        """Score text against all Consumer thesis categories."""
        if not text:
            return ThesisFit(
                thesis=ConsumerThesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
            )

        normalized = self._normalize(text)
        if company_name:
            normalized += " " + self._normalize(company_name)

        # Score each thesis
        scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for thesis, keywords in self.keywords.items():
            score, matches = self._score_thesis(normalized, keywords)
            scores[thesis.value] = score
            all_matches[thesis.value] = matches

        # Find negative keywords
        negative_matches = self._find_negative_keywords(normalized)

        # Find best thesis
        if scores:
            best_thesis_name = max(scores, key=scores.get)
            best_score = scores[best_thesis_name]
            best_thesis = ConsumerThesis(best_thesis_name)
            matched_kws = all_matches.get(best_thesis_name, [])
        else:
            best_thesis = ConsumerThesis.UNKNOWN
            best_score = 0.0
            matched_kws = []

        # Apply negative penalty
        if negative_matches:
            penalty = sum(NEGATIVE_KEYWORDS.get(kw, 0.2) for kw in negative_matches)
            best_score = max(0.0, best_score - penalty * 0.5)

        # Determine confidence
        if best_score >= 0.7:
            confidence = "HIGH"
        elif best_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return ThesisFit(
            thesis=best_thesis if best_score > 0.1 else ConsumerThesis.UNKNOWN,
            score=best_score,
            matched_keywords=matched_kws,
            negative_keywords=negative_matches,
            all_scores=scores,
            confidence=confidence,
        )

    def _normalize(self, text: str) -> str:
        return text.lower().strip()

    def _score_thesis(
        self,
        text: str,
        keywords: Dict[str, float],
    ) -> tuple[float, List[str]]:
        matches: List[str] = []
        total_weight = 0.0
        max_possible = sum(keywords.values())

        for keyword, weight in keywords.items():
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
                total_weight += weight

        if max_possible > 0:
            # Normalize score: matching ~15% of keyword weight gives 1.0
            # This allows single high-weight keywords (0.9) to score well
            score = min(total_weight / (max_possible * 0.15), 1.0)
        else:
            score = 0.0

        return score, matches

    def _find_negative_keywords(self, text: str) -> List[str]:
        matches = []
        for keyword in NEGATIVE_KEYWORDS:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
        return matches

    def score_signals(self, signals: List[Dict]) -> ThesisFit:
        """Score a list of signals to determine thesis fit."""
        texts = []
        company_name = None

        for signal in signals:
            raw = signal.get("raw_data", {}) if isinstance(signal, dict) else {}
            for field in ["description", "short_description", "about", "bio"]:
                if field in raw and raw[field]:
                    texts.append(str(raw[field]))
            if "company_name" in raw and not company_name:
                company_name = raw["company_name"]

        combined_text = " ".join(texts)
        return self.score(combined_text, company_name=company_name)


def score_thesis_fit(text: str, company_name: Optional[str] = None) -> ThesisFit:
    """Convenience function to score thesis fit."""
    matcher = ThesisMatcher()
    return matcher.score(text, company_name)


def is_thesis_fit(text: str, min_score: float = 0.4) -> bool:
    """Quick check if text matches investment thesis."""
    fit = score_thesis_fit(text)
    return fit.score >= min_score


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI for testing thesis matcher."""
    test_cases = [
        "We make healthy meal kits delivered to your door",
        "A fitness app for tracking your workouts and wellness",
        "Travel booking platform for unique hotel experiences",
        "Consumer marketplace connecting buyers and sellers",
        "Enterprise B2B SaaS platform for developers",
        "Premium skincare brand with d2c subscription model",
        "Blockchain crypto trading platform",
        "Mental health app for meditation and therapy",
        "Restaurant tech platform for hospitality businesses",
        "P2P marketplace for secondhand fashion resale",
    ]

    matcher = ThesisMatcher()

    print("=" * 70)
    print("CONSUMER THESIS MATCHER TEST")
    print("=" * 70)

    for text in test_cases:
        fit = matcher.score(text)
        marker = "[FIT]" if fit.is_fit else "[---]"
        print(f"\n{marker} {text[:55]}...")
        print(f"   Thesis: {fit.thesis.value}")
        print(f"   Score: {fit.score:.2f} ({fit.confidence})")
        print(f"   Matched: {', '.join(fit.matched_keywords[:5])}")
        if fit.negative_keywords:
            print(f"   Negative: {', '.join(fit.negative_keywords)}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
