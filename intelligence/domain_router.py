"""
DomainRouter: Routes signals to vertical-specific classifiers.

Two-stage classification:
1. Fast keyword-based domain detection (free, synchronous)
2. Vertical-specific LLM classification (cost per call, async)

Supported domains: health, travel, saas, consumer
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Domain(Enum):
    """Investment verticals supported by the platform."""
    HEALTH = "health"
    TRAVEL = "travel"
    SAAS = "saas"
    CONSUMER = "consumer"
    UNKNOWN = "unknown"


# Keyword patterns for domain detection
# Higher weight = stronger signal for that domain
HEALTH_KEYWORDS: Dict[str, float] = {
    # Regulatory
    "fda": 1.0,
    "fda-cleared": 1.0,
    "fda-approved": 1.0,
    "510k": 1.0,
    "clinical trial": 0.9,
    "phase 1": 0.8,
    "phase 2": 0.8,
    "phase 3": 0.8,
    "hipaa": 0.8,

    # Health tech categories
    "telehealth": 0.9,
    "telemedicine": 0.9,
    "digital health": 0.9,
    "health tech": 0.9,
    "healthtech": 0.9,
    "medtech": 0.9,
    "biotech": 0.7,

    # Devices and wearables
    "wearable": 0.7,
    "medical device": 0.9,
    "health monitor": 0.8,
    "fitness tracker": 0.6,

    # Services
    "virtual care": 0.9,
    "remote patient": 0.9,
    "patient monitoring": 0.9,
    "health platform": 0.7,
    "wellness app": 0.6,
    "mental health": 0.8,
    "fertility": 0.8,

    # Conditions (consumer health focus)
    "cardiac": 0.7,
    "cardiovascular": 0.7,
    "diabetes": 0.7,
    "chronic care": 0.8,
}


@dataclass
class DomainResult:
    """Result of domain detection."""
    primary_domain: Domain
    confidence: float
    secondary_domains: List[Domain] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)


class DomainRouter:
    """Routes signals to vertical-specific classifiers."""

    def __init__(self):
        """Initialize the domain router with keyword patterns."""
        self.health_keywords = HEALTH_KEYWORDS
        # Future: self.travel_keywords, self.saas_keywords, etc.

    def _match_keywords(
        self, content: str, keywords: Dict[str, float]
    ) -> Tuple[float, List[str]]:
        """
        Match content against keyword dictionary.

        Returns:
            Tuple of (max_score, matched_keywords)
        """
        content_lower = content.lower()
        matched = []
        max_score = 0.0

        for keyword, weight in keywords.items():
            # Use word boundary matching for accuracy
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, content_lower):
                matched.append(keyword)
                max_score = max(max_score, weight)

        return max_score, matched

    def detect_domain(self, content: str, source: Optional[str] = None) -> DomainResult:
        """
        Detect the domain of a signal using keyword matching.

        Args:
            content: Signal text content to analyze
            source: Optional source identifier (e.g., "producthunt_health")

        Returns:
            DomainResult with primary domain and confidence
        """
        # Check health keywords
        health_score, health_matches = self._match_keywords(content, self.health_keywords)

        # Source-based detection and boost
        source_is_health = source and "health" in source.lower()
        if source_is_health:
            health_score = max(0.5, health_score)  # Minimum 0.5 for health sources
            health_score = min(1.0, health_score + 0.2)  # Boost existing score

        # Determine primary domain
        if health_score >= 0.5:
            return DomainResult(
                primary_domain=Domain.HEALTH,
                confidence=health_score,
                secondary_domains=[],
                matched_keywords=health_matches
            )

        return DomainResult(
            primary_domain=Domain.UNKNOWN,
            confidence=0.0,
            secondary_domains=[],
            matched_keywords=[]
        )
