"""
DomainRouter: Routes signals to vertical-specific classifiers.

Two-stage classification:
1. Fast keyword-based domain detection (free, synchronous)
2. Vertical-specific LLM classification (cost per call, async)

Supported domains: health, travel, saas, consumer
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Domain(Enum):
    """Investment verticals supported by the platform."""
    HEALTH = "health"
    TRAVEL = "travel"
    SAAS = "saas"
    CONSUMER = "consumer"
    UNKNOWN = "unknown"


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
        """Initialize the domain router."""
        pass

    def detect_domain(self, content: str, source: Optional[str] = None) -> DomainResult:
        """
        Detect the domain of a signal using keyword matching.

        Args:
            content: Signal text content to analyze
            source: Optional source identifier (e.g., "producthunt_health")

        Returns:
            DomainResult with primary domain and confidence
        """
        # Default to unknown with low confidence
        return DomainResult(
            primary_domain=Domain.UNKNOWN,
            confidence=0.0,
            secondary_domains=[],
            matched_keywords=[]
        )
