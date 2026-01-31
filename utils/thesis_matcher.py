"""
Consumer Thesis Matcher - Keyword-based thesis fit scoring.

Matches companies against Press On Ventures' Consumer investment thesis:
- Consumer CPG: Food, beverage, snacks, beauty, personal care
- Consumer Health Tech: Fitness, wellness, mental health, supplements
- Travel & Hospitality: Travel booking, hospitality tech, restaurants
- Consumer Marketplaces: Consumer-facing two-sided markets

Phase B Enhancements (from founder_intel_canonical):
- Intent phrases: "join waitlist", "private beta", "pricing", etc.
- Domain regex patterns: get*, try*, join* consumer domains
- Domain blacklist: localhost, example, staging, etc.
- Additional negative keywords: boilerplate, template, tutorial, etc.

Usage:
    from utils.thesis_matcher import ThesisMatcher, ThesisFit

    matcher = ThesisMatcher()
    fit = matcher.score("Meal kit delivery startup")
    print(f"Thesis: {fit.thesis}, Score: {fit.score}")

    # With domain analysis
    fit = matcher.score("Health app", domain_name="getfitness.com")
    print(f"Domain match: {fit.domain_match}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    "api management": 0.5,
    "devops": 0.5,
    "infrastructure": 0.4,
    "logistics platform": 0.5,
    "logistics": 0.3,
    "data platform": 0.4,
    "sdk": 0.4,
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
    "aggregator": 0.2,
    # Phase B: Template/Educational content (from founder_intel_canonical)
    "boilerplate": 0.6,
    "starter": 0.5,
    "template": 0.5,
    "tutorial": 0.5,
    "workshop": 0.4,
    "course": 0.4,
    "homework": 0.4,
    "assignment": 0.4,
    "example": 0.3,
    "demo repo": 0.5,
    # Phase B: Developer tools (from founder_intel_canonical)
    "cli": 0.4,
    "library": 0.4,
    "framework": 0.4,
    "plugin": 0.4,
    "linter": 0.5,
}

# Intent phrases that indicate commercial/consumer intent (Phase B)
INTENT_PHRASES: Dict[str, float] = {
    "join waitlist": 0.3,
    "request access": 0.3,
    "private beta": 0.3,
    "coming soon": 0.2,
    "book a demo": 0.2,
    "sign up": 0.2,
    "get started": 0.2,
    "pricing": 0.25,
    "subscribe": 0.2,
}

# Regex patterns for consumer-oriented domains (Phase B)
CONSUMER_DOMAIN_PATTERNS: List[str] = [
    r"^get[a-z0-9-]{3,}$",  # getmyapp.com
    r"^try[a-z0-9-]{3,}$",  # tryproduct.io
    r"^join[a-z0-9-]{3,}$",  # joincommunity.co
]

# Domain blacklist fragments for non-production domains (Phase B)
DOMAIN_BLACKLIST_FRAGMENTS: List[str] = [
    "localhost",
    "example",
    "sample",
    "test",
    "staging",
    "dev.",
    "internal",
]


@dataclass
class ThesisFit:
    """Result of thesis matching."""
    thesis: ConsumerThesis
    score: float  # 0.0-1.0
    matched_keywords: List[str]
    negative_keywords: List[str]
    all_scores: Dict[str, float]  # Score per thesis
    confidence: str  # HIGH, MEDIUM, LOW
    # Phase B additions
    intent_phrases_matched: List[str] = field(default_factory=list)
    domain_match: bool = False
    domain_blacklisted: bool = False

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
            # Phase B additions
            "intent_phrases_matched": self.intent_phrases_matched,
            "domain_match": self.domain_match,
            "domain_blacklisted": self.domain_blacklisted,
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
        domain_name: Optional[str] = None,
    ) -> ThesisFit:
        """Score text against all Consumer thesis categories.

        Args:
            text: Company description or README content
            company_name: Optional company name for additional context
            domain_name: Optional domain for pattern matching (Phase B)

        Returns:
            ThesisFit with score, matched keywords, and domain analysis
        """
        if not text:
            return ThesisFit(
                thesis=ConsumerThesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
                intent_phrases_matched=[],
                domain_match=False,
                domain_blacklisted=False,
            )

        normalized = self._normalize(text)
        if company_name:
            normalized += " " + self._normalize(company_name)

        # Phase B: Check domain blacklist first
        domain_blacklisted = self._check_domain_blacklist(domain_name)
        if domain_blacklisted:
            return ThesisFit(
                thesis=ConsumerThesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
                intent_phrases_matched=[],
                domain_match=False,
                domain_blacklisted=True,
            )

        # Score each thesis
        scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for thesis, keywords in self.keywords.items():
            score, matches = self._score_thesis(normalized, keywords)
            scores[thesis.value] = score
            all_matches[thesis.value] = matches

        # Find negative keywords
        negative_matches = self._find_negative_keywords(normalized)

        # Phase B: Find intent phrases
        intent_matches = self._find_intent_phrases(normalized)

        # Phase B: Check domain patterns
        domain_match = self._check_domain_patterns(domain_name)

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

        # Phase B: Apply intent phrase boost
        if intent_matches:
            intent_boost = sum(INTENT_PHRASES.get(phrase, 0.1) for phrase in intent_matches)
            best_score = min(1.0, best_score + intent_boost)

        # Phase B: Apply domain pattern boost
        if domain_match:
            best_score = min(1.0, best_score + 0.15)

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
            intent_phrases_matched=intent_matches,
            domain_match=domain_match,
            domain_blacklisted=False,
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

    def _find_intent_phrases(self, text: str) -> List[str]:
        """Find intent phrases that indicate commercial/consumer intent (Phase B)."""
        matches = []
        for phrase in INTENT_PHRASES:
            pattern = r'\b' + re.escape(phrase) + r'\b'
            if re.search(pattern, text):
                matches.append(phrase)
        return matches

    def _check_domain_patterns(self, domain_name: Optional[str]) -> bool:
        """Check if domain matches consumer-oriented patterns (Phase B).

        Patterns like get*, try*, join* indicate consumer-focused apps.
        """
        if not domain_name:
            return False

        # Extract just the domain name without TLD
        domain_lower = domain_name.lower()
        # Remove protocol if present
        if "://" in domain_lower:
            domain_lower = domain_lower.split("://")[1]
        # Remove port if present
        if ":" in domain_lower:
            domain_lower = domain_lower.split(":")[0]
        # Get the first part (subdomain or main domain)
        parts = domain_lower.split(".")
        if not parts:
            return False

        # Check against patterns (typically check the main domain, not TLD)
        # For "getfitness.com", check "getfitness"
        main_domain = parts[0] if len(parts) > 1 else parts[0]

        for pattern in CONSUMER_DOMAIN_PATTERNS:
            if re.match(pattern, main_domain):
                return True
        return False

    def _check_domain_blacklist(self, domain_name: Optional[str]) -> bool:
        """Check if domain is blacklisted (non-production) (Phase B).

        Blacklist fragments: localhost, example, sample, test, staging, dev., internal
        """
        if not domain_name:
            return False

        domain_lower = domain_name.lower()
        for fragment in DOMAIN_BLACKLIST_FRAGMENTS:
            if fragment in domain_lower:
                return True
        return False

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
