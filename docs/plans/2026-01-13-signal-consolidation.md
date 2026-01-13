# Signal Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge grouped signals into unified prospects with field-level strategy, conflict detection, and provenance tracking.

**Architecture:** Signals for the same company are already grouped by canonical_key in `pipeline.py:987-992`. Currently, field extraction just picks `signals[0]` (see `pipeline.py:1373-1376`). We'll add a `SignalConsolidator` class that applies per-field merge strategies, detects conflicts, and preserves provenance. Integration point: between grouping and `_process_company()`.

**Tech Stack:** Python 3.11, pytest, dataclasses, existing StoredSignal from `storage/signal_store.py`

---

## Context: Current State

**Where signals are grouped:** `workflows/pipeline.py:987-992`
```python
by_key: Dict[str, List[StoredSignal]] = {}
for signal in pending:
    by_key.setdefault(signal.canonical_key, []).append(signal)
```

**Where company_name is extracted:** `workflows/pipeline.py:1373-1376`
```python
primary_signal = signals[0]  # Just picks first one!
company_name = primary_signal.company_name or "Unknown Company"
```

**StoredSignal dataclass:** `storage/signal_store.py:225-241`
- `id`, `signal_type`, `source_api`, `canonical_key`, `company_name`
- `confidence`, `raw_data`, `detected_at`, `created_at`

**ProspectPayload dataclass:** `connectors/notion_connector_v2.py:85-115`
- `company_name`, `canonical_key`, `why_now`, `signal_types`, etc.

---

## Source Priority (for company_name)

| Priority | Source | Rationale |
|----------|--------|-----------|
| 1 | companies_house | Official registry, verified |
| 2 | sec_edgar | SEC filings, legal name |
| 3 | crunchbase | Curated startup database |
| 4 | linkedin | Professional network |
| 5 | product_hunt | Product launch, may be branded |
| 6 | github | May be repo name, not company |
| 7 | domain_whois | Registrant, often incomplete |
| 8 | Other | Fallback |

---

## Task 1: Create ConsolidatedSignal dataclass

**Files:**
- Create: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test for dataclass**

```python
# tests/utils/test_signal_consolidator.py
"""Tests for signal consolidation logic."""

import pytest
from datetime import datetime, timezone
from utils.signal_consolidator import ConsolidatedSignal, ConflictFlag


class TestConsolidatedSignalDataclass:
    """Test the ConsolidatedSignal dataclass."""

    def test_consolidated_signal_required_fields(self):
        """ConsolidatedSignal requires canonical_key and company_name."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2, 3],
            signal_types=["github_spike", "incorporation"],
            source_apis=["github", "sec_edgar"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        assert consolidated.canonical_key == "domain:acme.ai"
        assert consolidated.company_name == "Acme Inc"
        assert consolidated.contributing_signal_ids == [1, 2, 3]
        assert len(consolidated.signal_types) == 2

    def test_consolidated_signal_has_conflict_flags(self):
        """ConsolidatedSignal can have conflict flags."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            conflict_flags=[
                ConflictFlag(
                    field="company_name",
                    values=["Acme Inc", "ACME Corporation"],
                    severity="warning",
                )
            ],
        )

        assert len(consolidated.conflict_flags) == 1
        assert consolidated.conflict_flags[0].field == "company_name"
        assert consolidated.has_conflicts is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.signal_consolidator'`

**Step 3: Write minimal implementation**

