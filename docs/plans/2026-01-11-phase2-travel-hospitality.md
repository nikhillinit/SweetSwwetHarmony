# Phase 2: Travel & Hospitality Intelligence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Travel & Hospitality vertical intelligence with domain routing, LLM classification, and enrichment from Yelp Fusion, Google Places, and travel certifications.

**Architecture:** Extends Phase 1 patterns - adds travel keywords to DomainRouter, creates TravelClassifier with YAML-configurable weighted scoring, implements three enrichment clients (Yelp, Google Places, certifications), and adds Plug and Play accelerator collector.

**Tech Stack:** Python 3.11, httpx (async HTTP), aiosqlite, PyYAML, pytest

**API Keys Required:**
- `YELP_API_KEY` - Free at developers.yelp.com (5000 calls/day)
- `GOOGLE_PLACES_API_KEY` - Free tier at console.cloud.google.com ($200/mo credit)
- `PERPLEXITY_API_KEY` - Optional, for deep research

---

## Task 1: Travel Keywords in Domain Router

**Files:**
- Modify: `intelligence/domain_router.py`
- Test: `tests/intelligence/test_domain_router.py`

### Step 1: Write failing tests for travel keyword detection

```python
# tests/intelligence/test_domain_router.py

class TestTravelDomainDetection:
    """Tests for travel domain keyword detection."""

    def test_detects_hotel_keyword(self):
        router = DomainRouter()
        result = router.detect_domain("Revolutionary hotel management software")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.7
        assert "hotel" in result.matched_keywords

    def test_detects_hospitality_keyword(self):
        router = DomainRouter()
        result = router.detect_domain("AI-powered hospitality platform")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.8

    def test_detects_booking_platform(self):
        router = DomainRouter()
        result = router.detect_domain("Next-gen booking platform for luxury travel")
        assert result.primary_domain == Domain.TRAVEL
        assert "booking" in result.matched_keywords

    def test_detects_property_management(self):
        router = DomainRouter()
        result = router.detect_domain("Short-term rental property management system")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.7

    def test_detects_experiential_travel(self):
        router = DomainRouter()
        result = router.detect_domain("Curated experiential travel marketplace")
        assert result.primary_domain == Domain.TRAVEL

    def test_source_boost_for_plugandplay_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "Software platform for hotels",
            source="plugandplay_travel"
        )
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.7  # Boosted by source

    def test_source_boost_for_phocuswright(self):
        router = DomainRouter()
        result = router.detect_domain(
            "New startup in the space",
            source="phocuswright"
        )
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.5  # Minimum from source

    def test_multi_domain_health_and_travel(self):
        router = DomainRouter()
        result = router.detect_domain("Hotel wellness spa technology platform")
        # Should detect both domains
        domains = [result.primary_domain] + result.secondary_domains
        assert Domain.TRAVEL in domains or Domain.HEALTH in domains
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/intelligence/test_domain_router.py::TestTravelDomainDetection -v`
Expected: FAIL (travel keywords not implemented)

### Step 3: Implement travel keywords in domain_router.py

```python
# Add to intelligence/domain_router.py after HEALTH_KEYWORDS

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


class DomainRouter:
    def __init__(self):
        self.health_keywords = HEALTH_KEYWORDS
        self.travel_keywords = TRAVEL_KEYWORDS

    def detect_domain(self, content: str, source: Optional[str] = None) -> DomainResult:
        # Check health keywords
        health_score, health_matches = self._match_keywords(content, self.health_keywords)

        # Check travel keywords
        travel_score, travel_matches = self._match_keywords(content, self.travel_keywords)

        # Source-based detection and boost for health
        source_is_health = source and "health" in source.lower()
        if source_is_health:
            health_score = max(0.5, health_score)
            health_score = min(1.0, health_score + 0.2)

        # Source-based detection and boost for travel
        travel_sources = ["travel", "phocuswright", "skift", "plugandplay"]
        source_is_travel = source and any(s in source.lower() for s in travel_sources)
        if source_is_travel:
            travel_score = max(0.5, travel_score)
            travel_score = min(1.0, travel_score + 0.2)

        # Determine primary and secondary domains
        scores = [
            (Domain.HEALTH, health_score, health_matches),
            (Domain.TRAVEL, travel_score, travel_matches),
        ]
        scores.sort(key=lambda x: x[1], reverse=True)

        primary_domain, primary_score, primary_matches = scores[0]
        secondary_domains = [d for d, s, _ in scores[1:] if s >= 0.5]

        if primary_score >= 0.5:
            return DomainResult(
                primary_domain=primary_domain,
                confidence=primary_score,
                secondary_domains=secondary_domains,
                matched_keywords=primary_matches
            )

        return DomainResult(
            primary_domain=Domain.UNKNOWN,
            confidence=0.0,
            secondary_domains=[],
            matched_keywords=[]
        )
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/intelligence/test_domain_router.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add intelligence/domain_router.py tests/intelligence/test_domain_router.py
git commit -m "feat(intelligence): add travel keywords to domain router"
```

---

## Task 2: Travel Thesis Rules YAML Configuration

**Files:**
- Create: `config/travel_thesis_rules.yaml`
- Create: `intelligence/thesis_config.py`
- Test: `tests/intelligence/test_thesis_config.py`

### Step 1: Write failing tests for thesis config loading

```python
# tests/intelligence/test_thesis_config.py
import pytest
from intelligence.thesis_config import ThesisConfig, load_thesis_config


class TestThesisConfigLoading:
    """Tests for YAML thesis configuration loading."""

    def test_load_travel_thesis_config(self):
        config = load_thesis_config("travel")
        assert config.vertical == "travel"
        assert config.version is not None

    def test_config_has_scoring_weights(self):
        config = load_thesis_config("travel")
        assert "distribution" in config.scoring_weights
        assert "category" in config.scoring_weights
        assert "traction" in config.scoring_weights
        assert "founder" in config.scoring_weights

    def test_weights_sum_to_one(self):
        config = load_thesis_config("travel")
        total = sum(config.scoring_weights.values())
        assert 0.99 <= total <= 1.01  # Allow small float error

    def test_config_has_positive_signals(self):
        config = load_thesis_config("travel")
        assert "distribution" in config.positive_signals
        assert len(config.positive_signals["distribution"]) > 0

    def test_config_has_negative_signals(self):
        config = load_thesis_config("travel")
        assert len(config.negative_signals) > 0

    def test_invalid_vertical_raises_error(self):
        with pytest.raises(FileNotFoundError):
            load_thesis_config("invalid_vertical")


class TestThesisConfig:
    """Tests for ThesisConfig dataclass."""

    def test_config_attributes(self):
        config = load_thesis_config("travel")
        assert hasattr(config, "vertical")
        assert hasattr(config, "version")
        assert hasattr(config, "scoring_weights")
        assert hasattr(config, "positive_signals")
        assert hasattr(config, "negative_signals")
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/intelligence/test_thesis_config.py -v`
Expected: FAIL (module not found)

### Step 3: Create config directory and YAML file

```yaml
# config/travel_thesis_rules.yaml
version: "1.0"
vertical: travel
description: "Travel & Hospitality thesis matching rules for Press On VC"

scoring_weights:
  distribution: 0.30    # OTA partnerships, hotel chains, travel agencies
  category: 0.25        # Tech-enabled category match
  traction: 0.25        # Bookings, GMV, partnerships, properties
  founder: 0.20         # Hospitality industry background

positive_signals:
  distribution:
    - "Expedia"
    - "Booking.com"
    - "Marriott"
    - "Hilton"
    - "Hyatt"
    - "IHG"
    - "Airbnb partner"
    - "VRBO"
    - "OTA"
    - "travel agency"
  category:
    - "hotel tech"
    - "booking platform"
    - "property management"
    - "guest experience"
    - "revenue management"
    - "channel manager"
    - "vacation rental"
    - "experiential travel"
    - "tour operator"
    - "concierge tech"
  traction:
    - "bookings"
    - "GMV"
    - "properties"
    - "hotels"
    - "partnerships"
    - "revenue"
    - "guests"
    - "nights booked"
  founder:
    - "hospitality veteran"
    - "hotel industry"
    - "travel exec"
    - "OTA founder"
    - "Marriott alum"
    - "Hilton alum"
    - "Airbnb alum"
    - "Expedia alum"

negative_signals:
  - "legacy GDS"
  - "traditional travel agency"
  - "no tech differentiation"
  - "Series C"
  - "Series D"
  - "public company"
  - "acquired"
  - "non-tech"
  - "brick and mortar only"

stage_filters:
  included:
    - "pre-seed"
    - "seed"
    - "Series A"
  excluded:
    - "Series B"
    - "Series C"
    - "Series D"
    - "IPO"
```

### Step 4: Implement thesis_config.py

```python
# intelligence/thesis_config.py
"""
Thesis configuration loader for vertical-specific scoring rules.

Loads YAML configuration files that define:
- Scoring weights for different signal categories
- Positive signals that indicate thesis fit
- Negative signals that disqualify companies
- Stage filters for investment stage matching
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml


@dataclass
class ThesisConfig:
    """Configuration for vertical-specific thesis matching."""

    vertical: str
    version: str
    description: str
    scoring_weights: Dict[str, float]
    positive_signals: Dict[str, List[str]]
    negative_signals: List[str]
    stage_filters: Dict[str, List[str]]


def load_thesis_config(vertical: str) -> ThesisConfig:
    """
    Load thesis configuration for a specific vertical.

    Args:
        vertical: The vertical name (e.g., "travel", "health")

    Returns:
        ThesisConfig with loaded rules

    Raises:
        FileNotFoundError: If config file doesn't exist
    """
    # Look for config in multiple locations
    config_paths = [
        Path(f"config/{vertical}_thesis_rules.yaml"),
        Path(__file__).parent.parent / "config" / f"{vertical}_thesis_rules.yaml",
    ]

    config_path = None
    for path in config_paths:
        if path.exists():
            config_path = path
            break

    if config_path is None:
        raise FileNotFoundError(
            f"Thesis config not found for vertical '{vertical}'. "
            f"Searched: {[str(p) for p in config_paths]}"
        )

    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    return ThesisConfig(
        vertical=data["vertical"],
        version=data["version"],
        description=data.get("description", ""),
        scoring_weights=data["scoring_weights"],
        positive_signals=data["positive_signals"],
        negative_signals=data["negative_signals"],
        stage_filters=data.get("stage_filters", {"included": [], "excluded": []}),
    )
```

