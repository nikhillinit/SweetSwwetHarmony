"""
HealthClassifier: Domain-specific LLM classifier for health signals.

Classifies health signals with expertise in:
- Consumer health products (devices, wearables, beauty tech)
- Consumer health services (telehealth, fertility, virtual care)
- Health IT (EHR, clinical workflow, health data platforms)

Excludes:
- Pure pharmaceutical/biotech (drug development)
- Provider-only medical devices (surgical equipment)

Uses structured output with fit scoring and investment stage matching.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List


HEALTH_CLASSIFIER_SYSTEM_PROMPT = """You are an expert investment analyst specializing in consumer health technology.

Your task is to evaluate signals about companies and determine their fit for a consumer health investment thesis.

## Investment Thesis Focus

IN SCOPE (evaluate positively):
- Consumer health products: wearables, beauty tech devices, home health monitors, oral care
- Consumer health services: telehealth, fertility services, virtual care, mental health platforms
- Health IT with consumer touchpoint: patient portals, health data platforms, care coordination
- Wellness platforms: fitness tech, nutrition apps, sleep tracking, stress management

OUT OF SCOPE (mark as out_of_scope):
- Pure pharmaceutical/biotech: drug development, clinical trials for therapeutics
- Provider-only medical devices: surgical equipment, hospital diagnostic tools
- Enterprise health IT without consumer interface: backend EHR systems, billing software

## Classification Output

For each signal, provide:
1. fit_score (0-1): How well does this match our consumer health thesis? (0.0 = no fit, 1.0 = perfect fit)
2. category: consumer_device, consumer_service, health_it, wellness, or out_of_scope
3. sub_category: More specific categorization (e.g., "wearables", "telehealth", "mental_health")
4. thesis_alignment: 2-3 sentences explaining your assessment
5. signals: List of keywords/signals detected in the content
6. confidence (0-1): How confident are you in this classification? (0.0 = uncertain, 1.0 = certain)
7. is_in_scope: true if consumer health, false if pharma/provider-only
8. investment_stage_fit: seed, series_a, later, or not_fit
9. regulatory_stage: pre_regulatory, fda_pending, fda_cleared, fda_approved, or null

Be skeptical of overly broad health claims. Focus on actual consumer health technology companies."""


class HealthCategory(Enum):
    """Categories within the health vertical."""
    CONSUMER_DEVICE = "consumer_device"  # Wearables, beauty tech, home health
    CONSUMER_SERVICE = "consumer_service"  # Telehealth, fertility, virtual care
    HEALTH_IT = "health_it"  # EHR, clinical workflow, data platforms
    WELLNESS = "wellness"  # Fitness, mental health, nutrition
    OUT_OF_SCOPE = "out_of_scope"  # Pharma, provider-only devices


@dataclass
class HealthClassifierConfig:
    """Configuration for HealthClassifier."""
    model: str = "claude-3-haiku-20240307"
    min_confidence: float = 0.7
    temperature: float = 0.2
    max_tokens: int = 500
    api_key: Optional[str] = None


@dataclass
class HealthClassificationResult:
    """Result of health signal classification."""
    # Required fields per spec
    fit_score: float  # 0-1 scale (NOT 0-10)
    category: HealthCategory
    sub_category: Optional[str]  # More specific categorization within category
    thesis_alignment: str  # 2-3 sentences explaining assessment
    signals: List[str]  # Keywords/signals detected in the content
    confidence: float  # 0-1 scale
    # Optional extra fields
    is_in_scope: bool = True  # True if consumer health, False if pharma/provider-only
    investment_stage_fit: str = "not_fit"  # "seed", "series_a", "later", "not_fit"
    regulatory_stage: Optional[str] = None  # "pre_regulatory", "fda_cleared", etc.


class HealthClassifier:
    """Domain-specific classifier for health vertical signals."""

    def __init__(self, config: Optional[HealthClassifierConfig] = None):
        """Initialize the health classifier."""
        self.config = config or HealthClassifierConfig()

    async def classify(
        self,
        content: str,
        company_name: Optional[str] = None,
        source: Optional[str] = None,
    ) -> HealthClassificationResult:
        """
        Classify a health signal using LLM with domain expertise.

        Args:
            content: Signal text to classify
            company_name: Optional company name for context
            source: Optional source identifier

        Returns:
            HealthClassificationResult with fit score and category
        """
        # Stub implementation - returns default result
        return HealthClassificationResult(
            fit_score=0.0,
            category=HealthCategory.OUT_OF_SCOPE,
            sub_category=None,
            thesis_alignment="Not yet classified",
            signals=[],
            confidence=0.0,
            is_in_scope=False,
            investment_stage_fit="not_fit",
        )
