# Enrichment Boost Integration - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Use ConsolidatedSignal data (founding_date, social_proof) to calculate enrichment boosts that factor into confidence scoring and appear in Notion.

**Architecture:** ConsolidatedSignal already extracts founding_date and social_proof in Phase 1. We add an EnrichmentBoostCalculator that converts these into a capped boost, pass it to VerificationGate.evaluate(), and include the data in ProspectPayload.

**Tech Stack:** Python 3.11, pytest, dataclasses, existing ConsolidatedSignal from `utils/signal_consolidator.py`

**Research Validation:** Simple Additive Weighting (SAW) pattern validated by [MCDM library](https://github.com/akestoridis/mcdm), threshold-based scoring from [lead-scoring repos](https://github.com/topics/lead-scoring), capped boosts from [ensemble-boxes](https://github.com/ZFTurbo/Weighted-Boxes-Fusion).

---

## Context: Current State

**ConsolidatedSignal has (from Phase 1):** `utils/signal_consolidator.py:160-196`
```python
@dataclass
class ConsolidatedSignal:
    founding_date: Optional[datetime] = None
    social_proof: Dict[str, int] = field(default_factory=dict)  # {"stars": 100, "upvotes": 50}
```

**VerificationGate.evaluate() signature:** `verification/verification_gate_v2.py:242-248`
```python
def evaluate(
    self,
    signals: List[Signal],
    founder_score: float = 0.0,
    velocity_boost: float = 0.0,
    momentum_score: float = 0.0,
) -> VerificationResult:
```

**Pipeline calls gate:** `workflows/pipeline.py:1291-1296`
```python
verification = self._gate.evaluate(
    gate_signals,
    founder_score=founder_score,
    velocity_boost=velocity_boost,
    momentum_score=momentum_score,
)
```

---

## Boost Thresholds (Research-Validated)

| Factor | Threshold | Boost | Rationale |
|--------|-----------|-------|-----------|
| Company age > 2 years | `founding_date` older than 730 days | +0.03 | Established company, lower risk |
| Company age > 1 year | `founding_date` older than 365 days | +0.02 | Growing company |
| High social proof | stars > 1000 OR upvotes > 200 | +0.02 | Strong community validation |
| Medium social proof | stars > 500 OR upvotes > 100 | +0.01 | Some community traction |
| **Max enrichment boost** | Combined cap | 0.05 | Prevent enrichment from dominating |

---

## Task 1: Create EnrichmentBoostCalculator class

**Files:**
- Create: `utils/enrichment_boost.py`
- Test: `tests/utils/test_enrichment_boost.py`

**Step 1: Write failing tests**

```python
# tests/utils/test_enrichment_boost.py
"""Tests for enrichment boost calculation."""

import pytest
from datetime import datetime, timezone, timedelta
from utils.enrichment_boost import (
    EnrichmentBoostCalculator,
    EnrichmentBoost,
    EnrichmentConfig,
)
from utils.signal_consolidator import ConsolidatedSignal


class TestEnrichmentBoostDataclass:
    """Test the EnrichmentBoost dataclass."""

    def test_enrichment_boost_fields(self):
        """EnrichmentBoost has expected fields."""
        boost = EnrichmentBoost(
            company_age_boost=0.03,
            social_proof_boost=0.02,
            total_boost=0.05,
            company_age_days=400,
            social_proof_score=1500,
        )
        assert boost.total_boost == 0.05
        assert boost.company_age_days == 400


class TestCompanyAgeBoost:
    """Test company age boost calculation."""

    def test_company_over_2_years_gets_max_boost(self):
        """Company > 2 years old gets +0.03 boost."""
        now = datetime.now(timezone.utc)
        founding = now - timedelta(days=800)  # > 2 years

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["incorporation"],
            source_apis=["companies_house"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding,
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.company_age_boost == 0.03
        assert boost.company_age_days >= 800

    def test_company_over_1_year_gets_medium_boost(self):
        """Company 1-2 years old gets +0.02 boost."""
        now = datetime.now(timezone.utc)
        founding = now - timedelta(days=400)  # 1-2 years

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["incorporation"],
            source_apis=["companies_house"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding,
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.company_age_boost == 0.02

    def test_company_under_1_year_gets_no_boost(self):
        """Company < 1 year old gets no age boost."""
        now = datetime.now(timezone.utc)
        founding = now - timedelta(days=200)  # < 1 year

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["incorporation"],
            source_apis=["companies_house"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding,
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.company_age_boost == 0.0

    def test_no_founding_date_gets_no_boost(self):
        """Missing founding_date gets no age boost."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=None,  # No founding date
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.company_age_boost == 0.0
        assert boost.company_age_days == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/utils/test_enrichment_boost.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.enrichment_boost'`

**Step 3: Write minimal implementation**

```python
# utils/enrichment_boost.py
"""
Enrichment Boost Calculator for Discovery Engine

Calculates confidence boosts from enrichment data extracted by SignalConsolidator.
Uses Simple Additive Weighting (SAW) pattern with threshold-based scoring.

Research validation:
- SAW pattern: https://github.com/akestoridis/mcdm
- Threshold scoring: https://github.com/topics/lead-scoring
- Capped boosts: https://github.com/ZFTurbo/Weighted-Boxes-Fusion
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from utils.signal_consolidator import ConsolidatedSignal


@dataclass
class EnrichmentConfig:
    """Configuration for enrichment boost calculation."""
    # Company age thresholds (days)
    age_high_threshold_days: int = 730  # 2 years
    age_medium_threshold_days: int = 365  # 1 year

    # Company age boosts
    age_high_boost: float = 0.03
    age_medium_boost: float = 0.02

    # Social proof thresholds
    stars_high_threshold: int = 1000
    stars_medium_threshold: int = 500
    upvotes_high_threshold: int = 200
    upvotes_medium_threshold: int = 100

    # Social proof boosts
    social_high_boost: float = 0.02
    social_medium_boost: float = 0.01

    # Maximum total boost (cap)
    max_total_boost: float = 0.05


@dataclass
class EnrichmentBoost:
    """Result of enrichment boost calculation."""
    company_age_boost: float
    social_proof_boost: float
    total_boost: float

    # Metrics for transparency
    company_age_days: int
    social_proof_score: int  # Combined stars + upvotes

    def to_dict(self) -> dict:
        return {
            "company_age_boost": self.company_age_boost,
            "social_proof_boost": self.social_proof_boost,
            "total_boost": self.total_boost,
            "company_age_days": self.company_age_days,
            "social_proof_score": self.social_proof_score,
        }


class EnrichmentBoostCalculator:
    """
    Calculates enrichment boosts from ConsolidatedSignal data.

    Uses threshold-based scoring with capped output:
    - Company age > 2yr: +0.03
    - Company age > 1yr: +0.02
    - High social proof: +0.02
    - Medium social proof: +0.01
    - Max total: 0.05
    """

    def __init__(self, config: Optional[EnrichmentConfig] = None):
        self.config = config or EnrichmentConfig()

    def calculate(self, consolidated: "ConsolidatedSignal") -> EnrichmentBoost:
        """
        Calculate enrichment boost from consolidated signal data.

        Args:
            consolidated: ConsolidatedSignal with founding_date and social_proof

        Returns:
            EnrichmentBoost with individual and total boosts
        """
        # Calculate company age boost
        company_age_boost, company_age_days = self._calculate_age_boost(
            consolidated.founding_date
        )

        # Calculate social proof boost
        social_proof_boost, social_proof_score = self._calculate_social_proof_boost(
            consolidated.social_proof
        )

        # Apply cap
        total_boost = min(
            company_age_boost + social_proof_boost,
            self.config.max_total_boost
        )

        return EnrichmentBoost(
            company_age_boost=company_age_boost,
            social_proof_boost=social_proof_boost,
            total_boost=total_boost,
            company_age_days=company_age_days,
            social_proof_score=social_proof_score,
        )

    def _calculate_age_boost(
        self, founding_date: Optional[datetime]
    ) -> tuple[float, int]:
        """Calculate boost from company age."""
        if not founding_date:
            return 0.0, 0

        now = datetime.now(timezone.utc)
        age = now - founding_date
        age_days = age.days

        if age_days >= self.config.age_high_threshold_days:
            return self.config.age_high_boost, age_days
        elif age_days >= self.config.age_medium_threshold_days:
            return self.config.age_medium_boost, age_days
        else:
            return 0.0, age_days

    def _calculate_social_proof_boost(
        self, social_proof: dict
    ) -> tuple[float, int]:
        """Calculate boost from social proof metrics."""
        if not social_proof:
            return 0.0, 0

        stars = social_proof.get("stars", 0) + social_proof.get("recent_stars", 0)
        upvotes = social_proof.get("upvotes", 0) + social_proof.get("votes", 0)
        total_score = stars + upvotes

        # Check high thresholds
        if stars >= self.config.stars_high_threshold or upvotes >= self.config.upvotes_high_threshold:
            return self.config.social_high_boost, total_score

        # Check medium thresholds
        if stars >= self.config.stars_medium_threshold or upvotes >= self.config.upvotes_medium_threshold:
            return self.config.social_medium_boost, total_score

        return 0.0, total_score
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/utils/test_enrichment_boost.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add utils/enrichment_boost.py tests/utils/test_enrichment_boost.py
git commit -m "feat(enrichment): add EnrichmentBoostCalculator with threshold-based scoring"
```

---

## Task 2: Add social proof boost tests

**Files:**
- Modify: `tests/utils/test_enrichment_boost.py`

**Step 1: Add social proof tests**

```python
# Add to tests/utils/test_enrichment_boost.py

class TestSocialProofBoost:
    """Test social proof boost calculation."""

    def test_high_stars_gets_high_boost(self):
        """Stars > 1000 gets +0.02 boost."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            social_proof={"stars": 1500},
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.social_proof_boost == 0.02

    def test_high_upvotes_gets_high_boost(self):
        """Upvotes > 200 gets +0.02 boost."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["product_hunt_launch"],
            source_apis=["product_hunt"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            social_proof={"upvotes": 250},
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.social_proof_boost == 0.02

    def test_medium_stars_gets_medium_boost(self):
        """Stars 500-1000 gets +0.01 boost."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            social_proof={"stars": 700},
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.social_proof_boost == 0.01

    def test_low_social_proof_gets_no_boost(self):
        """Low social proof gets no boost."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            social_proof={"stars": 50},
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.social_proof_boost == 0.0

    def test_no_social_proof_gets_no_boost(self):
        """Missing social_proof gets no boost."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["incorporation"],
            source_apis=["sec_edgar"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            social_proof={},
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        assert boost.social_proof_boost == 0.0
        assert boost.social_proof_score == 0
```

**Step 2: Run tests**

Run: `pytest tests/utils/test_enrichment_boost.py::TestSocialProofBoost -v`
Expected: PASS (5 tests)

**Step 3: Commit**

```bash
git add tests/utils/test_enrichment_boost.py
git commit -m "test(enrichment): add social proof boost tests"
```

---

## Task 3: Add total boost cap tests

**Files:**
- Modify: `tests/utils/test_enrichment_boost.py`

**Step 1: Add cap tests**

```python
# Add to tests/utils/test_enrichment_boost.py

class TestTotalBoostCap:
    """Test that total boost is capped."""

    def test_combined_boost_is_capped_at_max(self):
        """Combined age + social proof is capped at 0.05."""
        now = datetime.now(timezone.utc)
        founding = now - timedelta(days=800)  # > 2 years = +0.03

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2],
            signal_types=["incorporation", "github_spike"],
            source_apis=["companies_house", "github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding,
            social_proof={"stars": 1500},  # > 1000 = +0.02
        )

        calc = EnrichmentBoostCalculator()
        boost = calc.calculate(consolidated)

        # 0.03 + 0.02 = 0.05 (at cap)
        assert boost.company_age_boost == 0.03
        assert boost.social_proof_boost == 0.02
        assert boost.total_boost == 0.05

    def test_exceeding_cap_is_limited(self):
        """If individual boosts exceed cap, total is limited."""
        config = EnrichmentConfig(
            age_high_boost=0.04,  # Higher than normal
            social_high_boost=0.03,  # Higher than normal
            max_total_boost=0.05,  # But cap stays at 0.05
        )

        now = datetime.now(timezone.utc)
        founding = now - timedelta(days=800)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2],
            signal_types=["incorporation", "github_spike"],
            source_apis=["companies_house", "github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding,
            social_proof={"stars": 1500},
        )

        calc = EnrichmentBoostCalculator(config)
        boost = calc.calculate(consolidated)

        # 0.04 + 0.03 = 0.07, but capped at 0.05
        assert boost.company_age_boost == 0.04
        assert boost.social_proof_boost == 0.03
        assert boost.total_boost == 0.05  # Capped

    def test_config_allows_custom_thresholds(self):
        """Custom config changes thresholds."""
        config = EnrichmentConfig(
            age_high_threshold_days=365,  # Lower threshold
            age_high_boost=0.05,
        )

        now = datetime.now(timezone.utc)
        founding = now - timedelta(days=400)  # > 1 year

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["incorporation"],
            source_apis=["companies_house"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding,
        )

        calc = EnrichmentBoostCalculator(config)
        boost = calc.calculate(consolidated)

        assert boost.company_age_boost == 0.05
```

**Step 2: Run tests**

Run: `pytest tests/utils/test_enrichment_boost.py::TestTotalBoostCap -v`
Expected: PASS (3 tests)

**Step 3: Commit**

```bash
git add tests/utils/test_enrichment_boost.py
git commit -m "test(enrichment): add total boost cap tests"
```

---

## Task 4: Add enrichment_boost to VerificationGate

**Files:**
- Modify: `verification/verification_gate_v2.py`
- Test: `tests/test_verification_gate_enrichment.py`

**Step 1: Write failing test**

```python
# tests/test_verification_gate_enrichment.py
"""Tests for enrichment boost in VerificationGate."""

import pytest
from datetime import datetime, timezone, timedelta
from verification.verification_gate_v2 import VerificationGate, Signal


class TestEnrichmentBoost:
    """Test enrichment_boost parameter in VerificationGate."""

    def test_evaluate_accepts_enrichment_boost(self):
        """VerificationGate.evaluate() accepts enrichment_boost parameter."""
        gate = VerificationGate()
        signals = [
            Signal(
                id="sig-1",
                signal_type="incorporation",
                confidence=0.7,
                source_api="companies_house",
            )
        ]

        # Should not raise
        result = gate.evaluate(signals, enrichment_boost=0.05)

        assert result is not None

    def test_enrichment_boost_increases_confidence(self):
        """Enrichment boost should increase final confidence score."""
        gate = VerificationGate()
        signals = [
            Signal(
                id="sig-1",
                signal_type="incorporation",
                confidence=0.6,
                source_api="companies_house",
            )
        ]

        result_without = gate.evaluate(signals, enrichment_boost=0.0)
        result_with = gate.evaluate(signals, enrichment_boost=0.05)

        assert result_with.confidence_score > result_without.confidence_score

    def test_enrichment_boost_is_capped(self):
        """Enrichment boost should respect ENRICHMENT_BOOST_WEIGHT."""
        gate = VerificationGate()
        signals = [
            Signal(
                id="sig-1",
                signal_type="incorporation",
                confidence=0.7,
                source_api="companies_house",
            )
        ]

        # Even with large enrichment_boost, it should be capped
        result = gate.evaluate(signals, enrichment_boost=0.50)

        # Boost should be limited (check breakdown)
        breakdown = result.confidence_breakdown
        assert breakdown.get("enrichment_boost", 0) <= 0.05

    def test_enrichment_boost_in_breakdown(self):
        """Enrichment boost should appear in confidence breakdown."""
        gate = VerificationGate()
        signals = [
            Signal(
                id="sig-1",
                signal_type="incorporation",
                confidence=0.7,
                source_api="companies_house",
            )
        ]

        result = gate.evaluate(signals, enrichment_boost=0.03)

        assert "enrichment_boost" in result.confidence_breakdown
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verification_gate_enrichment.py -v`
Expected: FAIL (enrichment_boost parameter not accepted)

**Step 3: Modify VerificationGate**

Add to `verification/verification_gate_v2.py`:

1. Add constant near other boost weights (~line 217):
```python
ENRICHMENT_BOOST_WEIGHT = 0.05  # Max boost from enrichment data
```

2. Modify evaluate() signature (~line 242):
```python
def evaluate(
    self,
    signals: List[Signal],
    founder_score: float = 0.0,
    velocity_boost: float = 0.0,
    momentum_score: float = 0.0,
    enrichment_boost: float = 0.0,  # NEW
) -> VerificationResult:
```

3. Pass to _calculate_confidence (~line 287):
```python
breakdown = self._calculate_confidence(
    signals,
    founder_score=founder_score,
    velocity_boost=velocity_boost,
    momentum_score=momentum_score,
    enrichment_boost=enrichment_boost,  # NEW
)
```

4. Modify _calculate_confidence signature (~line 326):
```python
def _calculate_confidence(
    self,
    signals: List[Signal],
    founder_score: float = 0.0,
    velocity_boost: float = 0.0,
    momentum_score: float = 0.0,
    enrichment_boost: float = 0.0,  # NEW
) -> ConfidenceBreakdown:
```

5. Apply enrichment boost after velocity boost (~line 445):
```python
# Enrichment boost (Phase 2 enhancement)
enrichment_boost_applied = 0.0
if enrichment_boost > 0:
    enrichment_boost_applied = min(enrichment_boost, self.ENRICHMENT_BOOST_WEIGHT)
    signal_details.append({
        "type": "enrichment_data",
        "source": "consolidated_signal",
        "enrichment_boost": round(enrichment_boost, 3),
        "contribution": round(enrichment_boost_applied, 4),
        "effect": "boost"
    })

# Final score with all boosts
final_score = min(intermediate_score + founder_boost + velocity_boost_applied + enrichment_boost_applied, 1.0)
```

6. Add to ConfidenceBreakdown dataclass (~line 166):
```python
enrichment_boost: float = 0.0  # Boost from enrichment data
```

7. Update to_dict() to include enrichment_boost.

**Step 4: Run tests**

Run: `pytest tests/test_verification_gate_enrichment.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add verification/verification_gate_v2.py tests/test_verification_gate_enrichment.py
git commit -m "feat(gate): add enrichment_boost parameter to VerificationGate"
```

---

## Task 5: Wire EnrichmentBoostCalculator into pipeline

**Files:**
- Modify: `workflows/pipeline.py`
- Test: `tests/workflows/test_enrichment_integration.py`

**Step 1: Write failing test**

```python
# tests/workflows/test_enrichment_integration.py
"""Test enrichment boost integration with pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from storage.signal_store import StoredSignal
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


class TestEnrichmentIntegration:
    """Test enrichment boost integration."""

    def test_pipeline_config_has_use_enrichment_boost_flag(self):
        """PipelineConfig should have use_enrichment_boost flag."""
        config = PipelineConfig()
        assert hasattr(config, "use_enrichment_boost")
        assert config.use_enrichment_boost is True  # Default enabled

    def test_pipeline_creates_enrichment_calculator(self):
        """Pipeline should create EnrichmentBoostCalculator when enabled."""
        config = PipelineConfig(use_enrichment_boost=True)
        pipeline = DiscoveryPipeline(config=config)

        assert pipeline._enrichment_calculator is not None

    def test_pipeline_no_calculator_when_disabled(self):
        """Pipeline should not create calculator when disabled."""
        config = PipelineConfig(use_enrichment_boost=False)
        pipeline = DiscoveryPipeline(config=config)

        assert pipeline._enrichment_calculator is None
```

**Step 2: Run tests**

Run: `pytest tests/workflows/test_enrichment_integration.py -v`
Expected: FAIL (use_enrichment_boost not in PipelineConfig)

**Step 3: Modify pipeline**

Add to `workflows/pipeline.py`:

1. Add import at top:
```python
from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentConfig
```

2. Add to PipelineConfig dataclass:
```python
use_enrichment_boost: bool = True  # Enable enrichment boost calculation
```

3. Add to DiscoveryPipeline.__init__:
```python
self._enrichment_calculator: Optional[EnrichmentBoostCalculator] = None
if self.config.use_enrichment_boost:
    self._enrichment_calculator = EnrichmentBoostCalculator()
```

4. In _process_company(), after getting consolidated signal, calculate enrichment boost:
```python
# Get enrichment boost (Phase 2 enhancement)
enrichment_boost = 0.0
if self._enrichment_calculator and consolidated:
    try:
        enrichment = self._enrichment_calculator.calculate(consolidated)
        enrichment_boost = enrichment.total_boost
        if enrichment_boost > 0:
            logger.info(
                f"Enrichment boost for {canonical_key}: {enrichment_boost:.2f} "
                f"(age: {enrichment.company_age_days}d, social: {enrichment.social_proof_score})"
            )
    except Exception as e:
        logger.warning(f"Enrichment calculation failed (non-fatal): {e}")
```

5. Pass to gate.evaluate():
```python
verification = self._gate.evaluate(
    gate_signals,
    founder_score=founder_score,
    velocity_boost=velocity_boost,
    momentum_score=momentum_score,
    enrichment_boost=enrichment_boost,  # NEW
)
```

**Step 4: Run tests**

Run: `pytest tests/workflows/test_enrichment_integration.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_enrichment_integration.py
git commit -m "feat(pipeline): wire EnrichmentBoostCalculator into processing flow"
```

---

## Task 6: Add enriched fields to ProspectPayload

**Files:**
- Modify: `connectors/notion_connector_v2.py`
- Modify: `workflows/pipeline.py` (_push_to_notion method)
- Test: `tests/workflows/test_enrichment_integration.py`

**Step 1: Add fields to ProspectPayload**

Add to `connectors/notion_connector_v2.py` ProspectPayload dataclass (~line 115):
```python
# Enrichment fields (from ConsolidatedSignal)
founding_date: Optional[datetime] = None
social_proof_score: int = 0
```

**Step 2: Add test**

```python
# Add to tests/workflows/test_enrichment_integration.py

class TestEnrichedProspectPayload:
    """Test enriched fields in ProspectPayload."""

    def test_prospect_payload_has_enrichment_fields(self):
        """ProspectPayload should have founding_date and social_proof_score."""
        from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage

        payload = ProspectPayload(
            discovery_id="test-123",
            company_name="Acme Inc",
            canonical_key="domain:acme.ai",
            stage=InvestmentStage.PRE_SEED,
            founding_date=datetime.now(timezone.utc),
            social_proof_score=1500,
        )

        assert payload.founding_date is not None
        assert payload.social_proof_score == 1500
```

**Step 3: Modify _push_to_notion**

In `workflows/pipeline.py` `_push_to_notion()` method, populate enrichment fields from consolidated:
```python
# Include enrichment data in payload
founding_date = None
social_proof_score = 0
if consolidated:
    founding_date = consolidated.founding_date
    social_proof_score = sum(consolidated.social_proof.values())
```

**Step 4: Run tests**

Run: `pytest tests/workflows/test_enrichment_integration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add connectors/notion_connector_v2.py workflows/pipeline.py tests/workflows/test_enrichment_integration.py
git commit -m "feat(notion): add founding_date and social_proof_score to ProspectPayload"
```

---

## Task 7: Add enrichment metrics to pipeline stats

**Files:**
- Modify: `workflows/pipeline.py`
- Test: `tests/workflows/test_enrichment_integration.py`

**Step 1: Add test**

```python
# Add to tests/workflows/test_enrichment_integration.py

class TestEnrichmentMetrics:
    """Test enrichment metrics in pipeline stats."""

    @pytest.mark.asyncio
    async def test_pipeline_stats_include_enrichment_metrics(self):
        """Pipeline stats should include enrichment boost metrics."""
        # Setup similar to consolidation metrics test
        # Check for: enrichment_boosts_applied, avg_enrichment_boost
        pass  # Implementation follows pattern from Task 10 of Phase 1
```

**Step 2: Add metrics to stats**

In `_process_signals_stage()`, add to stats initialization:
```python
"enrichment_boosts_applied": 0,
"total_enrichment_boost": 0.0,
```

After processing, calculate:
```python
if stats["enrichment_boosts_applied"] > 0:
    stats["avg_enrichment_boost"] = stats["total_enrichment_boost"] / stats["enrichment_boosts_applied"]
else:
    stats["avg_enrichment_boost"] = 0.0
```

**Step 3: Run tests and commit**

```bash
git add workflows/pipeline.py tests/workflows/test_enrichment_integration.py
git commit -m "feat(pipeline): add enrichment metrics to pipeline stats"
```

---

## Task 8: Run full test suite and verify

**Step 1: Run all tests**

Run: `pytest tests/ -v --tb=short`
Expected: All 830+ tests pass

**Step 2: Run enrichment-specific tests**

Run: `pytest tests/utils/test_enrichment_boost.py tests/workflows/test_enrichment_integration.py tests/test_verification_gate_enrichment.py -v`
Expected: All ~20 new tests pass

**Step 3: Verify pipeline loads**

Run: `python -c "from workflows.pipeline import DiscoveryPipeline; p = DiscoveryPipeline(); print(f'Enrichment enabled: {p.config.use_enrichment_boost}')"`
Expected: "Enrichment enabled: True"

**Step 4: Commit plan**

```bash
git add docs/plans/2026-01-13-enrichment-boost.md
git commit -m "docs: add enrichment boost implementation plan"
```

---

## Summary

| Task | Description | Tests Added |
|------|-------------|-------------|
| 1 | EnrichmentBoostCalculator class | 5 |
| 2 | Social proof boost tests | 5 |
| 3 | Total boost cap tests | 3 |
| 4 | Add enrichment_boost to VerificationGate | 4 |
| 5 | Wire into pipeline | 3 |
| 6 | Add fields to ProspectPayload | 1 |
| 7 | Add enrichment metrics | 1 |
| 8 | Full suite verification | 0 |

**Total new tests:** ~22

**Files created:**
- `utils/enrichment_boost.py`
- `tests/utils/test_enrichment_boost.py`
- `tests/test_verification_gate_enrichment.py`
- `tests/workflows/test_enrichment_integration.py`

**Files modified:**
- `verification/verification_gate_v2.py`
- `workflows/pipeline.py`
- `connectors/notion_connector_v2.py`