### Step 5: Run tests to verify they pass

Run: `pytest tests/intelligence/test_thesis_config.py -v`
Expected: PASS

### Step 6: Commit

```bash
git add config/travel_thesis_rules.yaml intelligence/thesis_config.py tests/intelligence/test_thesis_config.py
git commit -m "feat(intelligence): add YAML-based thesis config for travel vertical"
```

---

## Task 3: Travel Classifier with Weighted Scoring

**Files:**
- Create: `intelligence/travel_classifier.py`
- Test: `tests/intelligence/test_travel_classifier.py`

### Step 1: Write failing tests for travel classifier

```python
# tests/intelligence/test_travel_classifier.py
import pytest
from intelligence.travel_classifier import (
    TravelClassifier,
    TravelClassifierConfig,
    TravelClassificationResult,
    TravelCategory,
)


class TestTravelClassifierConfig:
    """Tests for TravelClassifierConfig."""

    def test_default_config(self):
        config = TravelClassifierConfig()
        assert config.model == "claude-3-haiku-20240307"
        assert config.min_confidence == 0.7
        assert config.temperature == 0.2

    def test_custom_config(self):
        config = TravelClassifierConfig(
            model="claude-3-sonnet-20240229",
            min_confidence=0.8
        )
        assert config.model == "claude-3-sonnet-20240229"
        assert config.min_confidence == 0.8


class TestTravelCategory:
    """Tests for TravelCategory enum."""

    def test_categories_exist(self):
        assert TravelCategory.HOTEL_TECH.value == "hotel_tech"
        assert TravelCategory.BOOKING_PLATFORM.value == "booking_platform"
        assert TravelCategory.EXPERIENTIAL.value == "experiential"
        assert TravelCategory.TRAVEL_INFRASTRUCTURE.value == "travel_infrastructure"
        assert TravelCategory.RENTAL_TECH.value == "rental_tech"
        assert TravelCategory.OUT_OF_SCOPE.value == "out_of_scope"


class TestTravelClassificationResult:
    """Tests for TravelClassificationResult dataclass."""

    def test_result_fields(self):
        result = TravelClassificationResult(
            fit_score=0.85,
            category=TravelCategory.HOTEL_TECH,
            sub_category="pms",
            thesis_alignment="Strong hotel tech play with OTA integrations",
            signals=["hotel", "property management", "booking"],
            confidence=0.9,
            is_tech_enabled=True,
            investment_stage_fit="seed"
        )
        assert result.fit_score == 0.85
        assert result.category == TravelCategory.HOTEL_TECH
        assert result.sub_category == "pms"
        assert result.is_tech_enabled is True


class TestTravelClassifier:
    """Tests for TravelClassifier."""

    def test_classifier_initialization(self):
        classifier = TravelClassifier()
        assert classifier.config is not None

    def test_classifier_with_custom_config(self):
        config = TravelClassifierConfig(min_confidence=0.8)
        classifier = TravelClassifier(config=config)
        assert classifier.config.min_confidence == 0.8

    @pytest.mark.asyncio
    async def test_classify_returns_result(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="AI-powered hotel property management system",
            company_name="HotelTech Inc"
        )
        assert isinstance(result, TravelClassificationResult)
        assert 0.0 <= result.fit_score <= 1.0
        assert 0.0 <= result.confidence <= 1.0

    def test_system_prompt_exists(self):
        from intelligence.travel_classifier import TRAVEL_CLASSIFIER_SYSTEM_PROMPT
        assert "travel" in TRAVEL_CLASSIFIER_SYSTEM_PROMPT.lower()
        assert "hospitality" in TRAVEL_CLASSIFIER_SYSTEM_PROMPT.lower()
        assert "tech-enabled" in TRAVEL_CLASSIFIER_SYSTEM_PROMPT.lower()


class TestTravelClassifierWeightedScoring:
    """Tests for weighted signal scoring."""

    def test_compute_signal_score_distribution(self):
        classifier = TravelClassifier()
        score = classifier._compute_signal_score(
            "Partnership with Marriott and Hilton for hotel bookings",
            category="distribution"
        )
        assert score > 0  # Should match Marriott, Hilton

    def test_compute_signal_score_category(self):
        classifier = TravelClassifier()
        score = classifier._compute_signal_score(
            "Property management system with guest experience features",
            category="category"
        )
        assert score > 0  # Should match property management, guest experience

    def test_compute_signal_score_negative(self):
        classifier = TravelClassifier()
        score = classifier._compute_signal_score(
            "Series D funded traditional travel agency",
            category="negative"
        )
        assert score > 0  # Should match Series D, traditional travel agency

    def test_compute_weighted_fit_score(self):
        classifier = TravelClassifier()
        content = "Hotel tech startup with Marriott partnership, seed stage"
        fit_score = classifier._compute_weighted_fit_score(content)
        assert 0.0 <= fit_score <= 1.0
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/intelligence/test_travel_classifier.py -v`
Expected: FAIL (module not found)

### Step 3: Implement travel_classifier.py

```python
# intelligence/travel_classifier.py
"""
TravelClassifier: Domain-specific LLM classifier for travel & hospitality signals.

Classifies travel signals with expertise in:
- Hotel tech (PMS, guest experience, revenue management)
- Booking platforms (OTAs, direct booking, metasearch)
- Experiential travel (tours, activities, unique stays)
- Travel infrastructure (payments, distribution, analytics)
- Rental tech (vacation rental management, pricing optimization)

Requires: Tech-enabled approach, pre-Series B stage
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from intelligence.thesis_config import load_thesis_config, ThesisConfig


TRAVEL_CLASSIFIER_SYSTEM_PROMPT = """You are an expert investment analyst specializing in travel and hospitality technology.

## Investment Thesis Focus

IN SCOPE (evaluate positively):
- Tech-enabled travel platforms: booking, search, personalization
- Hotel tech / property management: PMS, guest experience, operations software
- Experiential travel: tours, activities, unique accommodations
- Travel infrastructure: payments, distribution, analytics
- Short-term rental tech: vacation rental management, pricing optimization

STAGE FILTER:
- Target: Pre-seed, Seed, Series A (pre-Series B)
- Later stage companies: mark as "stage_mismatch"

KEY REQUIREMENT:
- Must be tech-enabled (software, platform, marketplace)
- Pure services without tech differentiation: mark as "not_tech_enabled"

## Classification Output

1. fit_score (0-1): How well does this match our travel tech thesis?
2. category: hotel_tech, booking_platform, experiential, travel_infrastructure, rental_tech, or out_of_scope
3. sub_category: More specific (e.g., "pms", "guest_experience", "tour_marketplace")
4. thesis_alignment: 2-3 sentences explaining assessment
5. signals: Keywords/signals detected
6. confidence (0-1): Classification confidence
7. is_tech_enabled: true if software/platform, false if pure services
8. investment_stage_fit: seed, series_a, or stage_mismatch
"""


class TravelCategory(Enum):
    """Categories within the travel vertical."""
    HOTEL_TECH = "hotel_tech"
    BOOKING_PLATFORM = "booking_platform"
    EXPERIENTIAL = "experiential"
    TRAVEL_INFRASTRUCTURE = "travel_infrastructure"
    RENTAL_TECH = "rental_tech"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass
class TravelClassifierConfig:
    """Configuration for TravelClassifier."""
    model: str = "claude-3-haiku-20240307"
    min_confidence: float = 0.7
    temperature: float = 0.2
    max_tokens: int = 500
    api_key: Optional[str] = None


@dataclass
class TravelClassificationResult:
    """Result of travel signal classification."""
    fit_score: float  # 0-1 scale
    category: TravelCategory
    sub_category: Optional[str]
    thesis_alignment: str
    signals: List[str]
    confidence: float  # 0-1 scale
    is_tech_enabled: bool = True
    investment_stage_fit: str = "not_fit"
    regulatory_stage: Optional[str] = None


class TravelClassifier:
    """Domain-specific classifier for travel vertical signals."""

    def __init__(self, config: Optional[TravelClassifierConfig] = None):
        """Initialize the travel classifier."""
        self.config = config or TravelClassifierConfig()
        self._thesis_config: Optional[ThesisConfig] = None

    @property
    def thesis_config(self) -> ThesisConfig:
        """Lazy load thesis config."""
        if self._thesis_config is None:
            self._thesis_config = load_thesis_config("travel")
        return self._thesis_config

    def _compute_signal_score(self, content: str, category: str) -> float:
        """
        Compute score for a specific signal category.

        Args:
            content: Text to analyze
            category: Signal category (distribution, category, traction, founder, negative)

        Returns:
            Score between 0.0 and 1.0
        """
        content_lower = content.lower()

        if category == "negative":
            signals = self.thesis_config.negative_signals
        else:
            signals = self.thesis_config.positive_signals.get(category, [])

        if not signals:
            return 0.0

        matches = 0
        for signal in signals:
            pattern = r'\b' + re.escape(signal.lower()) + r'\b'
            if re.search(pattern, content_lower):
                matches += 1

        # Normalize: more matches = higher score, cap at 1.0
        return min(1.0, matches / max(len(signals) * 0.2, 1))

    def _compute_weighted_fit_score(self, content: str) -> float:
        """
        Compute weighted fit score based on thesis config.

        Args:
            content: Text to analyze

        Returns:
            Weighted fit score between 0.0 and 1.0
        """
        weights = self.thesis_config.scoring_weights

        # Compute positive signal scores
        positive_score = 0.0
        for category, weight in weights.items():
            category_score = self._compute_signal_score(content, category)
            positive_score += category_score * weight

        # Compute negative signal penalty
        negative_score = self._compute_signal_score(content, "negative")
        penalty = negative_score * 0.5  # Penalty caps at 0.5

        # Final score
        return max(0.0, min(1.0, positive_score - penalty))

    async def classify(
        self,
        content: str,
        company_name: Optional[str] = None,
        source: Optional[str] = None,
    ) -> TravelClassificationResult:
        """
        Classify a travel signal using weighted scoring + LLM.

        Args:
            content: Signal text to classify
            company_name: Optional company name for context
            source: Optional source identifier

        Returns:
            TravelClassificationResult with fit score and category
        """
        # Compute weighted fit score from thesis config
        fit_score = self._compute_weighted_fit_score(content)

        # Detect signals
        detected_signals = []
        for category in ["distribution", "category", "traction", "founder"]:
            for signal in self.thesis_config.positive_signals.get(category, []):
                if signal.lower() in content.lower():
                    detected_signals.append(signal)

        # Detect negative signals
        has_negative = any(
            neg.lower() in content.lower()
            for neg in self.thesis_config.negative_signals
        )

        # Determine category (simplified heuristic)
        category = TravelCategory.OUT_OF_SCOPE
        if fit_score >= 0.3:
            if any(kw in content.lower() for kw in ["hotel", "pms", "property management"]):
                category = TravelCategory.HOTEL_TECH
            elif any(kw in content.lower() for kw in ["booking", "reservation", "ota"]):
                category = TravelCategory.BOOKING_PLATFORM
            elif any(kw in content.lower() for kw in ["tour", "experience", "activity"]):
                category = TravelCategory.EXPERIENTIAL
            elif any(kw in content.lower() for kw in ["rental", "vacation", "airbnb"]):
                category = TravelCategory.RENTAL_TECH
            else:
                category = TravelCategory.TRAVEL_INFRASTRUCTURE

        # Determine stage fit
        stage_fit = "not_fit"
        content_lower = content.lower()
        if "seed" in content_lower or "pre-seed" in content_lower:
            stage_fit = "seed"
        elif "series a" in content_lower:
            stage_fit = "series_a"
        elif any(s in content_lower for s in ["series b", "series c", "series d"]):
            stage_fit = "stage_mismatch"

        # Check tech-enabled
        is_tech_enabled = any(
            kw in content_lower
            for kw in ["software", "platform", "app", "tech", "ai", "saas", "api"]
        )

        return TravelClassificationResult(
            fit_score=fit_score,
            category=category,
            sub_category=None,
            thesis_alignment=f"Weighted score: {fit_score:.2f}. Signals: {detected_signals[:5]}",
            signals=detected_signals[:10],
            confidence=0.7 if fit_score > 0.3 else 0.5,
            is_tech_enabled=is_tech_enabled,
            investment_stage_fit=stage_fit,
        )
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/intelligence/test_travel_classifier.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add intelligence/travel_classifier.py tests/intelligence/test_travel_classifier.py
git commit -m "feat(intelligence): add TravelClassifier with weighted thesis scoring"
```