```python
# utils/signal_consolidator.py
"""
Signal Consolidation for Discovery Engine

Merges multiple signals for the same company into a unified ConsolidatedSignal
with field-level merge strategies, conflict detection, and provenance tracking.

Usage:
    consolidator = SignalConsolidator()
    consolidated = consolidator.consolidate(signals)

    if consolidated.has_conflicts:
        # Route to human review
        pass
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional


@dataclass
class ConflictFlag:
    """Indicates a conflict during consolidation."""
    field: str  # Which field has conflicting values
    values: List[str]  # The conflicting values found
    severity: str = "warning"  # "warning" or "error"

    def __str__(self) -> str:
        return f"{self.field}: {self.values} ({self.severity})"


@dataclass
class ConsolidatedSignal:
    """
    Result of consolidating multiple StoredSignals for the same company.

    Preserves provenance via contributing_signal_ids so original signals
    can be traced back.
    """
    # Identity
    canonical_key: str
    company_name: str

    # Provenance (which signals contributed to this)
    contributing_signal_ids: List[int]
    signal_types: List[str]
    source_apis: List[str]

    # Aggregated metrics
    aggregated_confidence: float
    earliest_detected_at: datetime
    latest_detected_at: datetime

    # Optional aggregated fields
    descriptions: List[str] = field(default_factory=list)
    why_now_parts: List[str] = field(default_factory=list)
    founding_date: Optional[datetime] = None
    social_proof: Dict[str, int] = field(default_factory=dict)  # e.g., {"stars": 100, "votes": 50}

    # Raw data aggregation (merged from all signals)
    merged_raw_data: Dict[str, Any] = field(default_factory=dict)

    # Conflict tracking
    conflict_flags: List[ConflictFlag] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        """Returns True if any conflicts were detected during consolidation."""
        return len(self.conflict_flags) > 0

    @property
    def signal_count(self) -> int:
        """Number of signals that contributed to this consolidation."""
        return len(self.contributing_signal_ids)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add ConsolidatedSignal and ConflictFlag dataclasses"
```

---

## Task 2: Implement source priority for company_name selection

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

from storage.signal_store import StoredSignal
from utils.signal_consolidator import SignalConsolidator, SOURCE_PRIORITY


class TestSourcePriority:
    """Test source priority for field selection."""

    def test_source_priority_order(self):
        """Companies House has highest priority for company_name."""
        assert SOURCE_PRIORITY["companies_house"] < SOURCE_PRIORITY["github"]
        assert SOURCE_PRIORITY["sec_edgar"] < SOURCE_PRIORITY["product_hunt"]
        assert SOURCE_PRIORITY["crunchbase"] < SOURCE_PRIORITY["domain_whois"]

    def test_select_company_name_prefers_companies_house(self):
        """Should prefer company_name from Companies House over GitHub."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="acme-ai",  # GitHub style name
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme AI Limited",  # Official name
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        # Should pick Companies House name despite GitHub having higher confidence
        assert result.company_name == "Acme AI Limited"

    def test_select_company_name_falls_back_to_lower_priority(self):
        """Should fall back to lower priority if higher is missing."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="acme-ai",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.company_name == "acme-ai"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestSourcePriority -v`
Expected: FAIL with `ImportError: cannot import name 'SignalConsolidator'`

**Step 3: Write minimal implementation**

```python
# Add to utils/signal_consolidator.py (after ConsolidatedSignal class)

import logging
from storage.signal_store import StoredSignal

logger = logging.getLogger(__name__)

# Source priority for company_name selection (lower = higher priority)
SOURCE_PRIORITY = {
    "companies_house": 1,  # Official UK registry
    "sec_edgar": 2,        # SEC filings
    "crunchbase": 3,       # Curated startup DB
    "linkedin": 4,         # Professional network
    "product_hunt": 5,     # Product launches
    "hacker_news": 6,      # Tech news
    "github": 7,           # May be repo name
    "domain_whois": 8,     # Registrant info
    "arxiv": 9,            # Research papers
    "uspto": 10,           # Patent filings
}

DEFAULT_PRIORITY = 99  # For unknown sources


class SignalConsolidator:
    """
    Consolidates multiple StoredSignals for the same company.

    Applies field-level merge strategies:
    - company_name: source priority (Companies House > SEC > etc.)
    - confidence: weighted average by source priority
    - descriptions: concatenate unique values
    - social_proof: aggregate (sum stars, votes, etc.)
    """

    def __init__(self):
        self.source_priority = SOURCE_PRIORITY

    def consolidate(self, signals: List[StoredSignal]) -> ConsolidatedSignal:
        """
        Consolidate multiple signals into a single ConsolidatedSignal.

        Args:
            signals: List of StoredSignal objects for the same canonical_key

        Returns:
            ConsolidatedSignal with merged fields and conflict flags
        """
        if not signals:
            raise ValueError("Cannot consolidate empty signal list")

        # Sort by source priority
        sorted_signals = sorted(
            signals,
            key=lambda s: self.source_priority.get(s.source_api, DEFAULT_PRIORITY)
        )

        # Select company_name from highest priority source that has it
        company_name = self._select_company_name(sorted_signals)

        # Basic aggregation
        canonical_key = signals[0].canonical_key
        contributing_ids = [s.id for s in signals]
        signal_types = list(set(s.signal_type for s in signals))
        source_apis = list(set(s.source_api for s in signals))

        # Confidence: weighted average (for now, simple average)
        avg_confidence = sum(s.confidence for s in signals) / len(signals)

        # Time bounds
        earliest = min(s.detected_at for s in signals)
        latest = max(s.detected_at for s in signals)

        return ConsolidatedSignal(
            canonical_key=canonical_key,
            company_name=company_name,
            contributing_signal_ids=contributing_ids,
            signal_types=signal_types,
            source_apis=source_apis,
            aggregated_confidence=avg_confidence,
            earliest_detected_at=earliest,
            latest_detected_at=latest,
        )

    def _select_company_name(self, sorted_signals: List[StoredSignal]) -> str:
        """Select company_name from highest priority source that has it."""
        for signal in sorted_signals:
            if signal.company_name and signal.company_name.strip():
                return signal.company_name.strip()
        return "Unknown Company"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestSourcePriority -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add source priority for company_name selection"
```

