# Phase 1: Digital Health Intelligence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the digital health vertical slice as reference implementation for multi-vertical intelligence platform.

**Architecture:** Domain router classifies all signals by vertical, routing to specialized LLM classifiers. Health signals get enriched via SciSpacy entity resolution and clinical data APIs (ClinicalTrials.gov, OpenFDA, PubMed). Hybrid storage uses Signal.metadata for lightweight flags + dedicated enrichment tables.

**Tech Stack:** Python 3.11+, asyncio, aiosqlite, httpx, SciSpacy, spacy-umls, tenacity, pytest-asyncio

**Design Document:** `docs/plans/2026-01-11-multi-vertical-intelligence-design.md`

---

## Task Overview

| Task | Component | Estimated Steps |
|------|-----------|-----------------|
| 1 | Domain Router Infrastructure | 15 steps |
| 2 | Health Keyword Detection | 12 steps |
| 3 | Health LLM Classifier Prompt | 15 steps |
| 4 | Medical Entity Resolver | 18 steps |
| 5 | Health Enrichment Storage Tables | 12 steps |
| 6 | ClinicalTrials.gov Enrichment | 15 steps |
| 7 | OpenFDA Enrichment | 12 steps |
| 8 | PubMed Enrichment | 12 steps |
| 9 | Enrichment Orchestrator | 15 steps |
| 10 | Integration Tests | 10 steps |

---

## Task 1: Domain Router Infrastructure

Create the domain router that classifies signals into verticals (health, travel, saas, consumer).

**Files:**
- Create: `intelligence/__init__.py`
- Create: `intelligence/domain_router.py`
- Create: `tests/intelligence/__init__.py`
- Create: `tests/intelligence/test_domain_router.py`

### Step 1: Create intelligence module directory

```bash
mkdir -p intelligence tests/intelligence
touch intelligence/__init__.py tests/intelligence/__init__.py
```

### Step 2: Write failing test for DomainRouter existence

```python
# tests/intelligence/test_domain_router.py
"""Tests for DomainRouter - routes signals to vertical-specific classifiers."""
import pytest
from intelligence.domain_router import DomainRouter, DomainResult


class TestDomainRouterBasics:
    """Test basic DomainRouter functionality."""

    def test_domain_router_exists(self):
        """DomainRouter class should exist and be instantiable."""
        router = DomainRouter()
        assert router is not None
```

### Step 3: Run test to verify it fails

```bash
pytest tests/intelligence/test_domain_router.py::TestDomainRouterBasics::test_domain_router_exists -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'intelligence.domain_router'"

### Step 4: Write minimal implementation

```python
# intelligence/domain_router.py
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
```

### Step 5: Run test to verify it passes

```bash
pytest tests/intelligence/test_domain_router.py::TestDomainRouterBasics::test_domain_router_exists -v
```

Expected: PASS

### Step 6: Write failing test for detect_domain method

```python
# Add to tests/intelligence/test_domain_router.py
class TestDomainDetection:
    """Test keyword-based domain detection."""

    def test_detect_domain_returns_result(self):
        """detect_domain should return a DomainResult."""
        router = DomainRouter()
        result = router.detect_domain("Some signal content")
        assert isinstance(result, DomainResult)
        assert isinstance(result.primary_domain, Domain)
        assert isinstance(result.confidence, float)
```

### Step 7: Run test to verify it fails

```bash
pytest tests/intelligence/test_domain_router.py::TestDomainDetection::test_detect_domain_returns_result -v
```

Expected: FAIL with "AttributeError: 'DomainRouter' object has no attribute 'detect_domain'"

### Step 8: Implement detect_domain method

```python
# Add to intelligence/domain_router.py DomainRouter class
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
```

### Step 9: Run test to verify it passes

```bash
pytest tests/intelligence/test_domain_router.py::TestDomainDetection::test_detect_domain_returns_result -v
```

Expected: PASS

### Step 10: Write failing test for domain enum values

```python
# Add to tests/intelligence/test_domain_router.py
class TestDomainEnum:
    """Test Domain enum has correct values."""

    def test_domain_has_health(self):
        assert Domain.HEALTH.value == "health"

    def test_domain_has_travel(self):
        assert Domain.TRAVEL.value == "travel"

    def test_domain_has_saas(self):
        assert Domain.SAAS.value == "saas"

    def test_domain_has_consumer(self):
        assert Domain.CONSUMER.value == "consumer"

    def test_domain_has_unknown(self):
        assert Domain.UNKNOWN.value == "unknown"
```

### Step 11: Run tests to verify they pass

```bash
pytest tests/intelligence/test_domain_router.py::TestDomainEnum -v
```

Expected: PASS (already implemented)

### Step 12: Update intelligence/__init__.py exports

```python
# intelligence/__init__.py
"""Intelligence layer for multi-vertical signal classification and enrichment."""
from intelligence.domain_router import Domain, DomainResult, DomainRouter