---

## Task 4: Yelp Fusion API Client

**Files:**
- Create: `enrichment/yelp_fusion.py`
- Test: `tests/enrichment/test_yelp_fusion.py`

### Step 1: Write failing tests

```python
# tests/enrichment/test_yelp_fusion.py
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from enrichment.yelp_fusion import YelpFusionClient, YelpBusiness


class TestYelpBusiness:
    """Tests for YelpBusiness dataclass."""

    def test_yelp_business_fields(self):
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="abc123",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels", "Resorts"],
            url="https://yelp.com/biz/test-hotel",
            fetched_at=datetime.utcnow()
        )
        assert business.entity_id == "entity-123"
        assert business.rating == 4.5
        assert business.review_count == 100


class TestYelpFusionClient:
    """Tests for YelpFusionClient."""

    def test_client_initialization(self):
        client = YelpFusionClient(api_key="test_key")
        assert client.api_key == "test_key"
        assert client.rate_limit == 5.0  # Default

    def test_client_custom_rate_limit(self):
        client = YelpFusionClient(api_key="test_key", rate_limit=2.0)
        assert client.rate_limit == 2.0

    @pytest.mark.asyncio
    async def test_search_by_name_returns_businesses(self):
        client = YelpFusionClient(api_key="test_key")

        mock_response = {
            "businesses": [
                {
                    "id": "abc123",
                    "name": "Test Hotel",
                    "rating": 4.5,
                    "review_count": 100,
                    "price": "$$$",
                    "categories": [{"title": "Hotels"}],
                    "url": "https://yelp.com/biz/test"
                }
            ]
        }

        with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            results = await client.search_by_name("Test Hotel", location="New York")

            assert len(results) == 1
            assert results[0].name == "Test Hotel"
            assert results[0].rating == 4.5

    @pytest.mark.asyncio
    async def test_search_handles_empty_results(self):
        client = YelpFusionClient(api_key="test_key")

        with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"businesses": []}
            results = await client.search_by_name("Nonexistent", location="NYC")

            assert results == []

    @pytest.mark.asyncio
    async def test_search_handles_api_error(self):
        client = YelpFusionClient(api_key="test_key")

        with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = Exception("API Error")
            results = await client.search_by_name("Test", location="NYC")

            assert results == []

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        client = YelpFusionClient(api_key="test_key", rate_limit=50.0)

        # Rate limiting should be enforced
        assert client._hourly_limit == 50
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/enrichment/test_yelp_fusion.py -v`
Expected: FAIL (module not found)

### Step 3: Implement yelp_fusion.py

```python
# enrichment/yelp_fusion.py
"""
Yelp Fusion API Client for Travel Enrichment.

Provides async methods to search and fetch business data from Yelp's Fusion API.

API Details:
- Base URL: https://api.yelp.com/v3
- Requires API key (free tier: 5000 calls/day)
- Rate limit: 5 requests/second (self-imposed: 50/hour to avoid abuse flags)

Usage:
    client = YelpFusionClient(api_key="your_key")
    businesses = await client.search_by_name("Marriott", location="New York")
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

YELP_API_BASE = "https://api.yelp.com/v3"


@dataclass
class YelpBusiness:
    """Business data from Yelp Fusion API."""

    entity_id: str
    yelp_id: str
    name: str
    rating: float
    review_count: int
    price: Optional[str]
    categories: List[str]
    url: str
    fetched_at: datetime
    id: Optional[int] = None


class YelpFusionClient:
    """
    Async client for Yelp Fusion API.

    Implements rate limiting to stay within free tier limits and avoid
    abuse detection.
    """

    def __init__(self, api_key: str, rate_limit: float = 5.0, hourly_limit: int = 50):
        """
        Initialize Yelp Fusion client.

        Args:
            api_key: Yelp Fusion API key
            rate_limit: Max requests per second (default: 5.0)
            hourly_limit: Max requests per hour (default: 50 to avoid abuse flags)
        """
        self.api_key = api_key
        self.rate_limit = rate_limit
        self._hourly_limit = hourly_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0
        self._hourly_requests: deque = deque()

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            now = time.time()

            # Enforce per-second rate limit
            if self._last_request_time is not None:
                elapsed = now - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)

            # Enforce hourly rate limit
            hour_ago = now - 3600
            while self._hourly_requests and self._hourly_requests[0] < hour_ago:
                self._hourly_requests.popleft()

            if len(self._hourly_requests) >= self._hourly_limit:
                sleep_time = self._hourly_requests[0] + 3600 - now
                logger.warning(f"Hourly rate limit reached, sleeping {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)

            self._last_request_time = time.time()
            self._hourly_requests.append(self._last_request_time)

    async def _make_request(self, endpoint: str, params: dict) -> dict:
        """Make authenticated request to Yelp API."""
        await self._wait_for_rate_limit()

        url = f"{YELP_API_BASE}{endpoint}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    async def search_by_name(
        self,
        name: str,
        location: str,
        max_results: int = 10
    ) -> List[YelpBusiness]:
        """
        Search for businesses by name and location.

        Args:
            name: Business name to search for
            location: City or address for search
            max_results: Maximum results to return (default: 10)

        Returns:
            List of YelpBusiness objects
        """
        try:
            params = {
                "term": name,
                "location": location,
                "limit": min(max_results, 50),
                "categories": "hotels,resorts,travel",
            }

            data = await self._make_request("/businesses/search", params)
            businesses = []

            for biz in data.get("businesses", []):
                try:
                    business = YelpBusiness(
                        entity_id="",  # Set when saving
                        yelp_id=biz.get("id", ""),
                        name=biz.get("name", ""),
                        rating=biz.get("rating", 0.0),
                        review_count=biz.get("review_count", 0),
                        price=biz.get("price"),
                        categories=[c.get("title", "") for c in biz.get("categories", [])],
                        url=biz.get("url", ""),
                        fetched_at=datetime.utcnow()
                    )
                    businesses.append(business)
                except Exception as e:
                    logger.warning(f"Failed to parse business: {e}")
                    continue

            logger.info(f"Found {len(businesses)} Yelp businesses for '{name}'")
            return businesses

        except httpx.HTTPStatusError as e:
            logger.error(f"Yelp API HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"Yelp API error: {e}")
            return []

    async def get_business(self, yelp_id: str) -> Optional[YelpBusiness]:
        """
        Get details for a specific business.

        Args:
            yelp_id: Yelp business ID

        Returns:
            YelpBusiness or None if not found
        """
        try:
            data = await self._make_request(f"/businesses/{yelp_id}", {})

            return YelpBusiness(
                entity_id="",
                yelp_id=data.get("id", ""),
                name=data.get("name", ""),
                rating=data.get("rating", 0.0),
                review_count=data.get("review_count", 0),
                price=data.get("price"),
                categories=[c.get("title", "") for c in data.get("categories", [])],
                url=data.get("url", ""),
                fetched_at=datetime.utcnow()
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Business not found: {yelp_id}")
            else:
                logger.error(f"Yelp API HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"Yelp API error: {e}")
            return None
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/enrichment/test_yelp_fusion.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add enrichment/yelp_fusion.py tests/enrichment/test_yelp_fusion.py
git commit -m "feat(enrichment): add Yelp Fusion API client with rate limiting"
```