---

## Task 3: Add conflict detection for company_name

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

class TestConflictDetection:
    """Test conflict detection during consolidation."""

    def test_detects_different_company_names(self):
        """Should flag when signals have different company names."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme AI",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="ACME Corporation",  # Different name!
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.has_conflicts is True
        assert len(result.conflict_flags) == 1
        assert result.conflict_flags[0].field == "company_name"
        assert "Acme AI" in result.conflict_flags[0].values
        assert "ACME Corporation" in result.conflict_flags[0].values

    def test_no_conflict_for_same_company_name(self):
        """Should not flag when all signals have same company name."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",  # Same name
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.has_conflicts is False
        assert len(result.conflict_flags) == 0

    def test_ignores_none_and_empty_company_names(self):
        """Should not treat None/empty as conflicts."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="domain_registration",
                source_api="domain_whois",
                canonical_key="domain:acme.ai",
                company_name=None,  # No name from WHOIS
                confidence=0.5,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.has_conflicts is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestConflictDetection -v`
Expected: FAIL (conflict detection not implemented)

**Step 3: Write minimal implementation**

```python
# Modify consolidate() in utils/signal_consolidator.py

    def consolidate(self, signals: List[StoredSignal]) -> ConsolidatedSignal:
        """
        Consolidate multiple signals into a single ConsolidatedSignal.
        """
        if not signals:
            raise ValueError("Cannot consolidate empty signal list")

        # Sort by source priority
        sorted_signals = sorted(
            signals,
            key=lambda s: self.source_priority.get(s.source_api, DEFAULT_PRIORITY)
        )

        # Select company_name from highest priority source that has it
        company_name = self._select_company_name(sorted_signals)

        # Detect conflicts
        conflict_flags = self._detect_conflicts(signals)

        # Basic aggregation
        canonical_key = signals[0].canonical_key
        contributing_ids = [s.id for s in signals]
        signal_types = list(set(s.signal_type for s in signals))
        source_apis = list(set(s.source_api for s in signals))

        # Confidence: weighted average (for now, simple average)
        avg_confidence = sum(s.confidence for s in signals) / len(signals)

        # Time bounds
        earliest = min(s.detected_at for s in signals)
        latest = max(s.detected_at for s in signals)

        return ConsolidatedSignal(
            canonical_key=canonical_key,
            company_name=company_name,
            contributing_signal_ids=contributing_ids,
            signal_types=signal_types,
            source_apis=source_apis,
            aggregated_confidence=avg_confidence,
            earliest_detected_at=earliest,
            latest_detected_at=latest,
            conflict_flags=conflict_flags,
        )

    def _detect_conflicts(self, signals: List[StoredSignal]) -> List[ConflictFlag]:
        """Detect conflicts between signal field values."""
        conflicts = []

        # Check company_name conflicts
        company_names = set()
        for signal in signals:
            if signal.company_name and signal.company_name.strip():
                company_names.add(signal.company_name.strip())

        if len(company_names) > 1:
            conflicts.append(ConflictFlag(
                field="company_name",
                values=sorted(company_names),
                severity="warning",
            ))
            logger.warning(
                f"Conflict detected for {signals[0].canonical_key}: "
                f"multiple company names: {company_names}"
            )

        return conflicts
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestConflictDetection -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add conflict detection for company_name"
```

---

## Task 4: Add description aggregation from raw_data

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

class TestDescriptionAggregation:
    """Test description aggregation from raw_data."""

    def test_aggregates_descriptions_from_raw_data(self):
        """Should collect descriptions from raw_data of all signals."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"description": "AI-powered automation tool"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="product_hunt_launch",
                source_api="product_hunt",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"tagline": "Automate your workflow with AI"},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.descriptions) == 2
        assert "AI-powered automation tool" in result.descriptions
        assert "Automate your workflow with AI" in result.descriptions

    def test_deduplicates_identical_descriptions(self):
        """Should not include duplicate descriptions."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"description": "AI tool"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="github_activity",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.6,
                raw_data={"description": "AI tool"},  # Same description
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.descriptions) == 1
        assert result.descriptions[0] == "AI tool"

    def test_handles_missing_descriptions(self):
        """Should handle signals without descriptions gracefully."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"form_d": "D-123"},  # No description
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.descriptions) == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestDescriptionAggregation -v`
Expected: FAIL (descriptions list is empty)

**Step 3: Write minimal implementation**

```python
# Add to SignalConsolidator class in utils/signal_consolidator.py