__all__ = ["Domain", "DomainResult", "DomainRouter"]
```

### Step 13: Run all domain router tests

```bash
pytest tests/intelligence/test_domain_router.py -v
```

Expected: All tests PASS

### Step 14: Commit

```bash
git add intelligence/ tests/intelligence/
git commit -m "feat(intelligence): add DomainRouter infrastructure

- Create intelligence module with domain router
- Add Domain enum (health, travel, saas, consumer, unknown)
- Add DomainResult dataclass for detection results
- Add detect_domain method stub (returns unknown)
- TDD: tests first, implementation follows"
```

### Step 15: Verify commit

```bash
git log -1 --oneline
```

---

## Task 2: Health Keyword Detection

Implement keyword-based detection for health domain signals.

**Files:**
- Modify: `intelligence/domain_router.py`
- Modify: `tests/intelligence/test_domain_router.py`

### Step 1: Write failing test for health keyword detection

```python
# Add to tests/intelligence/test_domain_router.py
class TestHealthKeywordDetection:
    """Test health domain keyword detection."""

    def test_detects_fda_keyword(self):
        """FDA keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("FDA-cleared wearable device")
        assert result.primary_domain == Domain.HEALTH
        assert result.confidence >= 0.7
        assert "fda" in [k.lower() for k in result.matched_keywords]

    def test_detects_clinical_trial_keyword(self):
        """Clinical trial keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("Phase 2 clinical trial results")
        assert result.primary_domain == Domain.HEALTH

    def test_detects_telehealth_keyword(self):
        """Telehealth keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("New telehealth platform launches")
        assert result.primary_domain == Domain.HEALTH

    def test_detects_wearable_keyword(self):
        """Wearable keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("Smart wearable for heart monitoring")
        assert result.primary_domain == Domain.HEALTH
```

### Step 2: Run tests to verify they fail

```bash
pytest tests/intelligence/test_domain_router.py::TestHealthKeywordDetection -v
```

Expected: FAIL (returns UNKNOWN instead of HEALTH)

### Step 3: Add health keywords configuration

```python
# Add to intelligence/domain_router.py after Domain enum