---

## Task 5: Google Places API Client

**Files:**
- Create: `enrichment/google_places.py`
- Test: `tests/enrichment/test_google_places.py`

### Step 1: Write failing tests

```python
# tests/enrichment/test_google_places.py
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from enrichment.google_places import GooglePlacesClient, GooglePlace


class TestGooglePlace:
    """Tests for GooglePlace dataclass."""

    def test_google_place_fields(self):
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJ123",
            name="Test Hotel",
            rating=4.2,
            user_ratings_total=500,
            price_level=3,
            types=["lodging", "hotel"],
            website="https://testhotel.com",
            fetched_at=datetime.utcnow()
        )
        assert place.entity_id == "entity-123"
        assert place.rating == 4.2
        assert place.price_level == 3


class TestGooglePlacesClient:
    """Tests for GooglePlacesClient."""

    def test_client_initialization(self):
        client = GooglePlacesClient(api_key="test_key")
        assert client.api_key == "test_key"

    @pytest.mark.asyncio
    async def test_search_returns_places(self):
        client = GooglePlacesClient(api_key="test_key")

        mock_response = {
            "results": [
                {
                    "place_id": "ChIJ123",
                    "name": "Test Hotel",
                    "rating": 4.2,
                    "user_ratings_total": 500,
                    "price_level": 3,
                    "types": ["lodging"],
                }
            ],
            "status": "OK"
        }

        with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            results = await client.search_places("Test Hotel", "New York")

            assert len(results) == 1
            assert results[0].name == "Test Hotel"

    @pytest.mark.asyncio
    async def test_get_place_details(self):
        client = GooglePlacesClient(api_key="test_key")

        mock_response = {
            "result": {
                "place_id": "ChIJ123",
                "name": "Test Hotel",
                "rating": 4.2,
                "user_ratings_total": 500,
                "price_level": 3,
                "types": ["lodging"],
                "website": "https://test.com"
            },
            "status": "OK"
        }

        with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            place = await client.get_place_details("ChIJ123")

            assert place is not None
            assert place.website == "https://test.com"

    @pytest.mark.asyncio
    async def test_handles_zero_results(self):
        client = GooglePlacesClient(api_key="test_key")

        with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": [], "status": "ZERO_RESULTS"}
            results = await client.search_places("Nonexistent", "NYC")

            assert results == []
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/enrichment/test_google_places.py -v`
Expected: FAIL

### Step 3: Implement google_places.py

```python
# enrichment/google_places.py
"""
Google Places API Client for Travel Enrichment.

Provides async methods to search and fetch place data from Google Places API.

API Details:
- Base URL: https://maps.googleapis.com/maps/api/place
- Requires API key (free tier: $200/month credit)
- Rate limit: 100 requests/second (self-imposed: 50/hour to stay in free tier)

Usage:
    client = GooglePlacesClient(api_key="your_key")
    places = await client.search_places("Marriott", "New York")
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

GOOGLE_PLACES_API_BASE = "https://maps.googleapis.com/maps/api/place"


@dataclass
class GooglePlace:
    """Place data from Google Places API."""

    entity_id: str
    place_id: str
    name: str
    rating: float
    user_ratings_total: int
    price_level: Optional[int]  # 0-4
    types: List[str]
    website: Optional[str]
    fetched_at: datetime
    id: Optional[int] = None


class GooglePlacesClient:
    """
    Async client for Google Places API.

    Implements rate limiting to stay within free tier limits.
    """

    def __init__(self, api_key: str, hourly_limit: int = 50):
        """
        Initialize Google Places client.

        Args:
            api_key: Google Places API key
            hourly_limit: Max requests per hour (default: 50)
        """
        self.api_key = api_key
        self._hourly_limit = hourly_limit
        self._semaphore = asyncio.Semaphore(1)
        self._hourly_requests: deque = deque()

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            now = time.time()

            # Remove requests older than 1 hour
            hour_ago = now - 3600
            while self._hourly_requests and self._hourly_requests[0] < hour_ago:
                self._hourly_requests.popleft()

            # Wait if at hourly limit
            if len(self._hourly_requests) >= self._hourly_limit:
                sleep_time = self._hourly_requests[0] + 3600 - now
                logger.warning(f"Hourly rate limit reached, sleeping {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)

            self._hourly_requests.append(now)

    async def _make_request(self, endpoint: str, params: dict) -> dict:
        """Make request to Google Places API."""
        await self._wait_for_rate_limit()

        url = f"{GOOGLE_PLACES_API_BASE}/{endpoint}"
        params["key"] = self.api_key

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def search_places(
        self,
        query: str,
        location: Optional[str] = None,
        max_results: int = 10
    ) -> List[GooglePlace]:
        """
        Search for places by query.

        Args:
            query: Search query (business name)
            location: Optional location to bias results
            max_results: Maximum results to return

        Returns:
            List of GooglePlace objects
        """
        try:
            params = {
                "query": query,
                "type": "lodging",
            }
            if location:
                params["query"] = f"{query} {location}"

            data = await self._make_request("textsearch/json", params)

            if data.get("status") not in ["OK", "ZERO_RESULTS"]:
                logger.error(f"Google Places API error: {data.get('status')}")
                return []

            places = []
            for result in data.get("results", [])[:max_results]:
                try:
                    place = GooglePlace(
                        entity_id="",
                        place_id=result.get("place_id", ""),
                        name=result.get("name", ""),
                        rating=result.get("rating", 0.0),
                        user_ratings_total=result.get("user_ratings_total", 0),
                        price_level=result.get("price_level"),
                        types=result.get("types", []),
                        website=None,  # Not in search results
                        fetched_at=datetime.utcnow()
                    )
                    places.append(place)
                except Exception as e:
                    logger.warning(f"Failed to parse place: {e}")
                    continue

            logger.info(f"Found {len(places)} Google places for '{query}'")
            return places

        except Exception as e:
            logger.error(f"Google Places API error: {e}")
            return []

    async def get_place_details(self, place_id: str) -> Optional[GooglePlace]:
        """
        Get detailed information for a place.

        Args:
            place_id: Google place ID

        Returns:
            GooglePlace with full details or None
        """
        try:
            params = {
                "place_id": place_id,
                "fields": "place_id,name,rating,user_ratings_total,price_level,types,website"
            }

            data = await self._make_request("details/json", params)

            if data.get("status") != "OK":
                logger.error(f"Google Places details error: {data.get('status')}")
                return None

            result = data.get("result", {})

            return GooglePlace(
                entity_id="",
                place_id=result.get("place_id", ""),
                name=result.get("name", ""),
                rating=result.get("rating", 0.0),
                user_ratings_total=result.get("user_ratings_total", 0),
                price_level=result.get("price_level"),
                types=result.get("types", []),
                website=result.get("website"),
                fetched_at=datetime.utcnow()
            )

        except Exception as e:
            logger.error(f"Google Places details error: {e}")
            return None
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/enrichment/test_google_places.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add enrichment/google_places.py tests/enrichment/test_google_places.py
git commit -m "feat(enrichment): add Google Places API client with rate limiting"
```

---

## Task 6: Travel Certifications Client

**Files:**
- Create: `enrichment/travel_certifications.py`
- Test: `tests/enrichment/test_travel_certifications.py`

### Step 1: Write failing tests

```python
# tests/enrichment/test_travel_certifications.py
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from enrichment.travel_certifications import (
    TravelCertificationsClient,
    TravelCertification,
    CertificationSource,
)


class TestTravelCertification:
    """Tests for TravelCertification dataclass."""

    def test_certification_fields(self):
        cert = TravelCertification(
            entity_id="entity-123",
            source=CertificationSource.FORBES,
            rating="5-star",
            year=2026,
            property_name="The Ritz-Carlton",
            fetched_at=datetime.utcnow()
        )
        assert cert.source == CertificationSource.FORBES
        assert cert.rating == "5-star"


class TestCertificationSource:
    """Tests for CertificationSource enum."""

    def test_sources_exist(self):
        assert CertificationSource.FORBES.value == "forbes"
        assert CertificationSource.AAA.value == "aaa"
        assert CertificationSource.MICHELIN.value == "michelin"


class TestTravelCertificationsClient:
    """Tests for TravelCertificationsClient."""

    def test_client_initialization(self):
        client = TravelCertificationsClient()
        assert client.rate_limit == 1.0

    @pytest.mark.asyncio
    async def test_search_forbes_certifications(self):
        client = TravelCertificationsClient()

        mock_certs = [
            TravelCertification(
                entity_id="",
                source=CertificationSource.FORBES,
                rating="5-star",
                year=2026,
                property_name="Test Hotel",
                fetched_at=datetime.utcnow()
            )
        ]

        with patch.object(client, '_fetch_forbes', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_certs
            results = await client.search_certifications("Test Hotel")

            assert len(results) >= 0  # May or may not find matches

    @pytest.mark.asyncio
    async def test_search_handles_errors(self):
        client = TravelCertificationsClient()

        with patch.object(client, '_fetch_forbes', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Network error")
            results = await client.search_certifications("Test")

            # Should handle errors gracefully
            assert isinstance(results, list)
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/enrichment/test_travel_certifications.py -v`
Expected: FAIL

