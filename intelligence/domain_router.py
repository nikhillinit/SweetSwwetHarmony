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

TRAVEL_KEYWORDS: Dict[str, float] = {
    # Core travel
    "hotel": 0.8,
    "hospitality": 0.9,
    "travel tech": 0.9,
    "traveltech": 0.9,
    "booking": 0.7,
    "reservation": 0.7,
    # Accommodations
    "property management": 0.8,
    "vacation rental": 0.8,
    "short-term rental": 0.8,
    "airbnb": 0.6,
    "vrbo": 0.6,
    # Experiences
    "tour operator": 0.8,
    "experiential travel": 0.9,
    "destination": 0.6,
    "concierge": 0.7,
    # B2B hotel tech
    "pms": 0.7,
    "guest experience": 0.8,
    "hotel operations": 0.8,
    "revenue management": 0.7,
    # Industry signals
    "phocuswright": 0.9,
    "skift": 0.9,
    "plug and play travel": 1.0,
}

SAAS_KEYWORDS: Dict[str, float] = {
    # Core SaaS
    "saas": 0.9,
    "software as a service": 0.9,
    "b2b software": 0.9,
    "b2b saas": 1.0,
    "enterprise software": 0.8,

    # GTM patterns
    "product-led growth": 0.8,
    "plg": 0.7,
    "freemium": 0.7,
    "self-serve": 0.6,

    # Vertical SaaS
    "vertical saas": 0.9,
    "industry-specific software": 0.8,

    # Developer tools
    "api platform": 0.8,
    "developer tools": 0.8,
    "devtools": 0.8,
    "sdk": 0.6,
    "integration platform": 0.7,

    # Enterprise
    "enterprise": 0.6,
    "workflow automation": 0.7,
    "business intelligence": 0.7,
    "crm": 0.7,
    "erp": 0.7,

    # Industry signals
    "g2crowd": 0.9,
    "g2": 0.7,
    "capterra": 0.9,
}

CONSUMER_KEYWORDS: Dict[str, float] = {
    # DTC/Brand
    "dtc": 0.9,
    "direct-to-consumer": 0.9,
    "d2c": 0.9,
    "cpg": 0.8,
    "consumer packaged goods": 0.8,
    "brand": 0.6,
    "retail": 0.5,

    # Premium positioning
    "premium": 0.7,
    "luxury": 0.7,
    "affluent": 0.7,
    "lifestyle brand": 0.8,

    # Product categories
    "beverage": 0.7,
    "food & beverage": 0.7,
    "nutrition": 0.6,
    "beauty": 0.6,
    "skincare": 0.6,
    "wellness brand": 0.8,

    # Marketplace/Platform
    "marketplace": 0.9,
    "two-sided marketplace": 1.0,
    "platform": 0.5,
    "community commerce": 0.9,
    "social commerce": 0.9,

    # Industry signals
    "producthunt": 0.6,
    "kickstarter": 0.7,
    "indiegogo": 0.7,
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
        self.travel_keywords = TRAVEL_KEYWORDS
        self.saas_keywords = SAAS_KEYWORDS
        self.consumer_keywords = CONSUMER_KEYWORDS

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

        # Check travel keywords
        travel_score, travel_matches = self._match_keywords(content, self.travel_keywords)

        # Check SaaS keywords
        saas_score, saas_matches = self._match_keywords(content, self.saas_keywords)

        # Check consumer keywords
        consumer_score, consumer_matches = self._match_keywords(content, self.consumer_keywords)

        # Source-based detection and boost for health
        source_lower = source.lower() if source else ""
        source_is_health = "health" in source_lower
        if source_is_health:
            health_score = max(0.5, health_score)  # Minimum 0.5 for health sources
            health_score = min(1.0, health_score + 0.2)  # Boost existing score

        # Source-based detection and boost for travel
        travel_sources = ["travel", "phocuswright", "skift", "plugandplay"]
        source_is_travel = any(ts in source_lower for ts in travel_sources)
        if source_is_travel:
            travel_score = max(0.5, travel_score)  # Minimum 0.5 for travel sources
            travel_score = min(1.0, travel_score + 0.2)  # Boost existing score

        # Source-based detection and boost for SaaS
        saas_sources = ["g2crowd", "g2", "capterra", "saas"]
        source_is_saas = any(ss in source_lower for ss in saas_sources)
        if source_is_saas:
            saas_score = max(0.5, saas_score)  # Minimum 0.5 for SaaS sources
            saas_score = min(1.0, saas_score + 0.2)  # Boost existing score

        # Source-based detection and boost for consumer
        consumer_sources = ["consumer", "kickstarter", "indiegogo", "producthunt_consumer"]
        source_is_consumer = any(cs in source_lower for cs in consumer_sources)
        if source_is_consumer:
            consumer_score = max(0.5, consumer_score)  # Minimum 0.5 for consumer sources
            consumer_score = min(1.0, consumer_score + 0.2)  # Boost existing score

        # Collect domain scores
        domain_scores = [
            (Domain.HEALTH, health_score, health_matches),
            (Domain.TRAVEL, travel_score, travel_matches),
            (Domain.SAAS, saas_score, saas_matches),
            (Domain.CONSUMER, consumer_score, consumer_matches),
        ]

        # Sort by score descending
        domain_scores.sort(key=lambda x: x[1], reverse=True)

        # Get primary domain (highest score >= 0.5)
        primary_domain = Domain.UNKNOWN
        primary_confidence = 0.0
        primary_matches: List[str] = []
        secondary_domains: List[Domain] = []

        for domain, score, matches in domain_scores:
            if score >= 0.5:
                if primary_domain == Domain.UNKNOWN:
                    # First domain with score >= 0.5 is primary
                    primary_domain = domain
                    primary_confidence = score
                    primary_matches = matches
                else:
                    # Additional domains with score >= 0.5 are secondary
                    secondary_domains.append(domain)

        return DomainResult(
            primary_domain=primary_domain,
            confidence=primary_confidence,
            secondary_domains=secondary_domains,
            matched_keywords=primary_matches
        )