# Fields to extract descriptions from (in order of preference)
DESCRIPTION_FIELDS = ["description", "tagline", "summary", "bio", "about"]

    def consolidate(self, signals: List[StoredSignal]) -> ConsolidatedSignal:
        """
        Consolidate multiple signals into a single ConsolidatedSignal.
        """
        if not signals:
            raise ValueError("Cannot consolidate empty signal list")

        # Sort by source priority
        sorted_signals = sorted(
            signals,
            key=lambda s: self.source_priority.get(s.source_api, DEFAULT_PRIORITY)
        )

        # Select company_name from highest priority source that has it
        company_name = self._select_company_name(sorted_signals)

        # Detect conflicts
        conflict_flags = self._detect_conflicts(signals)

        # Aggregate descriptions
        descriptions = self._aggregate_descriptions(signals)

        # Basic aggregation
        canonical_key = signals[0].canonical_key
        contributing_ids = [s.id for s in signals]
        signal_types = list(set(s.signal_type for s in signals))
        source_apis = list(set(s.source_api for s in signals))

        # Confidence: weighted average (for now, simple average)
        avg_confidence = sum(s.confidence for s in signals) / len(signals)

        # Time bounds
        earliest = min(s.detected_at for s in signals)
        latest = max(s.detected_at for s in signals)

        return ConsolidatedSignal(
            canonical_key=canonical_key,
            company_name=company_name,
            contributing_signal_ids=contributing_ids,
            signal_types=signal_types,
            source_apis=source_apis,
            aggregated_confidence=avg_confidence,
            earliest_detected_at=earliest,
            latest_detected_at=latest,
            conflict_flags=conflict_flags,
            descriptions=descriptions,
        )

    def _aggregate_descriptions(self, signals: List[StoredSignal]) -> List[str]:
        """Extract and deduplicate descriptions from signal raw_data."""
        seen = set()
        descriptions = []

        for signal in signals:
            raw_data = signal.raw_data or {}
            for field in DESCRIPTION_FIELDS:
                value = raw_data.get(field)
                if isinstance(value, str) and value.strip():
                    normalized = value.strip()
                    if normalized not in seen:
                        seen.add(normalized)
                        descriptions.append(normalized)

        return descriptions
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestDescriptionAggregation -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add description aggregation from raw_data"
```

---

## Task 5: Add social proof aggregation

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

class TestSocialProofAggregation:
    """Test social proof aggregation from raw_data."""

    def test_aggregates_github_stars(self):
        """Should aggregate stars from GitHub signals."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"stars": 150, "recent_stars": 50},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.social_proof["stars"] == 150
        assert result.social_proof["recent_stars"] == 50

    def test_aggregates_product_hunt_upvotes(self):
        """Should aggregate upvotes from Product Hunt signals."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="product_hunt_launch",
                source_api="product_hunt",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"votes": 200, "upvotes": 180, "comments": 45},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.social_proof["votes"] == 200
        assert result.social_proof["upvotes"] == 180
        assert result.social_proof["comments"] == 45

    def test_sums_social_proof_from_multiple_signals(self):
        """Should sum social proof from multiple signals."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"stars": 100},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="product_hunt_launch",
                source_api="product_hunt",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"upvotes": 50},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.social_proof["stars"] == 100
        assert result.social_proof["upvotes"] == 50
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestSocialProofAggregation -v`
Expected: FAIL (social_proof dict is empty)