# Keyword patterns for domain detection
# Higher weight = stronger signal for that domain
HEALTH_KEYWORDS = {
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
```

### Step 4: Implement keyword matching logic

```python
# Replace detect_domain method in intelligence/domain_router.py
import re
from typing import Dict, Tuple

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

        # Source-based boost (if from health-specific source)
        if source and "health" in source.lower():
            health_score = min(1.0, health_score + 0.2)

        # Determine primary domain (currently only health implemented)
        if health_score >= 0.6:
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
```

### Step 5: Run tests to verify they pass

```bash
pytest tests/intelligence/test_domain_router.py::TestHealthKeywordDetection -v
```

Expected: PASS

### Step 6: Write failing test for source-based detection

```python
# Add to tests/intelligence/test_domain_router.py
class TestSourceBasedDetection:
    """Test source-based domain detection boost."""

    def test_health_source_boosts_confidence(self):
        """Health source should boost health domain confidence."""
        router = DomainRouter()
        # Generic content without health keywords
        result = router.detect_domain(
            "New product launch announcement",
            source="producthunt_health"
        )
        assert result.confidence >= 0.2  # Source boost applies

    def test_health_source_with_keywords_high_confidence(self):
        """Health source + keywords should have high confidence."""
        router = DomainRouter()
        result = router.detect_domain(
            "FDA-cleared device for monitoring",
            source="producthunt_health"
        )
        assert result.primary_domain == Domain.HEALTH
        assert result.confidence >= 0.9
```

### Step 7: Run tests

```bash
pytest tests/intelligence/test_domain_router.py::TestSourceBasedDetection -v
```

Expected: First test may FAIL (need to adjust logic for source-only detection)

### Step 8: Fix source-only detection

```python
# Update detect_domain in intelligence/domain_router.py
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
```

### Step 9: Run all health detection tests

```bash
pytest tests/intelligence/test_domain_router.py -v -k "Health"
```

Expected: PASS

### Step 10: Write test for non-health content

```python
# Add to tests/intelligence/test_domain_router.py
class TestNonHealthContent:
    """Test that non-health content returns unknown."""

    def test_generic_content_returns_unknown(self):
        """Generic content should return unknown domain."""
        router = DomainRouter()
        result = router.detect_domain("Check out this new software tool")
        assert result.primary_domain == Domain.UNKNOWN

    def test_saas_content_returns_unknown_for_now(self):
        """SaaS content returns unknown until SaaS keywords added."""
        router = DomainRouter()
        result = router.detect_domain("Enterprise B2B SaaS platform")
        assert result.primary_domain == Domain.UNKNOWN
```

### Step 11: Run tests

```bash
pytest tests/intelligence/test_domain_router.py::TestNonHealthContent -v
```

Expected: PASS

### Step 12: Commit

```bash
git add intelligence/domain_router.py tests/intelligence/test_domain_router.py
git commit -m "feat(intelligence): implement health keyword detection

- Add HEALTH_KEYWORDS dictionary with weighted terms
- Implement _match_keywords for pattern matching
- Add source-based detection boost for health sources
- Keywords: FDA, clinical trial, telehealth, wearable, etc.
- TDD: comprehensive tests for health detection"
```

---

## Task 3: Health LLM Classifier Prompt

Create the health-specific LLM classifier with domain expertise prompt.

**Files:**
- Create: `intelligence/health_classifier.py`
- Create: `tests/intelligence/test_health_classifier.py`

### Step 1: Write failing test for HealthClassifier

```python
# tests/intelligence/test_health_classifier.py
"""Tests for health-specific LLM classifier."""
import pytest
from intelligence.health_classifier import (
    HealthClassifier,
    HealthClassifierConfig,
    HealthClassificationResult,
    HealthCategory,
)


class TestHealthClassifierBasics:
    """Test basic HealthClassifier functionality."""

    def test_classifier_exists(self):
        """HealthClassifier should exist and be instantiable."""
        classifier = HealthClassifier()
        assert classifier is not None

    def test_config_has_defaults(self):
        """HealthClassifierConfig should have sensible defaults."""
        config = HealthClassifierConfig()
        assert config.model is not None
        assert config.min_confidence >= 0.0
        assert config.min_confidence <= 1.0
```

### Step 2: Run test to verify it fails

```bash
pytest tests/intelligence/test_health_classifier.py::TestHealthClassifierBasics -v
```

Expected: FAIL with "ModuleNotFoundError"

### Step 3: Write minimal implementation

```python
# intelligence/health_classifier.py
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
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/intelligence/test_health_classifier.py::TestHealthClassifierBasics -v
```

Expected: PASS

### Step 5: Write test for health category enum

```python
# Add to tests/intelligence/test_health_classifier.py
class TestHealthCategories:
    """Test HealthCategory enum values."""

    def test_has_consumer_device(self):
        assert HealthCategory.CONSUMER_DEVICE.value == "consumer_device"

    def test_has_consumer_service(self):
        assert HealthCategory.CONSUMER_SERVICE.value == "consumer_service"

    def test_has_health_it(self):
        assert HealthCategory.HEALTH_IT.value == "health_it"

    def test_has_wellness(self):
        assert HealthCategory.WELLNESS.value == "wellness"

    def test_has_out_of_scope(self):
        assert HealthCategory.OUT_OF_SCOPE.value == "out_of_scope"
```

### Step 6: Run category tests

```bash
pytest tests/intelligence/test_health_classifier.py::TestHealthCategories -v
```

Expected: PASS

### Step 7: Add system prompt constant

```python
# Add to intelligence/health_classifier.py after imports

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
```

### Step 8: Write test for system prompt existence

```python
# Add to tests/intelligence/test_health_classifier.py
from intelligence.health_classifier import HEALTH_CLASSIFIER_SYSTEM_PROMPT


class TestHealthClassifierPrompt:
    """Test health classifier prompt configuration."""

    def test_system_prompt_exists(self):
        """System prompt should be defined."""
        assert HEALTH_CLASSIFIER_SYSTEM_PROMPT is not None
        assert len(HEALTH_CLASSIFIER_SYSTEM_PROMPT) > 100

    def test_system_prompt_mentions_consumer_health(self):
        """System prompt should focus on consumer health."""
        assert "consumer health" in HEALTH_CLASSIFIER_SYSTEM_PROMPT.lower()

    def test_system_prompt_excludes_pharma(self):
        """System prompt should exclude pharmaceutical."""
        assert "pharmaceutical" in HEALTH_CLASSIFIER_SYSTEM_PROMPT.lower()
        assert "out of scope" in HEALTH_CLASSIFIER_SYSTEM_PROMPT.lower()
```

### Step 9: Run prompt tests

```bash
pytest tests/intelligence/test_health_classifier.py::TestHealthClassifierPrompt -v
```

Expected: PASS

### Step 10: Write async classify test (mock LLM)

```python
# Add to tests/intelligence/test_health_classifier.py
import pytest


class TestHealthClassification:
    """Test health signal classification."""

    @pytest.mark.asyncio
    async def test_classify_returns_result(self):
        """classify should return HealthClassificationResult."""
        classifier = HealthClassifier()
        result = await classifier.classify("FDA-cleared wearable for heart monitoring")
        assert isinstance(result, HealthClassificationResult)
        assert isinstance(result.fit_score, float)
        assert isinstance(result.category, HealthCategory)

    @pytest.mark.asyncio
    async def test_classify_with_company_name(self):
        """classify should accept company_name parameter."""
        classifier = HealthClassifier()
        result = await classifier.classify(
            "New health monitoring device",
            company_name="Acme Health"
        )
        assert result is not None
```

### Step 11: Run async tests

```bash
pytest tests/intelligence/test_health_classifier.py::TestHealthClassification -v
```

Expected: PASS (stub returns valid result)

### Step 12: Update intelligence/__init__.py

```python
# intelligence/__init__.py
"""Intelligence layer for multi-vertical signal classification and enrichment."""
from intelligence.domain_router import Domain, DomainResult, DomainRouter
from intelligence.health_classifier import (
    HealthCategory,
    HealthClassificationResult,
    HealthClassifier,
    HealthClassifierConfig,
    HEALTH_CLASSIFIER_SYSTEM_PROMPT,
)

__all__ = [
    "Domain",
    "DomainResult",
    "DomainRouter",
    "HealthCategory",
    "HealthClassificationResult",
    "HealthClassifier",
    "HealthClassifierConfig",
    "HEALTH_CLASSIFIER_SYSTEM_PROMPT",
]
```

### Step 13: Run all classifier tests

```bash
pytest tests/intelligence/test_health_classifier.py -v
```

Expected: All PASS

### Step 14: Commit

```bash
git add intelligence/ tests/intelligence/
git commit -m "feat(intelligence): add HealthClassifier with domain expertise prompt

- Add HealthCategory enum for health sub-verticals
- Add HealthClassificationResult with fit_score and reasoning
- Add HEALTH_CLASSIFIER_SYSTEM_PROMPT with investment thesis focus
- Stub classify method for LLM integration
- Focus: consumer health, exclude pharma/provider-only
- TDD: comprehensive tests for classifier structure"
```

### Step 15: Verify tests still pass

```bash
pytest tests/intelligence/ -v
```

---

## Task 4: Medical Entity Resolver

Create the medical entity resolver using SciSpacy for terminology normalization.

**Files:**
- Create: `intelligence/medical_entity_resolver.py`
- Create: `tests/intelligence/test_medical_entity_resolver.py`
- Modify: `requirements.txt`

### Step 1: Add SciSpacy dependencies to requirements.txt

```bash
echo "scispacy>=0.5.0" >> requirements.txt
echo "spacy>=3.5.0" >> requirements.txt
```

### Step 2: Write failing test for MedicalEntityResolver

```python
# tests/intelligence/test_medical_entity_resolver.py
"""Tests for medical entity resolution using SciSpacy."""
import pytest
from intelligence.medical_entity_resolver import (
    MedicalEntityResolver,
    MedicalEntity,
    ResolvedHealthEntity,
)


class TestMedicalEntityResolverBasics:
    """Test basic MedicalEntityResolver functionality."""

    def test_resolver_exists(self):
        """MedicalEntityResolver should exist."""
        # Note: Won't load model in unit tests
        resolver = MedicalEntityResolver(load_model=False)
        assert resolver is not None

    def test_medical_entity_dataclass(self):
        """MedicalEntity should hold extracted entity info."""
        entity = MedicalEntity(
            text="cardiovascular disease",
            label="DISEASE",
            cui="C0007222",
            confidence=0.95
        )
        assert entity.text == "cardiovascular disease"
        assert entity.cui == "C0007222"
```

### Step 3: Run test to verify it fails

```bash
pytest tests/intelligence/test_medical_entity_resolver.py::TestMedicalEntityResolverBasics -v
```

Expected: FAIL with "ModuleNotFoundError"

### Step 4: Write minimal implementation

```python
# intelligence/medical_entity_resolver.py
"""
MedicalEntityResolver: Extract and normalize medical entities from text.

Uses SciSpacy for medical NER and UMLS for terminology normalization.
Enables linking signals across data sources using standardized medical concepts.

Features:
- Medical entity extraction (diseases, treatments, devices)
- UMLS Concept Unique Identifier (CUI) linking
- Company name normalization with medical context
- Graceful degradation if models unavailable
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MedicalEntity:
    """A medical entity extracted from text."""
    text: str  # Original text span
    label: str  # Entity type (DISEASE, TREATMENT, DEVICE, etc.)
    cui: Optional[str] = None  # UMLS Concept Unique Identifier
    confidence: float = 0.0


@dataclass
class ResolvedHealthEntity:
    """A fully resolved health company entity."""
    entity_id: str
    company_name: str
    normalized_name: str
    medical_concepts: List[str] = field(default_factory=list)  # UMLS CUIs
    medical_entities: List[MedicalEntity] = field(default_factory=list)


class MedicalEntityResolver:
    """Resolves and normalizes medical entities in health signals."""

    def __init__(self, load_model: bool = True, model_name: str = "en_core_sci_sm"):
        """
        Initialize the medical entity resolver.

        Args:
            load_model: Whether to load the SciSpacy model (False for testing)
            model_name: SciSpacy model to use (en_core_sci_sm, en_core_sci_lg)
        """
        self.model_name = model_name
        self.nlp = None

        if load_model:
            self._load_model()

    def _load_model(self):
        """Load the SciSpacy model."""
        try:
            import spacy
            self.nlp = spacy.load(self.model_name)
            logger.info(f"Loaded SciSpacy model: {self.model_name}")
        except OSError:
            logger.warning(
                f"SciSpacy model {self.model_name} not found. "
                "Run: python -m spacy download en_core_sci_sm"
            )
            self.nlp = None

    def extract_entities(self, text: str) -> List[MedicalEntity]:
        """
        Extract medical entities from text.

        Args:
            text: Text to analyze

        Returns:
            List of MedicalEntity objects
        """
        if self.nlp is None:
            return []

        doc = self.nlp(text)
        entities = []

        for ent in doc.ents:
            entities.append(MedicalEntity(
                text=ent.text,
                label=ent.label_,
                confidence=0.8  # Default confidence
            ))

        return entities

    def resolve(
        self,
        content: str,
        company_name: str,
        existing_entity_id: Optional[str] = None
    ) -> ResolvedHealthEntity:
        """
        Resolve a health signal to a normalized entity.

        Args:
            content: Signal text content
            company_name: Company name from signal
            existing_entity_id: Optional existing entity ID to link to

        Returns:
            ResolvedHealthEntity with normalized name and medical concepts
        """
        # Extract medical entities
        medical_entities = self.extract_entities(content)

        # Collect UMLS CUIs
        medical_concepts = [
            e.cui for e in medical_entities
            if e.cui is not None
        ]

        # Generate or use existing entity ID
        entity_id = existing_entity_id or self._generate_entity_id(company_name)

        return ResolvedHealthEntity(
            entity_id=entity_id,
            company_name=company_name,
            normalized_name=self._normalize_company_name(company_name),
            medical_concepts=medical_concepts,
            medical_entities=medical_entities
        )

    def _normalize_company_name(self, name: str) -> str:
        """Normalize company name for matching."""
        # Remove common suffixes
        suffixes = [" Inc", " Inc.", " LLC", " Ltd", " Ltd.", " Corp", " Corp."]
        normalized = name.strip()
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        return normalized.strip().lower()

    def _generate_entity_id(self, company_name: str) -> str:
        """Generate entity ID from company name."""
        import hashlib
        normalized = self._normalize_company_name(company_name)
        return f"health:{hashlib.md5(normalized.encode()).hexdigest()[:12]}"
```

### Step 5: Run tests to verify they pass

```bash
pytest tests/intelligence/test_medical_entity_resolver.py::TestMedicalEntityResolverBasics -v
```

Expected: PASS

### Step 6: Write test for company name normalization

```python
# Add to tests/intelligence/test_medical_entity_resolver.py
class TestCompanyNameNormalization:
    """Test company name normalization."""

    def test_removes_inc_suffix(self):
        resolver = MedicalEntityResolver(load_model=False)
        assert resolver._normalize_company_name("Acme Inc") == "acme"
        assert resolver._normalize_company_name("Acme Inc.") == "acme"

    def test_removes_llc_suffix(self):
        resolver = MedicalEntityResolver(load_model=False)
        assert resolver._normalize_company_name("Acme LLC") == "acme"

    def test_removes_corp_suffix(self):
        resolver = MedicalEntityResolver(load_model=False)
        assert resolver._normalize_company_name("Acme Corp") == "acme"
        assert resolver._normalize_company_name("Acme Corp.") == "acme"

    def test_lowercases_name(self):
        resolver = MedicalEntityResolver(load_model=False)
        assert resolver._normalize_company_name("ACME HEALTH") == "acme health"

    def test_matching_normalized_names(self):
        """Different variants should normalize to same value."""
        resolver = MedicalEntityResolver(load_model=False)
        name1 = resolver._normalize_company_name("Acme Therapeutics Inc")
        name2 = resolver._normalize_company_name("Acme Therapeutics Inc.")
        name3 = resolver._normalize_company_name("acme therapeutics")
        assert name1 == name2 == name3
```

### Step 7: Run normalization tests

```bash
pytest tests/intelligence/test_medical_entity_resolver.py::TestCompanyNameNormalization -v
```

Expected: PASS

### Step 8: Write test for entity ID generation

```python
# Add to tests/intelligence/test_medical_entity_resolver.py
class TestEntityIdGeneration:
    """Test entity ID generation."""

    def test_generates_health_prefixed_id(self):
        resolver = MedicalEntityResolver(load_model=False)
        entity_id = resolver._generate_entity_id("Acme Health")
        assert entity_id.startswith("health:")

    def test_same_company_same_id(self):
        """Same company name should generate same ID."""
        resolver = MedicalEntityResolver(load_model=False)
        id1 = resolver._generate_entity_id("Acme Health Inc")
        id2 = resolver._generate_entity_id("Acme Health Inc.")
        id3 = resolver._generate_entity_id("acme health")
        assert id1 == id2 == id3

    def test_different_companies_different_ids(self):
        """Different companies should have different IDs."""
        resolver = MedicalEntityResolver(load_model=False)
        id1 = resolver._generate_entity_id("Acme Health")
        id2 = resolver._generate_entity_id("Beta Health")
        assert id1 != id2
```

### Step 9: Run entity ID tests

```bash
pytest tests/intelligence/test_medical_entity_resolver.py::TestEntityIdGeneration -v
```

Expected: PASS

### Step 10: Write test for resolve method

```python
# Add to tests/intelligence/test_medical_entity_resolver.py
class TestResolveMethod:
    """Test the resolve method."""

    def test_resolve_returns_entity(self):
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver.resolve(
            content="FDA-cleared cardiac monitor",
            company_name="Acme Health Inc"
        )
        assert isinstance(result, ResolvedHealthEntity)
        assert result.company_name == "Acme Health Inc"
        assert result.normalized_name == "acme health"
        assert result.entity_id.startswith("health:")

    def test_resolve_uses_existing_entity_id(self):
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver.resolve(
            content="New product launch",
            company_name="Acme Health",
            existing_entity_id="health:existing123"
        )
        assert result.entity_id == "health:existing123"
```

### Step 11: Run resolve tests

```bash
pytest tests/intelligence/test_medical_entity_resolver.py::TestResolveMethod -v
```

Expected: PASS

### Step 12: Update intelligence/__init__.py

```python
# Add to intelligence/__init__.py
from intelligence.medical_entity_resolver import (
    MedicalEntity,
    MedicalEntityResolver,
    ResolvedHealthEntity,
)

# Update __all__
__all__ = [
    # ... existing exports ...
    "MedicalEntity",
    "MedicalEntityResolver",
    "ResolvedHealthEntity",
]
```

### Step 13: Run all resolver tests

```bash
pytest tests/intelligence/test_medical_entity_resolver.py -v
```

Expected: All PASS

### Step 14: Commit

```bash
git add intelligence/ tests/intelligence/ requirements.txt
git commit -m "feat(intelligence): add MedicalEntityResolver with SciSpacy

- Add MedicalEntity dataclass for extracted entities
- Add ResolvedHealthEntity for fully resolved entities
- Implement company name normalization (remove Inc, LLC, etc.)
- Implement entity ID generation with health: prefix
- Add SciSpacy integration (graceful fallback if model unavailable)
- Add scispacy, spacy to requirements.txt
- TDD: comprehensive tests without loading actual model"
```

---

## Task 5: Health Enrichment Storage Tables

Create database tables for storing health enrichment data.

**Files:**
- Create: `storage/health_enrichment.py`
- Create: `tests/storage/test_health_enrichment.py`
- Modify: `storage/migrations.py`

### Step 1: Write failing test for HealthEnrichmentStore

```python
# tests/storage/test_health_enrichment.py
"""Tests for health enrichment storage."""
import pytest
from storage.health_enrichment import (
    HealthEnrichmentStore,
    ClinicalTrial,
    FDAClearance,
    Publication,
)


class TestHealthEnrichmentStoreBasics:
    """Test basic HealthEnrichmentStore functionality."""

    def test_store_exists(self):
        """HealthEnrichmentStore should exist."""
        store = HealthEnrichmentStore(":memory:")
        assert store is not None

    def test_clinical_trial_dataclass(self):
        """ClinicalTrial should hold trial data."""
        trial = ClinicalTrial(
            entity_id="health:abc123",
            nct_id="NCT12345678",
            title="Test Trial",
            phase="Phase 2",
            status="Recruiting"
        )
        assert trial.nct_id == "NCT12345678"
        assert trial.phase == "Phase 2"
```

### Step 2: Run test to verify it fails

```bash
pytest tests/storage/test_health_enrichment.py::TestHealthEnrichmentStoreBasics -v
```

Expected: FAIL with "ModuleNotFoundError"

### Step 3: Write minimal implementation

```python
# storage/health_enrichment.py
"""
Health Enrichment Storage Layer

Stores health-specific enrichment data:
- Clinical trials from ClinicalTrials.gov
- FDA clearances from OpenFDA
- Research publications from PubMed

All linked to entities via entity_id for cross-referencing with signals.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional
import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class ClinicalTrial:
    """Clinical trial data from ClinicalTrials.gov."""
    entity_id: str
    nct_id: str
    title: str
    phase: Optional[str] = None
    status: Optional[str] = None
    enrollment: Optional[int] = None
    conditions: List[str] = field(default_factory=list)
    start_date: Optional[date] = None
    completion_date: Optional[date] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class FDAClearance:
    """FDA clearance data from OpenFDA."""
    entity_id: str
    application_number: str
    device_name: str
    device_class: Optional[str] = None  # I, II, III
    clearance_type: Optional[str] = None  # 510k, PMA, de_novo
    decision: Optional[str] = None
    decision_date: Optional[date] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class Publication:
    """Research publication data from PubMed."""
    entity_id: str
    pmid: str
    title: str
    authors: Optional[str] = None
    journal: Optional[str] = None
    pub_date: Optional[date] = None
    citation_count: Optional[int] = None
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


class HealthEnrichmentStore:
    """Storage for health enrichment data."""

    def __init__(self, db_path: str = "signals.db"):
        """Initialize the health enrichment store."""
        self.db_path = db_path
        self._initialized = False

    async def initialize(self):
        """Initialize database tables."""
        async with aiosqlite.connect(self.db_path) as db:
            await self._create_tables(db)
            await db.commit()
        self._initialized = True

    async def _create_tables(self, db: aiosqlite.Connection):
        """Create health enrichment tables."""
        # Clinical trials table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS health_clinical_trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                nct_id TEXT UNIQUE,
                title TEXT,
                phase TEXT,
                status TEXT,
                enrollment INTEGER,
                conditions TEXT,
                start_date DATE,
                completion_date DATE,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_trials_entity ON health_clinical_trials(entity_id)"
        )

        # FDA clearances table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS health_fda_clearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                application_number TEXT,
                device_name TEXT,
                device_class TEXT,
                clearance_type TEXT,
                decision TEXT,
                decision_date DATE,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fda_entity ON health_fda_clearances(entity_id)"
        )

        # Publications table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS health_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                pmid TEXT,
                title TEXT,
                authors TEXT,
                journal TEXT,
                pub_date DATE,
                citation_count INTEGER,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pubs_entity ON health_publications(entity_id)"
        )

    async def save_clinical_trial(self, trial: ClinicalTrial) -> int:
        """Save a clinical trial record."""
        async with aiosqlite.connect(self.db_path) as db:
            import json
            conditions_json = json.dumps(trial.conditions) if trial.conditions else "[]"

            cursor = await db.execute("""
                INSERT OR REPLACE INTO health_clinical_trials
                (entity_id, nct_id, title, phase, status, enrollment, conditions, start_date, completion_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trial.entity_id,
                trial.nct_id,
                trial.title,
                trial.phase,
                trial.status,
                trial.enrollment,
                conditions_json,
                trial.start_date.isoformat() if trial.start_date else None,
                trial.completion_date.isoformat() if trial.completion_date else None,
            ))
            await db.commit()
            return cursor.lastrowid

    async def get_trials_for_entity(self, entity_id: str) -> List[ClinicalTrial]:
        """Get all clinical trials for an entity."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM health_clinical_trials WHERE entity_id = ?",
                (entity_id,)
            )
            rows = await cursor.fetchall()
            import json
            return [
                ClinicalTrial(
                    id=row["id"],
                    entity_id=row["entity_id"],
                    nct_id=row["nct_id"],
                    title=row["title"],
                    phase=row["phase"],
                    status=row["status"],
                    enrollment=row["enrollment"],
                    conditions=json.loads(row["conditions"]) if row["conditions"] else [],
                )
                for row in rows
            ]
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/storage/test_health_enrichment.py::TestHealthEnrichmentStoreBasics -v
```

Expected: PASS

### Step 5: Write async initialization test

```python
# Add to tests/storage/test_health_enrichment.py
class TestHealthEnrichmentStoreInit:
    """Test store initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self):
        """Initialize should create all health tables."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()
        assert store._initialized is True

    @pytest.mark.asyncio
    async def test_save_and_get_trial(self):
        """Should save and retrieve clinical trial."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        trial = ClinicalTrial(
            entity_id="health:test123",
            nct_id="NCT12345678",
            title="Test Clinical Trial",
            phase="Phase 2",
            status="Recruiting",
            conditions=["Cardiovascular Disease"]
        )

        trial_id = await store.save_clinical_trial(trial)
        assert trial_id is not None

        trials = await store.get_trials_for_entity("health:test123")
        assert len(trials) == 1
        assert trials[0].nct_id == "NCT12345678"
        assert trials[0].phase == "Phase 2"
```

### Step 6: Run async tests

```bash
pytest tests/storage/test_health_enrichment.py::TestHealthEnrichmentStoreInit -v
```

Expected: PASS

### Step 7: Add FDA clearance save/get methods

```python
# Add to storage/health_enrichment.py HealthEnrichmentStore class

    async def save_fda_clearance(self, clearance: FDAClearance) -> int:
        """Save an FDA clearance record."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO health_fda_clearances
                (entity_id, application_number, device_name, device_class, clearance_type, decision, decision_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                clearance.entity_id,
                clearance.application_number,
                clearance.device_name,
                clearance.device_class,
                clearance.clearance_type,
                clearance.decision,
                clearance.decision_date.isoformat() if clearance.decision_date else None,
            ))
            await db.commit()
            return cursor.lastrowid

    async def get_fda_clearances_for_entity(self, entity_id: str) -> List[FDAClearance]:
        """Get all FDA clearances for an entity."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM health_fda_clearances WHERE entity_id = ?",
                (entity_id,)
            )
            rows = await cursor.fetchall()
            return [
                FDAClearance(
                    id=row["id"],
                    entity_id=row["entity_id"],
                    application_number=row["application_number"],
                    device_name=row["device_name"],
                    device_class=row["device_class"],
                    clearance_type=row["clearance_type"],
                    decision=row["decision"],
                )
                for row in rows
            ]
```

### Step 8: Write FDA clearance tests

```python
# Add to tests/storage/test_health_enrichment.py
class TestFDAClearanceStorage:
    """Test FDA clearance storage."""

    @pytest.mark.asyncio
    async def test_save_and_get_fda_clearance(self):
        """Should save and retrieve FDA clearance."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        clearance = FDAClearance(
            entity_id="health:test123",
            application_number="K123456",
            device_name="Smart Heart Monitor",
            device_class="II",
            clearance_type="510k",
            decision="Cleared"
        )

        clearance_id = await store.save_fda_clearance(clearance)
        assert clearance_id is not None

        clearances = await store.get_fda_clearances_for_entity("health:test123")
        assert len(clearances) == 1
        assert clearances[0].application_number == "K123456"
        assert clearances[0].clearance_type == "510k"