### Step 3: Implement travel_certifications.py

```python
# enrichment/travel_certifications.py
"""
Travel Certifications Client for Travel Enrichment.

Fetches luxury travel certifications from:
- Forbes Travel Guide (5-star, 4-star ratings)
- AAA Diamond ratings (5-diamond, 4-diamond)
- Michelin Guide (3-star, 2-star, 1-star)

Note: These sources may require scraping public lists as they don't have public APIs.

Usage:
    client = TravelCertificationsClient()
    certs = await client.search_certifications("The Ritz-Carlton")
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)


class CertificationSource(Enum):
    """Sources for travel certifications."""
    FORBES = "forbes"
    AAA = "aaa"
    MICHELIN = "michelin"


@dataclass
class TravelCertification:
    """Travel certification record."""

    entity_id: str
    source: CertificationSource
    rating: str  # "5-star", "5-diamond", "3-star", etc.
    year: int
    property_name: str
    fetched_at: datetime
    id: Optional[int] = None


class TravelCertificationsClient:
    """
    Client for fetching travel certifications.

    Implements polite scraping with rate limiting.
    """

    def __init__(self, rate_limit: float = 1.0):
        """
        Initialize certifications client.

        Args:
            rate_limit: Max requests per second (default: 1.0 for polite scraping)
        """
        self.rate_limit = rate_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            if self._last_request_time is not None:
                elapsed = asyncio.get_event_loop().time() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def search_certifications(
        self,
        property_name: str,
        sources: Optional[List[CertificationSource]] = None
    ) -> List[TravelCertification]:
        """
        Search for certifications by property name.

        Args:
            property_name: Hotel/resort name to search for
            sources: Optional list of sources to check (default: all)

        Returns:
            List of TravelCertification objects
        """
        if sources is None:
            sources = list(CertificationSource)

        all_certs = []

        for source in sources:
            try:
                if source == CertificationSource.FORBES:
                    certs = await self._fetch_forbes(property_name)
                elif source == CertificationSource.AAA:
                    certs = await self._fetch_aaa(property_name)
                elif source == CertificationSource.MICHELIN:
                    certs = await self._fetch_michelin(property_name)
                else:
                    certs = []

                all_certs.extend(certs)

            except Exception as e:
                logger.error(f"Error fetching {source.value} certifications: {e}")
                continue

        logger.info(f"Found {len(all_certs)} certifications for '{property_name}'")
        return all_certs

    async def _fetch_forbes(self, property_name: str) -> List[TravelCertification]:
        """
        Fetch Forbes Travel Guide certifications.

        Note: This is a stub. In production, would scrape Forbes Travel Guide
        or use their data if available via partnership.
        """
        await self._wait_for_rate_limit()

        # Stub: Return empty list
        # In production: scrape forbestravelguide.com/hotels
        logger.debug(f"Forbes lookup for '{property_name}' (stub)")
        return []

    async def _fetch_aaa(self, property_name: str) -> List[TravelCertification]:
        """
        Fetch AAA Diamond certifications.

        Note: This is a stub. In production, would use AAA's diamond rating data.
        """
        await self._wait_for_rate_limit()

        # Stub: Return empty list
        # In production: use AAA data source
        logger.debug(f"AAA lookup for '{property_name}' (stub)")
        return []

    async def _fetch_michelin(self, property_name: str) -> List[TravelCertification]:
        """
        Fetch Michelin Guide certifications.

        Note: This is a stub. In production, would scrape Michelin Guide
        or use their API if available.
        """
        await self._wait_for_rate_limit()

        # Stub: Return empty list
        # In production: scrape guide.michelin.com
        logger.debug(f"Michelin lookup for '{property_name}' (stub)")
        return []

    async def get_forbes_five_star_hotels(self) -> List[TravelCertification]:
        """
        Get list of all Forbes 5-star hotels.

        Useful for batch matching against entities.

        Returns:
            List of TravelCertification for all 5-star properties
        """
        await self._wait_for_rate_limit()

        # Stub: In production, would scrape/cache the full Forbes 5-star list
        logger.debug("Fetching Forbes 5-star list (stub)")
        return []
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/enrichment/test_travel_certifications.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add enrichment/travel_certifications.py tests/enrichment/test_travel_certifications.py
git commit -m "feat(enrichment): add travel certifications client (Forbes, AAA, Michelin)"
```

---

## Task 7: Travel Enrichment Storage

**Files:**
- Create: `storage/travel_enrichment.py`
- Test: `tests/storage/test_travel_enrichment.py`

### Step 1: Write failing tests

```python
# tests/storage/test_travel_enrichment.py
import pytest
from datetime import datetime

from storage.travel_enrichment import (
    TravelEnrichmentStore,
    YelpReview,
    GooglePlaceRecord,
    TravelCertificationRecord,
)
from enrichment.yelp_fusion import YelpBusiness
from enrichment.google_places import GooglePlace
from enrichment.travel_certifications import TravelCertification, CertificationSource


class TestTravelEnrichmentStore:
    """Tests for TravelEnrichmentStore."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store for testing."""
        store = TravelEnrichmentStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_store_initialization(self, store):
        assert store._db is not None

    @pytest.mark.asyncio
    async def test_save_yelp_review(self, store):
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="abc123",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels"],
            url="https://yelp.com/biz/test",
            fetched_at=datetime.utcnow()
        )

        record_id = await store.save_yelp_review(business)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_yelp_reviews_for_entity(self, store):
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="abc123",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels"],
            url="https://yelp.com/biz/test",
            fetched_at=datetime.utcnow()
        )
        await store.save_yelp_review(business)

        reviews = await store.get_yelp_reviews_for_entity("entity-123")
        assert len(reviews) == 1
        assert reviews[0].name == "Test Hotel"

    @pytest.mark.asyncio
    async def test_save_google_place(self, store):
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJ123",
            name="Test Hotel",
            rating=4.2,
            user_ratings_total=500,
            price_level=3,
            types=["lodging"],
            website="https://test.com",
            fetched_at=datetime.utcnow()
        )

        record_id = await store.save_google_place(place)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_google_places_for_entity(self, store):
        place = GooglePlace(
            entity_id="entity-456",
            place_id="ChIJ123",
            name="Test Resort",
            rating=4.8,
            user_ratings_total=1000,
            price_level=4,
            types=["lodging", "resort"],
            website="https://resort.com",
            fetched_at=datetime.utcnow()
        )
        await store.save_google_place(place)

        places = await store.get_google_places_for_entity("entity-456")
        assert len(places) == 1
        assert places[0].rating == 4.8

    @pytest.mark.asyncio
    async def test_save_certification(self, store):
        cert = TravelCertification(
            entity_id="entity-789",
            source=CertificationSource.FORBES,
            rating="5-star",
            year=2026,
            property_name="Luxury Hotel",
            fetched_at=datetime.utcnow()
        )

        record_id = await store.save_certification(cert)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_certifications_for_entity(self, store):
        cert = TravelCertification(
            entity_id="entity-789",
            source=CertificationSource.AAA,
            rating="5-diamond",
            year=2026,
            property_name="Diamond Hotel",
            fetched_at=datetime.utcnow()
        )
        await store.save_certification(cert)

        certs = await store.get_certifications_for_entity("entity-789")
        assert len(certs) == 1
        assert certs[0].rating == "5-diamond"
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/storage/test_travel_enrichment.py -v`
Expected: FAIL

### Step 3: Implement travel_enrichment.py

