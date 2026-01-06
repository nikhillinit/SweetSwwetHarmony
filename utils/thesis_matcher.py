"""
Thesis Matcher - Keyword-based thesis fit scoring.

Matches companies against Press On Ventures' investment thesis:
- Healthtech
- Cleantech
- AI Infrastructure

Usage:
    from utils.thesis_matcher import ThesisMatcher, ThesisFit

    matcher = ThesisMatcher()
    fit = matcher.score("AI startup building inference optimization")
    print(f"Thesis: {fit.thesis}, Score: {fit.score}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set


# =============================================================================
# THESIS DEFINITIONS
# =============================================================================

class Thesis(str, Enum):
    """Investment thesis categories."""
    AI_INFRASTRUCTURE = "ai_infrastructure"
    HEALTHTECH = "healthtech"
    CLEANTECH = "cleantech"
    UNKNOWN = "unknown"


# Keyword lists for each thesis (weighted by specificity)
THESIS_KEYWORDS: Dict[Thesis, Dict[str, float]] = {
    Thesis.AI_INFRASTRUCTURE: {
        # Core AI infra (high weight)
        "llm": 0.9,
        "large language model": 0.9,
        "inference": 0.8,
        "vector database": 0.9,
        "embedding": 0.7,
        "ml ops": 0.8,
        "mlops": 0.8,
        "fine-tuning": 0.8,
        "fine tuning": 0.8,
        "model training": 0.7,
        "gpu": 0.5,
        "cuda": 0.6,
        "transformer": 0.7,
        "neural network": 0.5,
        "deep learning": 0.5,

        # AI tooling (medium weight)
        "ai platform": 0.6,
        "machine learning": 0.4,
        "ml platform": 0.7,
        "data pipeline": 0.5,
        "feature store": 0.8,
        "model serving": 0.8,
        "model deployment": 0.7,
        "ai api": 0.6,
        "prompt engineering": 0.6,
        "rag": 0.7,  # Retrieval augmented generation
        "retrieval augmented": 0.8,

        # Specific technologies (medium weight)
        "pytorch": 0.5,
        "tensorflow": 0.5,
        "langchain": 0.7,
        "openai api": 0.5,
        "anthropic api": 0.5,
        "hugging face": 0.6,
        "vertex ai": 0.5,
        "sagemaker": 0.5,
    },

    Thesis.HEALTHTECH: {
        # Clinical/Medical (high weight)
        "clinical trial": 0.9,
        "clinical": 0.6,
        "fda": 0.8,
        "fda approval": 0.9,
        "diagnostic": 0.7,
        "therapeutics": 0.8,
        "drug discovery": 0.9,
        "pharmaceutical": 0.6,
        "biotech": 0.6,
        "medical device": 0.8,
        "patient data": 0.7,
        "patient care": 0.6,
        "healthcare ai": 0.9,
        "clinical decision": 0.8,

        # Digital health (medium weight)
        "telehealth": 0.8,
        "telemedicine": 0.8,
        "digital health": 0.7,
        "health platform": 0.6,
        "electronic health record": 0.7,
        "ehr": 0.6,
        "emr": 0.6,
        "hipaa": 0.7,
        "health insurance": 0.5,
        "healthcare": 0.4,
        "hospital": 0.4,
        "physician": 0.5,
        "medical imaging": 0.8,
        "radiology": 0.7,

        # Wellness/Prevention (lower weight)
        "mental health": 0.6,
        "wellness": 0.4,
        "fitness": 0.3,
        "nutrition": 0.4,
        "health monitoring": 0.5,
        "wearable": 0.4,
    },

    Thesis.CLEANTECH: {
        # Climate/Carbon (high weight)
        "carbon capture": 0.9,
        "carbon offset": 0.8,
        "carbon credit": 0.8,
        "carbon footprint": 0.7,
        "net zero": 0.8,
        "climate tech": 0.9,
        "climate change": 0.5,
        "decarbonization": 0.9,
        "emissions reduction": 0.8,
        "greenhouse gas": 0.7,

        # Renewable energy (high weight)
        "renewable energy": 0.9,
        "solar energy": 0.8,
        "wind energy": 0.8,
        "battery storage": 0.8,
        "energy storage": 0.8,
        "ev charging": 0.7,
        "electric vehicle": 0.6,
        "clean energy": 0.8,
        "green energy": 0.7,
        "hydrogen fuel": 0.8,

        # ESG/Sustainability (medium weight)
        "esg": 0.6,
        "sustainability": 0.5,
        "sustainable": 0.4,
        "circular economy": 0.7,
        "waste reduction": 0.6,
        "recycling": 0.4,
        "green": 0.3,
        "eco-friendly": 0.4,

        # Specific technologies (medium weight)
        "smart grid": 0.7,
        "energy efficiency": 0.6,
        "building efficiency": 0.6,
        "water treatment": 0.6,
        "agtech": 0.5,
        "food tech": 0.5,
    },
}

# Negative signals (lower score if present)
NEGATIVE_KEYWORDS: Dict[str, float] = {
    "consumer app": 0.3,
    "social media": 0.5,
    "marketing": 0.3,
    "advertising": 0.3,
    "gaming": 0.4,
    "crypto": 0.4,
    "blockchain": 0.4,
    "nft": 0.5,
    "web3": 0.4,
    "real estate": 0.4,
    "fintech": 0.2,  # Not negative, just not in thesis
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ThesisFit:
    """Result of thesis matching."""
    thesis: Thesis
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


# =============================================================================
# MATCHER
# =============================================================================

class ThesisMatcher:
    """
    Matches company descriptions against investment thesis.

    Uses keyword matching with weights to score thesis fit.
    Returns the best-matching thesis with a confidence score.
    """

    def __init__(
        self,
        custom_keywords: Optional[Dict[Thesis, Dict[str, float]]] = None,
    ):
        """
        Args:
            custom_keywords: Optional custom keyword definitions to merge
        """
        self.keywords = dict(THESIS_KEYWORDS)

        # Merge custom keywords if provided
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
        sic_code: Optional[str] = None,
    ) -> ThesisFit:
        """
        Score text against all thesis categories.

        Args:
            text: Description, about text, or combined signals
            company_name: Optional company name for additional context
            sic_code: Optional SIC code for sector hint

        Returns:
            ThesisFit with best matching thesis
        """
        if not text:
            return ThesisFit(
                thesis=Thesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
            )

        # Normalize text
        normalized = self._normalize(text)
        if company_name:
            normalized += " " + self._normalize(company_name)

        # Score each thesis
        scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for thesis, keywords in self.keywords.items():
            score, matches = self._score_thesis(normalized, keywords)

            # Apply SIC code boost if available
            if sic_code:
                sic_boost = self._sic_boost(sic_code, thesis)
                score = min(score + sic_boost, 1.0)

            scores[thesis.value] = score
            all_matches[thesis.value] = matches

        # Find negative keywords
        negative_matches = self._find_negative_keywords(normalized)

        # Find best thesis
        if scores:
            best_thesis_name = max(scores, key=scores.get)
            best_score = scores[best_thesis_name]
            best_thesis = Thesis(best_thesis_name)
            matched_kws = all_matches.get(best_thesis_name, [])
        else:
            best_thesis = Thesis.UNKNOWN
            best_score = 0.0
            matched_kws = []

        # Apply negative penalty
        if negative_matches:
            penalty = sum(NEGATIVE_KEYWORDS.get(kw, 0.2) for kw in negative_matches)
            best_score = max(0.0, best_score - penalty * 0.3)

        # Determine confidence
        if best_score >= 0.7:
            confidence = "HIGH"
        elif best_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return ThesisFit(
            thesis=best_thesis if best_score > 0.1 else Thesis.UNKNOWN,
            score=best_score,
            matched_keywords=matched_kws,
            negative_keywords=negative_matches,
            all_scores=scores,
            confidence=confidence,
        )

    def _normalize(self, text: str) -> str:
        """Normalize text for matching."""
        return text.lower().strip()

    def _score_thesis(
        self,
        text: str,
        keywords: Dict[str, float],
    ) -> tuple[float, List[str]]:
        """Score text against a single thesis keyword set."""
        matches: List[str] = []
        total_weight = 0.0
        max_possible = sum(keywords.values())

        for keyword, weight in keywords.items():
            # Use word boundaries to avoid partial matches
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
                total_weight += weight

        # Normalize score (0-1)
        if max_possible > 0:
            # Don't require all keywords - just measure how much thesis signal
            # Cap at reasonable max (e.g., matching 40% of keywords = 1.0)
            score = min(total_weight / (max_possible * 0.4), 1.0)
        else:
            score = 0.0

        return score, matches

    def _find_negative_keywords(self, text: str) -> List[str]:
        """Find negative keywords in text."""
        matches = []
        for keyword in NEGATIVE_KEYWORDS:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
        return matches

    def _sic_boost(self, sic_code: str, thesis: Thesis) -> float:
        """Apply boost based on SIC code matching thesis."""
        # SIC code ranges for each thesis
        SIC_MAPPINGS = {
            Thesis.HEALTHTECH: [
                ("8000", "8099"),  # Health services
                ("2833", "2836"),  # Pharmaceuticals
                ("3841", "3845"),  # Medical instruments
            ],
            Thesis.CLEANTECH: [
                ("4911", "4941"),  # Electric, gas, sanitary services
                ("1311", "1389"),  # Oil and gas (for transition tech)
                ("4953", "4959"),  # Refuse systems
            ],
            Thesis.AI_INFRASTRUCTURE: [
                ("7370", "7379"),  # Computer services
                ("3571", "3579"),  # Computer equipment
                ("7372", "7372"),  # Prepackaged software
            ],
        }

        mappings = SIC_MAPPINGS.get(thesis, [])
        for start, end in mappings:
            if start <= sic_code <= end:
                return 0.15  # Small boost for matching SIC

        return 0.0

    def score_signals(self, signals: List[Dict]) -> ThesisFit:
        """
        Score a list of signals to determine thesis fit.

        Combines text from all signals for comprehensive matching.

        Args:
            signals: List of signal dicts with raw_data

        Returns:
            ThesisFit
        """
        # Combine all text from signals
        texts = []
        sic_code = None
        company_name = None

        for signal in signals:
            raw = signal.get("raw_data", {}) if isinstance(signal, dict) else {}

            # Get description fields
            for field in ["description", "short_description", "about", "bio"]:
                if field in raw and raw[field]:
                    texts.append(str(raw[field]))

            # Get company name
            if "company_name" in raw and not company_name:
                company_name = raw["company_name"]

            # Get SIC code
            if "sic_code" in raw and not sic_code:
                sic_code = raw["sic_code"]
            if "sic_codes" in raw and not sic_code:
                codes = raw["sic_codes"]
                if isinstance(codes, list) and codes:
                    sic_code = codes[0]

            # Get topics/tags
            if "topics" in raw:
                topics = raw["topics"]
                if isinstance(topics, list):
                    texts.extend(topics)

        combined_text = " ".join(texts)
        return self.score(combined_text, company_name=company_name, sic_code=sic_code)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def score_thesis_fit(
    text: str,
    company_name: Optional[str] = None,
    sic_code: Optional[str] = None,
) -> ThesisFit:
    """
    Convenience function to score thesis fit.

    Usage:
        fit = score_thesis_fit("AI startup building vector databases")
        print(f"Best fit: {fit.thesis}, Score: {fit.score}")
    """
    matcher = ThesisMatcher()
    return matcher.score(text, company_name, sic_code)


def is_thesis_fit(
    text: str,
    min_score: float = 0.4,
) -> bool:
    """
    Quick check if text matches investment thesis.

    Usage:
        if is_thesis_fit(description):
            print("Matches thesis!")
    """
    fit = score_thesis_fit(text)
    return fit.score >= min_score


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI for testing thesis matcher."""
    import sys

    test_cases = [
        "Building AI infrastructure for LLM inference optimization",
        "Healthcare platform for clinical decision support",
        "Carbon capture technology for industrial emissions",
        "Social media app for Gen Z",
        "Enterprise vector database for RAG applications",
        "Telehealth platform with FDA-approved diagnostics",
        "Renewable energy storage for EV charging networks",
        "Consumer fintech app for crypto trading",
        "ML ops platform for model deployment and serving",
        "Drug discovery using deep learning and transformers",
    ]

    matcher = ThesisMatcher()

    print("=" * 70)
    print("THESIS MATCHER TEST")
    print("=" * 70)

    for text in test_cases:
        fit = matcher.score(text)
        emoji = "✓" if fit.is_fit else "✗"
        print(f"\n{emoji} {text[:50]}...")
        print(f"   Thesis: {fit.thesis.value}")
        print(f"   Score: {fit.score:.2f} ({fit.confidence})")
        print(f"   Matched: {', '.join(fit.matched_keywords[:5])}")
        if fit.negative_keywords:
            print(f"   Negative: {', '.join(fit.negative_keywords)}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