```

### Step 9: Run FDA tests

```bash
pytest tests/storage/test_health_enrichment.py::TestFDAClearanceStorage -v
```

Expected: PASS

### Step 10: Update storage/__init__.py

```python
# Add to storage/__init__.py
from storage.health_enrichment import (
    ClinicalTrial,
    FDAClearance,
    HealthEnrichmentStore,
    Publication,
)
```

### Step 11: Run all storage tests

```bash
pytest tests/storage/test_health_enrichment.py -v
```

Expected: All PASS

### Step 12: Commit

```bash
git add storage/ tests/storage/
git commit -m "feat(storage): add health enrichment tables

- Add ClinicalTrial, FDAClearance, Publication dataclasses
- Add HealthEnrichmentStore with async SQLite operations
- Create indexed tables for entity_id lookups
- Implement save/get methods for trials and clearances
- TDD: comprehensive async tests with in-memory DB"
```

---

## Task 6-9: Enrichment APIs and Orchestrator

[Tasks 6-9 follow the same TDD pattern for ClinicalTrials.gov, OpenFDA, PubMed clients and orchestrator. Each task includes:]

- Write failing tests
- Implement minimal code
- Run tests to verify
- Commit frequently

See design document for API specifications and schemas.

---

## Task 10: Integration Tests

Create end-to-end integration tests for the health intelligence pipeline.

**Files:**
- Create: `tests/integration/test_health_intelligence.py`

### Step 1: Write integration test for full pipeline

```python
# tests/integration/test_health_intelligence.py
"""Integration tests for health intelligence pipeline."""
import pytest
from intelligence import DomainRouter, Domain, HealthClassifier
from intelligence.medical_entity_resolver import MedicalEntityResolver