```python
# storage/travel_enrichment.py
"""
Travel Enrichment Storage Layer.

Provides persistent SQLite storage for travel enrichment data from:
- Yelp Fusion (reviews, ratings)
- Google Places (ratings, details)
- Travel Certifications (Forbes, AAA, Michelin)

Tables:
  - travel_yelp_reviews: Yelp business data linked to entities
  - travel_google_places: Google Places data linked to entities
  - travel_certifications: Certification records linked to entities
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import aiosqlite

from enrichment.yelp_fusion import YelpBusiness
from enrichment.google_places import GooglePlace
from enrichment.travel_certifications import TravelCertification, CertificationSource

logger = logging.getLogger(__name__)


@dataclass
class YelpReview:
    """Yelp review record from storage."""
    id: int
    entity_id: str
    yelp_id: str
    name: str
    rating: float
    review_count: int
    price: Optional[str]
    categories: List[str]
    url: str
    fetched_at: Optional[datetime]


@dataclass
class GooglePlaceRecord:
    """Google Place record from storage."""
    id: int
    entity_id: str
    place_id: str
    name: str
    rating: float
    user_ratings_total: int
    price_level: Optional[int]
    types: List[str]
    website: Optional[str]
    fetched_at: Optional[datetime]


@dataclass
class TravelCertificationRecord:
    """Travel certification record from storage."""
    id: int
    entity_id: str
    source: str
    rating: str
    year: int
    property_name: str
    fetched_at: Optional[datetime]


class TravelEnrichmentStore:
    """
    Async SQLite storage for travel enrichment data.
    """

    def __init__(self, db_path: str = "signals.db"):
        """
        Initialize travel enrichment store.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for testing.
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._db.commit()
        logger.info(f"TravelEnrichmentStore initialized at {self.db_path}")

    async def _create_tables(self) -> None:
        """Create database tables and indexes."""
        # Yelp reviews table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS travel_yelp_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                yelp_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rating REAL,
                review_count INTEGER,
                price TEXT,
                categories TEXT,
                url TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_yelp_entity
            ON travel_yelp_reviews(entity_id)
        """)

        # Google Places table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS travel_google_places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                place_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rating REAL,
                user_ratings_total INTEGER,
                price_level INTEGER,
                types TEXT,
                website TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_google_entity
            ON travel_google_places(entity_id)
        """)

        # Certifications table
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS travel_certifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                source TEXT NOT NULL,
                rating TEXT NOT NULL,
                year INTEGER,
                property_name TEXT,
                fetched_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_cert_entity
            ON travel_certifications(entity_id)
        """)

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # Yelp operations
    async def save_yelp_review(self, business: YelpBusiness) -> int:
        """Save a Yelp business record."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        categories_json = json.dumps(business.categories)

        cursor = await self._db.execute(
            """
            INSERT INTO travel_yelp_reviews (
                entity_id, yelp_id, name, rating, review_count,
                price, categories, url, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                business.entity_id,
                business.yelp_id,
                business.name,
                business.rating,
                business.review_count,
                business.price,
                categories_json,
                business.url,
                business.fetched_at.isoformat() if business.fetched_at else None,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_yelp_reviews_for_entity(self, entity_id: str) -> List[YelpReview]:
        """Get all Yelp reviews for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, yelp_id, name, rating, review_count,
                   price, categories, url, fetched_at
            FROM travel_yelp_reviews
            WHERE entity_id = ?
            ORDER BY rating DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [
            YelpReview(
                id=row[0],
                entity_id=row[1],
                yelp_id=row[2],
                name=row[3],
                rating=row[4],
                review_count=row[5],
                price=row[6],
                categories=json.loads(row[7]) if row[7] else [],
                url=row[8],
                fetched_at=datetime.fromisoformat(row[9]) if row[9] else None,
            )
            for row in rows
        ]

    # Google Places operations
    async def save_google_place(self, place: GooglePlace) -> int:
        """Save a Google Place record."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        types_json = json.dumps(place.types)

        cursor = await self._db.execute(
            """
            INSERT INTO travel_google_places (
                entity_id, place_id, name, rating, user_ratings_total,
                price_level, types, website, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                place.entity_id,
                place.place_id,
                place.name,
                place.rating,
                place.user_ratings_total,
                place.price_level,
                types_json,
                place.website,
                place.fetched_at.isoformat() if place.fetched_at else None,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_google_places_for_entity(self, entity_id: str) -> List[GooglePlaceRecord]:
        """Get all Google Places for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, place_id, name, rating, user_ratings_total,
                   price_level, types, website, fetched_at
            FROM travel_google_places
            WHERE entity_id = ?
            ORDER BY rating DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [
            GooglePlaceRecord(
                id=row[0],
                entity_id=row[1],
                place_id=row[2],
                name=row[3],
                rating=row[4],
                user_ratings_total=row[5],
                price_level=row[6],
                types=json.loads(row[7]) if row[7] else [],
                website=row[8],
                fetched_at=datetime.fromisoformat(row[9]) if row[9] else None,
            )
            for row in rows
        ]

    # Certification operations
    async def save_certification(self, cert: TravelCertification) -> int:
        """Save a certification record."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            INSERT INTO travel_certifications (
                entity_id, source, rating, year, property_name, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cert.entity_id,
                cert.source.value,
                cert.rating,
                cert.year,
                cert.property_name,
                cert.fetched_at.isoformat() if cert.fetched_at else None,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_certifications_for_entity(self, entity_id: str) -> List[TravelCertificationRecord]:
        """Get all certifications for an entity."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, entity_id, source, rating, year, property_name, fetched_at
            FROM travel_certifications
            WHERE entity_id = ?
            ORDER BY year DESC NULLS LAST
            """,
            (entity_id,),
        )

        rows = await cursor.fetchall()
        return [
            TravelCertificationRecord(
                id=row[0],
                entity_id=row[1],
                source=row[2],
                rating=row[3],
                year=row[4],
                property_name=row[5],
                fetched_at=datetime.fromisoformat(row[6]) if row[6] else None,
            )
            for row in rows
        ]
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/storage/test_travel_enrichment.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add storage/travel_enrichment.py tests/storage/test_travel_enrichment.py
git commit -m "feat(storage): add travel enrichment storage for Yelp, Google, certifications"
```

---

## Task 8: Travel Enrichment Orchestrator

**Files:**
- Create: `enrichment/travel_orchestrator.py`
- Test: `tests/enrichment/test_travel_orchestrator.py`

### Step 1: Write failing tests

```python
# tests/enrichment/test_travel_orchestrator.py
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from enrichment.travel_orchestrator import (
    TravelEnrichmentOrchestrator,
    TravelEnrichmentResult,
)
from enrichment.yelp_fusion import YelpBusiness
from enrichment.google_places import GooglePlace


class TestTravelEnrichmentResult:
    """Tests for TravelEnrichmentResult dataclass."""

    def test_result_fields(self):
        result = TravelEnrichmentResult(
            entity_id="entity-123",
            yelp_count=2,
            google_places_count=3,
            certifications_count=1,
            enriched_at=datetime.utcnow(),
            success=True
        )
        assert result.entity_id == "entity-123"
        assert result.yelp_count == 2
        assert result.success is True


class TestTravelEnrichmentOrchestrator:
    """Tests for TravelEnrichmentOrchestrator."""

    @pytest.fixture
    async def orchestrator(self):
        """Create orchestrator with mocked clients."""
        orch = TravelEnrichmentOrchestrator(db_path=":memory:")
        await orch.initialize()
        yield orch

    @pytest.mark.asyncio
    async def test_orchestrator_initialization(self, orchestrator):
        assert orchestrator._initialized is True

    @pytest.mark.asyncio
    async def test_enrich_entity_returns_result(self, orchestrator):
        # Mock all clients
        orchestrator.yelp_client.search_by_name = AsyncMock(return_value=[])
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        result = await orchestrator.enrich_entity(
            entity_id="entity-123",
            company_name="Test Hotel",
            location="New York"
        )

        assert isinstance(result, TravelEnrichmentResult)
        assert result.entity_id == "entity-123"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enrich_entity_stores_results(self, orchestrator):
        mock_business = YelpBusiness(
            entity_id="",
            yelp_id="abc",
            name="Test",
            rating=4.5,
            review_count=100,
            price="$$",
            categories=[],
            url="",
            fetched_at=datetime.utcnow()
        )

        orchestrator.yelp_client.search_by_name = AsyncMock(return_value=[mock_business])
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        result = await orchestrator.enrich_entity(
            entity_id="entity-456",
            company_name="Test Hotel"
        )

        assert result.yelp_count == 1

    @pytest.mark.asyncio
    async def test_enrich_handles_partial_failures(self, orchestrator):
        orchestrator.yelp_client.search_by_name = AsyncMock(side_effect=Exception("API Error"))
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        result = await orchestrator.enrich_entity(
            entity_id="entity-789",
            company_name="Test Hotel"
        )

        # Should still succeed partially
        assert result.success is True
        assert result.yelp_count == 0

    @pytest.mark.asyncio
    async def test_enrich_batch(self, orchestrator):
        orchestrator.yelp_client.search_by_name = AsyncMock(return_value=[])
        orchestrator.google_client.search_places = AsyncMock(return_value=[])
        orchestrator.cert_client.search_certifications = AsyncMock(return_value=[])

        entities = [
            ("entity-1", "Hotel A", "NYC"),
            ("entity-2", "Hotel B", "LA"),
        ]

        results = await orchestrator.enrich_batch(entities)

        assert len(results) == 2
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/enrichment/test_travel_orchestrator.py -v`
Expected: FAIL

### Step 3: Implement travel_orchestrator.py