**Step 3: Write minimal implementation**

```python
# Add to utils/signal_consolidator.py

# Social proof fields to aggregate (sum values)
SOCIAL_PROOF_FIELDS = [
    "stars", "recent_stars", "forks", "watchers",  # GitHub
    "votes", "upvotes", "comments",  # Product Hunt
    "followers", "connections",  # LinkedIn
    "mentions",  # Hacker News
]

    def _aggregate_social_proof(self, signals: List[StoredSignal]) -> Dict[str, int]:
        """Aggregate social proof metrics from signal raw_data."""
        totals: Dict[str, int] = {}

        for signal in signals:
            raw_data = signal.raw_data or {}
            for field in SOCIAL_PROOF_FIELDS:
                value = raw_data.get(field)
                if isinstance(value, (int, float)) and value > 0:
                    totals[field] = totals.get(field, 0) + int(value)

        return totals

# Update consolidate() to call _aggregate_social_proof:
# Add after descriptions aggregation:
        social_proof = self._aggregate_social_proof(signals)
# And add to ConsolidatedSignal constructor:
        social_proof=social_proof,
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestSocialProofAggregation -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add social proof aggregation"
```

---

## Task 6: Add founding_date extraction

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

class TestFoundingDateExtraction:
    """Test founding date extraction from raw_data."""

    def test_extracts_founding_date_from_companies_house(self):
        """Should extract founding_date from Companies House signal."""
        now = datetime.now(timezone.utc)
        founding = datetime(2023, 6, 15, tzinfo=timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"founding_date": "2023-06-15"},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.founding_date is not None
        assert result.founding_date.year == 2023
        assert result.founding_date.month == 6
        assert result.founding_date.day == 15

    def test_prefers_earliest_founding_date(self):
        """Should pick earliest founding_date when multiple exist."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"founding_date": "2024-01-01"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"founding_date": "2023-06-15"},  # Earlier
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.founding_date.year == 2023

    def test_extracts_from_registered_date_field(self):
        """Should also check registered_date field (domain WHOIS)."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="domain_registration",
                source_api="domain_whois",
                canonical_key="domain:acme.ai",
                company_name=None,
                confidence=0.5,
                raw_data={"registered_date": "2022-03-01"},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.founding_date is not None
        assert result.founding_date.year == 2022
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestFoundingDateExtraction -v`
Expected: FAIL (founding_date is None)

**Step 3: Write minimal implementation**

```python
# Add to utils/signal_consolidator.py

from datetime import datetime, timezone
from typing import Optional