class TestHealthIntelligencePipeline:
    """Test the complete health intelligence flow."""

    @pytest.mark.asyncio
    async def test_health_signal_full_flow(self):
        """Test signal flows through domain detection → classification → resolution."""
        # 1. Domain detection
        router = DomainRouter()
        domain_result = router.detect_domain(
            "FDA-cleared wearable for cardiac monitoring",
            source="producthunt_health"
        )
        assert domain_result.primary_domain == Domain.HEALTH
        assert domain_result.confidence >= 0.8

        # 2. Health classification
        classifier = HealthClassifier()
        classification = await classifier.classify(
            "FDA-cleared wearable for cardiac monitoring",
            company_name="CardioTech Inc"
        )
        assert classification is not None

        # 3. Entity resolution
        resolver = MedicalEntityResolver(load_model=False)
        entity = resolver.resolve(
            content="FDA-cleared wearable for cardiac monitoring",
            company_name="CardioTech Inc"
        )
        assert entity.entity_id.startswith("health:")
        assert entity.normalized_name == "cardiotech"

    def test_non_health_signal_filtered(self):
        """Non-health signals should not be classified as health."""
        router = DomainRouter()
        result = router.detect_domain("New B2B SaaS platform for enterprises")
        assert result.primary_domain != Domain.HEALTH
```

### Step 2: Run integration tests

```bash
pytest tests/integration/test_health_intelligence.py -v
```

Expected: PASS

### Step 3: Final commit

```bash
git add tests/integration/
git commit -m "test(integration): add health intelligence pipeline tests

- Test full flow: domain detection → classification → resolution
- Verify health signals correctly identified and processed
- Verify non-health signals filtered appropriately"
```

---

## Verification Checklist

After completing all tasks, verify:

```bash
# Run all tests
pytest tests/intelligence/ tests/storage/test_health_enrichment.py tests/integration/test_health_intelligence.py -v

# Check test coverage
pytest --cov=intelligence --cov=storage/health_enrichment -v

# Verify no regressions
pytest -x -q
```

---

## Next Steps

After Phase 1 completion:
1. Deploy to staging environment
2. Run with live data sources
3. Measure signal quality metrics
4. Begin Phase 2: Travel & B2B SaaS verticals