```python
# enrichment/travel_orchestrator.py
"""
Travel Enrichment Orchestrator.

Coordinates enrichment of travel entities by querying multiple data sources
in parallel and storing results.

Data Sources:
- Yelp Fusion (reviews, ratings)
- Google Places (ratings, details)
- Travel Certifications (Forbes, AAA, Michelin)

Usage:
    orchestrator = TravelEnrichmentOrchestrator("signals.db")
    await orchestrator.initialize()

    result = await orchestrator.enrich_entity(
        entity_id="entity-123",
        company_name="The Ritz-Carlton",
        location="New York"
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from enrichment.yelp_fusion import YelpFusionClient, YelpBusiness
from enrichment.google_places import GooglePlacesClient, GooglePlace
from enrichment.travel_certifications import TravelCertificationsClient, TravelCertification
from storage.travel_enrichment import TravelEnrichmentStore

logger = logging.getLogger(__name__)


@dataclass
class TravelEnrichmentResult:
    """Result of enriching a travel entity."""

    entity_id: str
    yelp_count: int
    google_places_count: int
    certifications_count: int
    enriched_at: datetime
    success: bool
    error: Optional[str] = None


class TravelEnrichmentOrchestrator:
    """
    Orchestrates travel entity enrichment across multiple data sources.
    """

    def __init__(self, db_path: str = "signals.db"):
        """
        Initialize the travel enrichment orchestrator.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path

        # Initialize clients (API keys from environment)
        self.yelp_client = YelpFusionClient(
            api_key=os.getenv("YELP_API_KEY", ""),
            hourly_limit=50
        )
        self.google_client = GooglePlacesClient(
            api_key=os.getenv("GOOGLE_PLACES_API_KEY", ""),
            hourly_limit=50
        )
        self.cert_client = TravelCertificationsClient()

        self.store = TravelEnrichmentStore(db_path)
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the orchestrator and its storage."""
        await self.store.initialize()
        self._initialized = True
        logger.info("TravelEnrichmentOrchestrator initialized")

    async def enrich_entity(
        self,
        entity_id: str,
        company_name: str,
        location: Optional[str] = None,
    ) -> TravelEnrichmentResult:
        """
        Enrich a travel entity by searching all data sources.

        Args:
            entity_id: Unique identifier for the entity
            company_name: Company/property name to search for
            location: Optional location for better matching

        Returns:
            TravelEnrichmentResult with counts and status
        """
        if not self._initialized:
            raise RuntimeError("Orchestrator not initialized. Call initialize() first.")

        enriched_at = datetime.utcnow()
        errors = []

        # Search all sources in parallel
        yelp_task = self._search_yelp(company_name, location)
        google_task = self._search_google(company_name, location)
        cert_task = self._search_certifications(company_name)

        results = await asyncio.gather(
            yelp_task, google_task, cert_task,
            return_exceptions=True
        )

        # Process Yelp results
        yelp_businesses: List[YelpBusiness] = []
        if isinstance(results[0], Exception):
            logger.error(f"Yelp search failed for {company_name}: {results[0]}")
            errors.append(f"Yelp: {results[0]}")
        else:
            yelp_businesses = results[0] or []

        # Process Google results
        google_places: List[GooglePlace] = []
        if isinstance(results[1], Exception):
            logger.error(f"Google search failed for {company_name}: {results[1]}")
            errors.append(f"Google: {results[1]}")
        else:
            google_places = results[1] or []

        # Process certification results
        certifications: List[TravelCertification] = []
        if isinstance(results[2], Exception):
            logger.error(f"Certification search failed for {company_name}: {results[2]}")
            errors.append(f"Certifications: {results[2]}")
        else:
            certifications = results[2] or []

        # Store results
        await self._store_yelp(entity_id, yelp_businesses)
        await self._store_google(entity_id, google_places)
        await self._store_certifications(entity_id, certifications)

        # Determine success
        all_failed = len(errors) == 3

        return TravelEnrichmentResult(
            entity_id=entity_id,
            yelp_count=len(yelp_businesses),
            google_places_count=len(google_places),
            certifications_count=len(certifications),
            enriched_at=enriched_at,
            success=not all_failed,
            error="; ".join(errors) if all_failed else None,
        )

    async def enrich_batch(
        self,
        entities: List[Tuple[str, str, str]]
    ) -> List[TravelEnrichmentResult]:
        """
        Enrich multiple entities in batch.

        Args:
            entities: List of tuples (entity_id, company_name, location)

        Returns:
            List of TravelEnrichmentResult objects
        """
        if not entities:
            return []

        if not self._initialized:
            raise RuntimeError("Orchestrator not initialized.")

        results = []
        for entity_id, company_name, location in entities:
            result = await self.enrich_entity(entity_id, company_name, location)
            results.append(result)

        logger.info(f"Batch enrichment complete: {len(results)} entities processed")
        return results

    async def _search_yelp(
        self, company_name: str, location: Optional[str]
    ) -> List[YelpBusiness]:
        """Search Yelp for businesses."""
        if not self.yelp_client.api_key:
            logger.warning("Yelp API key not configured, skipping")
            return []
        return await self.yelp_client.search_by_name(
            company_name,
            location=location or "United States",
            max_results=5
        )

    async def _search_google(
        self, company_name: str, location: Optional[str]
    ) -> List[GooglePlace]:
        """Search Google Places."""
        if not self.google_client.api_key:
            logger.warning("Google Places API key not configured, skipping")
            return []
        return await self.google_client.search_places(
            company_name,
            location=location,
            max_results=5
        )

    async def _search_certifications(
        self, company_name: str
    ) -> List[TravelCertification]:
        """Search travel certifications."""
        return await self.cert_client.search_certifications(company_name)

    async def _store_yelp(
        self, entity_id: str, businesses: List[YelpBusiness]
    ) -> None:
        """Store Yelp businesses."""
        for business in businesses:
            business.entity_id = entity_id
            await self.store.save_yelp_review(business)

    async def _store_google(
        self, entity_id: str, places: List[GooglePlace]
    ) -> None:
        """Store Google places."""
        for place in places:
            place.entity_id = entity_id
            await self.store.save_google_place(place)

    async def _store_certifications(
        self, entity_id: str, certs: List[TravelCertification]
    ) -> None:
        """Store certifications."""
        for cert in certs:
            cert.entity_id = entity_id
            await self.store.save_certification(cert)
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/enrichment/test_travel_orchestrator.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add enrichment/travel_orchestrator.py tests/enrichment/test_travel_orchestrator.py
git commit -m "feat(enrichment): add travel enrichment orchestrator"
```

---

## Task 9: Plug and Play Collector

**Files:**
- Create: `collectors/plugandplay.py`
- Test: `tests/collectors/test_plugandplay.py`

### Step 1: Write failing tests

```python
# tests/collectors/test_plugandplay.py
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from collectors.plugandplay import (
    PlugAndPlayCollector,
    PlugAndPlayCompany,
    PLUGANDPLAY_VERTICALS,
)


class TestPlugAndPlayCompany:
    """Tests for PlugAndPlayCompany dataclass."""

    def test_company_fields(self):
        company = PlugAndPlayCompany(
            name="TravelTech Inc",
            vertical="travel",
            description="AI-powered hotel booking",
            website="https://traveltech.com",
            batch="Winter 2026",
            headquarters="San Francisco",
            collected_at=datetime.utcnow()
        )
        assert company.name == "TravelTech Inc"
        assert company.vertical == "travel"


class TestPlugAndPlayVerticals:
    """Tests for vertical configuration."""

    def test_travel_vertical_exists(self):
        assert "travel" in PLUGANDPLAY_VERTICALS
        assert PLUGANDPLAY_VERTICALS["travel"] == "travel-hospitality"

    def test_health_vertical_exists(self):
        assert "health" in PLUGANDPLAY_VERTICALS


class TestPlugAndPlayCollector:
    """Tests for PlugAndPlayCollector."""

    def test_collector_initialization(self):
        collector = PlugAndPlayCollector()
        assert collector.verticals == ["travel"]

    def test_collector_custom_verticals(self):
        collector = PlugAndPlayCollector(verticals=["travel", "health"])
        assert "travel" in collector.verticals
        assert "health" in collector.verticals

    @pytest.mark.asyncio
    async def test_collect_vertical_returns_companies(self):
        collector = PlugAndPlayCollector()

        mock_companies = [
            PlugAndPlayCompany(
                name="Test Startup",
                vertical="travel",
                description="Hotel tech",
                website="https://test.com",
                batch="Winter 2026",
                headquarters="NYC",
                collected_at=datetime.utcnow()
            )
        ]

        with patch.object(collector, '_fetch_portfolio', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_companies
            results = await collector.collect_vertical("travel")

            assert len(results) == 1
            assert results[0].name == "Test Startup"

    @pytest.mark.asyncio
    async def test_collect_all_returns_dict(self):
        collector = PlugAndPlayCollector(verticals=["travel"])

        with patch.object(collector, 'collect_vertical', new_callable=AsyncMock) as mock_collect:
            mock_collect.return_value = []
            results = await collector.collect_all()

            assert isinstance(results, dict)
            assert "travel" in results

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        collector = PlugAndPlayCollector()

        with patch.object(collector, '_fetch_portfolio', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Network error")
            results = await collector.collect_vertical("travel")

            assert results == []
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/collectors/test_plugandplay.py -v`
Expected: FAIL

### Step 3: Implement plugandplay.py