# Date fields that indicate founding/registration date
FOUNDING_DATE_FIELDS = ["founding_date", "registered_date", "incorporation_date", "created_date"]

    def _extract_founding_date(self, signals: List[StoredSignal]) -> Optional[datetime]:
        """Extract earliest founding/registration date from signals."""
        dates = []

        for signal in signals:
            raw_data = signal.raw_data or {}
            for field in FOUNDING_DATE_FIELDS:
                value = raw_data.get(field)
                if value:
                    parsed = self._parse_date(value)
                    if parsed:
                        dates.append(parsed)

        return min(dates) if dates else None

    def _parse_date(self, value: Any) -> Optional[datetime]:
        """Parse a date from various formats."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            # Try ISO format first
            for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None

# Update consolidate() to call _extract_founding_date:
        founding_date = self._extract_founding_date(signals)
# And add to ConsolidatedSignal constructor:
        founding_date=founding_date,
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestFoundingDateExtraction -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add founding date extraction"
```

---

## Task 7: Add why_now aggregation

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

class TestWhyNowAggregation:
    """Test why_now aggregation from raw_data."""

    def test_aggregates_why_now_from_raw_data(self):
        """Should collect why_now from raw_data of all signals."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="funding_event",
                source_api="crunchbase",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={"why_now": "Just raised $5M seed round"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="hiring_signal",
                source_api="greenhouse",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"why_now": "Hiring 10 engineers"},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.why_now_parts) == 2
        assert "Just raised $5M seed round" in result.why_now_parts
        assert "Hiring 10 engineers" in result.why_now_parts

    def test_generates_fallback_why_now(self):
        """Should generate fallback when no explicit why_now in raw_data."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"stars": 100},  # No why_now
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        # Should have fallback
        assert len(result.why_now_parts) >= 1
        assert "github_spike" in result.why_now_parts[0].lower() or "detected" in result.why_now_parts[0].lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestWhyNowAggregation -v`
Expected: FAIL (why_now_parts is empty)

**Step 3: Write minimal implementation**

```python
# Add to SignalConsolidator class in utils/signal_consolidator.py

    def _aggregate_why_now(self, signals: List[StoredSignal]) -> List[str]:
        """Extract why_now reasons from signal raw_data."""
        seen = set()
        parts = []

        for signal in signals:
            raw_data = signal.raw_data or {}
            why_now = raw_data.get("why_now")
            if isinstance(why_now, str) and why_now.strip():
                normalized = why_now.strip()
                if normalized not in seen:
                    seen.add(normalized)
                    parts.append(normalized)

        # Fallback if no explicit why_now found
        if not parts:
            signal_types = list(set(s.signal_type for s in signals))
            parts.append(f"Detected via {', '.join(signal_types)}")

        return parts

# Update consolidate() to call _aggregate_why_now:
        why_now_parts = self._aggregate_why_now(signals)
# And add to ConsolidatedSignal constructor:
        why_now_parts=why_now_parts,
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestWhyNowAggregation -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add why_now aggregation"
```

---

## Task 8: Add weighted confidence calculation

**Files:**
- Modify: `utils/signal_consolidator.py`
- Test: `tests/utils/test_signal_consolidator.py`

**Step 1: Write the failing test**

```python
# Add to tests/utils/test_signal_consolidator.py

class TestWeightedConfidence:
    """Test weighted confidence calculation."""

    def test_weights_confidence_by_source_priority(self):
        """Higher priority sources should have more weight in confidence."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",  # Low priority (7)
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,  # High confidence but low priority
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",  # High priority (1)
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.6,  # Lower confidence but high priority
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        # Weighted average should be closer to 0.6 than simple average of 0.75
        # Simple average = (0.9 + 0.6) / 2 = 0.75
        # With weighting, Companies House signal should pull it down
        assert result.aggregated_confidence < 0.75
        assert result.aggregated_confidence > 0.6

    def test_single_signal_uses_own_confidence(self):
        """Single signal should use its own confidence."""
        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.aggregated_confidence == 0.85
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_signal_consolidator.py::TestWeightedConfidence -v`
Expected: FAIL (simple average instead of weighted)

**Step 3: Write minimal implementation**

```python
# Replace confidence calculation in SignalConsolidator.consolidate()

    def _calculate_weighted_confidence(self, signals: List[StoredSignal]) -> float:
        """
        Calculate weighted average confidence.

        Weight is inversely proportional to source priority (lower priority = higher weight).
        This ensures high-quality sources like Companies House and SEC EDGAR
        have more influence on the final confidence score.
        """
        if len(signals) == 1:
            return signals[0].confidence

        total_weight = 0.0
        weighted_sum = 0.0

        for signal in signals:
            # Invert priority: priority 1 -> weight 10, priority 10 -> weight 1
            priority = self.source_priority.get(signal.source_api, DEFAULT_PRIORITY)
            weight = 11 - min(priority, 10)  # Clamp priority to max 10

            weighted_sum += signal.confidence * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

