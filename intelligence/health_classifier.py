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
1. fit_score (0-10): How well does this match our consumer health thesis?
2. category: consumer_device, consumer_service, health_it, wellness, or out_of_scope
3. reasoning: 2-3 sentences explaining your assessment
4. investment_stage_fit: seed, series_a, later, or not_fit
5. is_in_scope: true if consumer health, false if pharma/provider-only
6. regulatory_stage: pre_regulatory, fda_pending, fda_cleared, fda_approved, or null

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
    fit_score: float  # 0-10 scale
    category: HealthCategory
    reasoning: str
    investment_stage_fit: str  # "seed", "series_a", "later", "not_fit"
    confidence: float
    is_in_scope: bool  # True if consumer health, False if pharma/provider-only
    regulatory_stage: Optional[str] = None  # "pre_regulatory", "fda_cleared", etc.
    keywords_detected: List[str] = field(default_factory=list)


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
            reasoning="Not yet classified",
            investment_stage_fit="not_fit",
            confidence=0.0,
            is_in_scope=False,
        )
