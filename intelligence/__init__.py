"""Intelligence layer for multi-vertical signal classification and enrichment."""
from intelligence.domain_router import Domain, DomainResult, DomainRouter
from intelligence.health_classifier import (
    HealthCategory,
    HealthClassificationResult,
    HealthClassifier,
    HealthClassifierConfig,
    HEALTH_CLASSIFIER_SYSTEM_PROMPT,
)
from intelligence.medical_entity_resolver import (
    MedicalEntity,
    MedicalEntityResolver,
    ResolvedHealthEntity,
)
from intelligence.thesis_config import ThesisConfig, load_thesis_config

__all__ = [
    "Domain",
    "DomainResult",
    "DomainRouter",
    "HealthCategory",
    "HealthClassificationResult",
    "HealthClassifier",
    "HealthClassifierConfig",
    "HEALTH_CLASSIFIER_SYSTEM_PROMPT",
    "MedicalEntity",
    "MedicalEntityResolver",
    "ResolvedHealthEntity",
    "ThesisConfig",
    "load_thesis_config",
]