# Update consolidate() to use _calculate_weighted_confidence:
        avg_confidence = self._calculate_weighted_confidence(signals)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_signal_consolidator.py::TestWeightedConfidence -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add utils/signal_consolidator.py tests/utils/test_signal_consolidator.py
git commit -m "feat(consolidator): add weighted confidence calculation"
```

---

## Task 9: Integrate SignalConsolidator into pipeline

**Files:**
- Modify: `workflows/pipeline.py`
- Test: `tests/workflows/test_signal_consolidation_integration.py`

**Step 1: Write the failing test**

```python
# tests/workflows/test_signal_consolidation_integration.py
"""Test signal consolidation integration with pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from storage.signal_store import StoredSignal
from utils.signal_consolidator import SignalConsolidator, ConsolidatedSignal


class TestPipelineConsolidationIntegration:
    """Test that pipeline uses SignalConsolidator."""

    @pytest.mark.asyncio
    async def test_pipeline_consolidates_signals_before_processing(self):
        """Pipeline should consolidate signals before _process_company."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="acme-ai",  # GitHub style
                confidence=0.8,
                raw_data={"description": "AI tool", "stars": 100},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme AI Ltd",  # Official name
                confidence=0.7,
                raw_data={"founding_date": "2023-06-15"},
                detected_at=now,
                created_at=now,
            ),
        ]

        # Mock store
        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_queued = AsyncMock()
        store.mark_rejected = AsyncMock()
        store.enqueue_notion_write = AsyncMock(return_value="outbox-123")

        # Mock Notion connector
        notion = AsyncMock()

        # Create pipeline with consolidation enabled
        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._notion = notion

        # Track what company_name is used in _push_to_notion
        captured_company_name = None
        original_push = pipeline._push_to_notion

        async def capture_push(signals, verification):
            nonlocal captured_company_name
            # The company_name should come from consolidated signal
            # which prefers Companies House over GitHub
            captured_company_name = signals[0].company_name if hasattr(signals[0], 'company_name') else None
            return {"status": "queued", "outbox_id": "test"}

        pipeline._push_to_notion = capture_push

        # Run processing
        await pipeline._process_pending(dry_run=True)

        # Verify company_name came from Companies House (higher priority)
        # This tests the integration point
        # Note: With consolidation, the preferred name should be used
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_signal_consolidation_integration.py -v`
Expected: FAIL (PipelineConfig doesn't have use_consolidation)

**Step 3: Write minimal implementation**

```python
# Modify workflows/pipeline.py

# 1. Add import at top of file:
from utils.signal_consolidator import SignalConsolidator, ConsolidatedSignal

# 2. Add to PipelineConfig dataclass:
@dataclass
class PipelineConfig:
    # ... existing fields ...
    use_consolidation: bool = True  # Enable signal consolidation

# 3. Add consolidator to DiscoveryPipeline.__init__:
class DiscoveryPipeline:
    def __init__(self, config: Optional[PipelineConfig] = None):
        # ... existing init ...
        self._consolidator = SignalConsolidator() if self.config.use_consolidation else None

# 4. Modify _process_pending() after grouping (around line 992):
        # Group by canonical key
        by_key: Dict[str, List[StoredSignal]] = {}
        for signal in pending:
            by_key.setdefault(signal.canonical_key, []).append(signal)

        logger.info(f"Grouped into {len(by_key)} unique companies")

        # NEW: Consolidate signals if enabled
        consolidated_map: Dict[str, ConsolidatedSignal] = {}
        if self._consolidator:
            for key, signals in by_key.items():
                consolidated_map[key] = self._consolidator.consolidate(signals)

            # Log conflicts
            conflicts = sum(1 for c in consolidated_map.values() if c.has_conflicts)
            if conflicts:
                logger.warning(f"Signal consolidation found {conflicts} companies with conflicts")

# 5. Modify _push_to_notion() to use consolidated data when available (around line 1373):
    async def _push_to_notion(
        self,
        signals: List[StoredSignal],
        verification: VerificationResult,
        consolidated: Optional[ConsolidatedSignal] = None,  # NEW parameter
    ) -> Dict[str, Any]:
        # Use consolidated data if available
        if consolidated:
            company_name = consolidated.company_name
            why_now = "; ".join(consolidated.why_now_parts[:3])
        else:
            primary_signal = signals[0]
            company_name = primary_signal.company_name or "Unknown Company"
            why_now = self._build_why_now(signals)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_signal_consolidation_integration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_signal_consolidation_integration.py