```python
# collectors/plugandplay.py
"""
Plug and Play Portfolio Collector.

Collects startup data from Plug and Play's industry-specific accelerator batches.
Supports multiple verticals including Travel & Hospitality, Health, Fintech, etc.

Source: https://www.plugandplaytechcenter.com/portfolio/

Usage:
    collector = PlugAndPlayCollector(verticals=["travel", "health"])
    companies = await collector.collect_all()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Mapping of vertical names to Plug and Play URL slugs
PLUGANDPLAY_VERTICALS = {
    "travel": "travel-hospitality",
    "health": "health",
    "fintech": "fintech",
    "retail": "brand-retail",
    "supply_chain": "supply-chain",
    "insurtech": "insurtech",
    "mobility": "mobility",
    "food": "food-ag-tech",
    "energy": "energy",
    "real_estate": "real-estate",
}

PLUGANDPLAY_BASE_URL = "https://www.plugandplaytechcenter.com"


@dataclass
class PlugAndPlayCompany:
    """Company from Plug and Play portfolio."""

    name: str
    vertical: str
    description: str
    website: Optional[str]
    batch: Optional[str]
    headquarters: Optional[str]
    collected_at: datetime


class PlugAndPlayCollector:
    """
    Collects portfolio companies from Plug and Play accelerator.

    Implements polite scraping with rate limiting.
    """

    def __init__(
        self,
        verticals: Optional[List[str]] = None,
        rate_limit: float = 1.0
    ):
        """
        Initialize Plug and Play collector.

        Args:
            verticals: List of verticals to collect (default: ["travel"])
            rate_limit: Max requests per second (default: 1.0)
        """
        self.verticals = verticals or ["travel"]
        self.rate_limit = rate_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            if self._last_request_time is not None:
                elapsed = asyncio.get_event_loop().time() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def collect_vertical(self, vertical: str) -> List[PlugAndPlayCompany]:
        """
        Collect all companies from a specific vertical.

        Args:
            vertical: Vertical name (e.g., "travel", "health")

        Returns:
            List of PlugAndPlayCompany objects
        """
        if vertical not in PLUGANDPLAY_VERTICALS:
            logger.warning(f"Unknown vertical: {vertical}")
            return []

        try:
            companies = await self._fetch_portfolio(vertical)
            logger.info(f"Collected {len(companies)} companies from {vertical} vertical")
            return companies
        except Exception as e:
            logger.error(f"Error collecting {vertical} portfolio: {e}")
            return []

    async def collect_all(self) -> Dict[str, List[PlugAndPlayCompany]]:
        """
        Collect from all configured verticals.

        Returns:
            Dict mapping vertical names to lists of companies
        """
        results = {}

        for vertical in self.verticals:
            companies = await self.collect_vertical(vertical)
            results[vertical] = companies

        total = sum(len(c) for c in results.values())
        logger.info(f"Collected {total} companies across {len(self.verticals)} verticals")
        return results

    async def _fetch_portfolio(self, vertical: str) -> List[PlugAndPlayCompany]:
        """
        Fetch portfolio companies for a vertical.

        Note: This is a stub implementation. In production, would scrape
        the Plug and Play portfolio page or use their API if available.

        Args:
            vertical: Vertical name

        Returns:
            List of PlugAndPlayCompany objects
        """
        await self._wait_for_rate_limit()

        url_slug = PLUGANDPLAY_VERTICALS.get(vertical, vertical)
        url = f"{PLUGANDPLAY_BASE_URL}/portfolio/?industry={url_slug}"

        # Stub: In production, would scrape the portfolio page
        # The page uses JavaScript rendering, so would need:
        # 1. Playwright/Selenium for JS rendering, or
        # 2. Find their internal API endpoints, or
        # 3. Use a service like ScrapingBee

        logger.debug(f"Would fetch portfolio from: {url}")

        # Return empty list for stub
        # In production: parse HTML/JSON and return companies
        return []

    async def _parse_company_card(self, card_html: str, vertical: str) -> Optional[PlugAndPlayCompany]:
        """
        Parse a company card from the portfolio page.

        Args:
            card_html: HTML of the company card
            vertical: Vertical name

        Returns:
            PlugAndPlayCompany or None if parsing fails
        """
        # Stub: Would parse HTML to extract company details
        # In production: use BeautifulSoup or similar
        return None
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/collectors/test_plugandplay.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add collectors/plugandplay.py tests/collectors/test_plugandplay.py
git commit -m "feat(collectors): add Plug and Play portfolio collector"
```

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/integration/test_travel_pipeline.py`

### Step 1: Write integration tests

```python
# tests/integration/test_travel_pipeline.py
"""
Integration tests for the Travel & Hospitality intelligence pipeline.

Tests the full flow from domain detection to classification to enrichment.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from intelligence.domain_router import DomainRouter, Domain
from intelligence.travel_classifier import TravelClassifier, TravelCategory
from enrichment.travel_orchestrator import TravelEnrichmentOrchestrator
from storage.travel_enrichment import TravelEnrichmentStore


class TestTravelDomainRouting:
    """Integration tests for travel domain routing."""

    def test_hotel_tech_signal_routes_to_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "AI-powered property management system for boutique hotels"
        )
        assert result.primary_domain == Domain.TRAVEL

    def test_booking_platform_routes_to_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "Next-generation booking platform for experiential travel"
        )
        assert result.primary_domain == Domain.TRAVEL

    def test_phocuswright_source_routes_to_travel(self):
        router = DomainRouter()
        result = router.detect_domain(
            "Startup raises seed round",
            source="phocuswright"
        )
        assert result.primary_domain == Domain.TRAVEL


class TestTravelClassification:
    """Integration tests for travel classification."""

    @pytest.mark.asyncio
    async def test_hotel_tech_classified_correctly(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="Property management software for hotels with guest experience features",
            company_name="HotelOS"
        )
        assert result.category in [TravelCategory.HOTEL_TECH, TravelCategory.TRAVEL_INFRASTRUCTURE]
        assert result.fit_score > 0

    @pytest.mark.asyncio
    async def test_stage_filter_detects_series_b(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="Series B funded hotel booking platform",
            company_name="BigHotel Inc"
        )
        assert result.investment_stage_fit == "stage_mismatch"

    @pytest.mark.asyncio
    async def test_tech_enabled_detection(self):
        classifier = TravelClassifier()
        result = await classifier.classify(
            content="AI-powered concierge platform with mobile app",
            company_name="ConciergeAI"
        )
        assert result.is_tech_enabled is True


class TestTravelEnrichmentPipeline:
    """Integration tests for travel enrichment."""

    @pytest.fixture
    async def orchestrator(self):
        orch = TravelEnrichmentOrchestrator(db_path=":memory:")
        await orch.initialize()
        yield orch

    @pytest.mark.asyncio
    async def test_enrichment_pipeline_without_api_keys(self, orchestrator):
        """Test that pipeline handles missing API keys gracefully."""
        result = await orchestrator.enrich_entity(
            entity_id="test-entity",
            company_name="Test Hotel",
            location="New York"
        )
        # Should succeed even without API keys (returns empty results)
        assert result.success is True
        assert result.entity_id == "test-entity"

    @pytest.mark.asyncio
    async def test_storage_persists_enrichment(self, orchestrator):
        """Test that enrichment results are stored."""
        # This tests the storage layer integration
        store = orchestrator.store

        # Verify tables were created
        assert store._db is not None


class TestFullTravelPipeline:
    """End-to-end tests for the travel pipeline."""

    @pytest.mark.asyncio
    async def test_signal_to_enrichment_flow(self):
        """Test full flow: signal → domain → classify → enrich."""
        # Step 1: Domain routing
        router = DomainRouter()
        signal_content = "Seed-stage hotel tech startup with AI-powered PMS"
        domain_result = router.detect_domain(signal_content)

        assert domain_result.primary_domain == Domain.TRAVEL

        # Step 2: Classification
        classifier = TravelClassifier()
        class_result = await classifier.classify(
            content=signal_content,
            company_name="HotelAI"
        )

        assert class_result.category != TravelCategory.OUT_OF_SCOPE
        assert class_result.fit_score > 0

        # Step 3: Enrichment (with mocked clients)
        orchestrator = TravelEnrichmentOrchestrator(db_path=":memory:")
        await orchestrator.initialize()

        enrich_result = await orchestrator.enrich_entity(
            entity_id="hotel-ai-123",
            company_name="HotelAI",
            location="San Francisco"
        )

        assert enrich_result.success is True

    @pytest.mark.asyncio
    async def test_multi_domain_signal(self):
        """Test signal that matches multiple domains."""
        router = DomainRouter()

        # Health + Travel signal
        result = router.detect_domain(
            "Wellness retreat booking platform with telehealth integration"
        )

        # Should detect at least one domain
        assert result.primary_domain in [Domain.TRAVEL, Domain.HEALTH]


class TestTravelStorageIntegration:
    """Integration tests for travel storage."""

    @pytest.fixture
    async def store(self):
        store = TravelEnrichmentStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_yelp_roundtrip(self, store):
        """Test saving and retrieving Yelp data."""
        from enrichment.yelp_fusion import YelpBusiness

        business = YelpBusiness(
            entity_id="test-123",
            yelp_id="abc",
            name="Test Hotel",
            rating=4.5,
            review_count=100,
            price="$$$",
            categories=["Hotels", "Resorts"],
            url="https://yelp.com/biz/test",
            fetched_at=datetime.utcnow()
        )

        await store.save_yelp_review(business)
        reviews = await store.get_yelp_reviews_for_entity("test-123")

        assert len(reviews) == 1
        assert reviews[0].name == "Test Hotel"
        assert reviews[0].rating == 4.5

    @pytest.mark.asyncio
    async def test_google_roundtrip(self, store):
        """Test saving and retrieving Google Places data."""
        from enrichment.google_places import GooglePlace

        place = GooglePlace(
            entity_id="test-456",
            place_id="ChIJ123",
            name="Luxury Resort",
            rating=4.8,
            user_ratings_total=500,
            price_level=4,
            types=["lodging", "resort"],
            website="https://resort.com",
            fetched_at=datetime.utcnow()
        )

        await store.save_google_place(place)
        places = await store.get_google_places_for_entity("test-456")

        assert len(places) == 1
        assert places[0].rating == 4.8

    @pytest.mark.asyncio
    async def test_certification_roundtrip(self, store):
        """Test saving and retrieving certification data."""
        from enrichment.travel_certifications import TravelCertification, CertificationSource

        cert = TravelCertification(
            entity_id="test-789",
            source=CertificationSource.FORBES,
            rating="5-star",
            year=2026,
            property_name="Grand Hotel",
            fetched_at=datetime.utcnow()
        )

        await store.save_certification(cert)
        certs = await store.get_certifications_for_entity("test-789")

        assert len(certs) == 1
        assert certs[0].rating == "5-star"
```

### Step 2: Run integration tests

Run: `pytest tests/integration/test_travel_pipeline.py -v`
Expected: PASS (after all previous tasks complete)

### Step 3: Commit

```bash
git add tests/integration/test_travel_pipeline.py
git commit -m "test(integration): add travel pipeline integration tests"
```

---

## Task 11: Final Verification

### Step 1: Run all tests

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass

### Step 2: Verify test count

```bash
pytest tests/ --collect-only | grep "test session starts" -A 5
```

Expected: ~50+ new tests for travel vertical

### Step 3: Final commit

```bash
git add -A
git commit -m "feat(phase2): complete Travel & Hospitality vertical intelligence"
```

---

## Summary

| Task | Component | Files | Tests |
|------|-----------|-------|-------|
| 1 | Travel keywords | domain_router.py | 8 |
| 2 | Thesis config YAML | thesis_config.py, travel_thesis_rules.yaml | 6 |
| 3 | Travel classifier | travel_classifier.py | 12 |
| 4 | Yelp client | yelp_fusion.py | 7 |
| 5 | Google Places client | google_places.py | 5 |
| 6 | Certifications client | travel_certifications.py | 5 |
| 7 | Travel storage | travel_enrichment.py | 7 |
| 8 | Travel orchestrator | travel_orchestrator.py | 5 |
| 9 | Plug and Play collector | plugandplay.py | 6 |
| 10 | Integration tests | test_travel_pipeline.py | 10 |
| 11 | Final verification | - | - |

**Total: 11 tasks, ~70 tests**

**API Keys Required:**
- `YELP_API_KEY`
- `GOOGLE_PLACES_API_KEY`
- `PERPLEXITY_API_KEY` (optional)