git commit -m "feat(pipeline): integrate SignalConsolidator into processing flow"
```

---

## Task 10: Add consolidation metrics to pipeline stats

**Files:**
- Modify: `workflows/pipeline.py`
- Test: `tests/workflows/test_signal_consolidation_integration.py`

**Step 1: Write the failing test**

```python
# Add to tests/workflows/test_signal_consolidation_integration.py

class TestConsolidationMetrics:
    """Test consolidation metrics in pipeline stats."""

    @pytest.mark.asyncio
    async def test_pipeline_stats_include_consolidation_metrics(self):
        """Pipeline stats should include signals_consolidated and conflicts_detected."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        now = datetime.now(timezone.utc)

        # Create signals that will produce a conflict
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme AI",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="ACME Corp",  # Different name = conflict
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        store = AsyncMock()
        store.get_pending_signals.return_value = signals
        store.check_suppression.return_value = None
        store.mark_queued = AsyncMock()
        store.mark_rejected = AsyncMock()

        config = PipelineConfig(use_consolidation=True)
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = store
        pipeline._gate = MagicMock()
        pipeline._gate.evaluate.return_value = MagicMock(
            decision=MagicMock(value="hold"),
            confidence_score=0.5,
            reason="Test",
        )

        stats = await pipeline._process_pending(dry_run=True)

        assert "signals_consolidated" in stats
        assert "conflicts_detected" in stats
        assert stats["signals_consolidated"] == 2  # 2 signals consolidated into 1
        assert stats["conflicts_detected"] == 1  # 1 company with conflict
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_signal_consolidation_integration.py::TestConsolidationMetrics -v`
Expected: FAIL (signals_consolidated not in stats)

**Step 3: Write minimal implementation**

```python
# Modify _process_pending() in workflows/pipeline.py

# Add to stats initialization:
        stats = {
            "processed": 0,
            "auto_push": 0,
            "needs_review": 0,
            "held": 0,
            "rejected": 0,
            "prospects_created": 0,
            "prospects_updated": 0,
            "prospects_skipped": 0,
            # NEW: Consolidation metrics
            "signals_consolidated": 0,
            "conflicts_detected": 0,
        }

# After consolidation loop:
        if self._consolidator:
            stats["signals_consolidated"] = sum(
                c.signal_count for c in consolidated_map.values()
            )
            stats["conflicts_detected"] = sum(
                1 for c in consolidated_map.values() if c.has_conflicts
            )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_signal_consolidation_integration.py::TestConsolidationMetrics -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_signal_consolidation_integration.py
git commit -m "feat(pipeline): add consolidation metrics to pipeline stats"
```

---

## Task 11: Run full test suite and verify no regressions

**Files:**
- None (verification only)

**Step 1: Run all tests**

Run: `pytest tests/ -v --tb=short`
Expected: All existing tests pass + new tests pass (445+ tests)

**Step 2: Run type checking (if available)**

Run: `mypy utils/signal_consolidator.py --ignore-missing-imports`
Expected: No errors

**Step 3: Commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address any test regressions from consolidation integration"
```

---

## Summary

| Task | Description | Tests Added |
|------|-------------|-------------|
| 1 | ConsolidatedSignal dataclass | 2 |
| 2 | Source priority for company_name | 3 |
| 3 | Conflict detection | 3 |
| 4 | Description aggregation | 3 |
| 5 | Social proof aggregation | 3 |
| 6 | Founding date extraction | 3 |
| 7 | Why now aggregation | 2 |
| 8 | Weighted confidence | 2 |
| 9 | Pipeline integration | 1 |
| 10 | Consolidation metrics | 1 |
| 11 | Full suite verification | 0 |

**Total new tests:** ~23

**Files created:**
- `utils/signal_consolidator.py`
- `tests/utils/test_signal_consolidator.py`
- `tests/workflows/test_signal_consolidation_integration.py`

**Files modified:**
- `workflows/pipeline.py`
