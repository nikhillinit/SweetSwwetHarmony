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

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
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

    def consolidate(self, signals: List["StoredSignal"]) -> ConsolidatedSignal:
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

        # Detect conflicts
        conflict_flags = self._detect_conflicts(signals)

        # Basic aggregation
        canonical_key = signals[0].canonical_key
        contributing_ids = [s.id for s in signals]
        signal_types = list(set(s.signal_type for s in signals))
        source_apis = list(set(s.source_api for s in signals))

        # Confidence: simple average (for now)
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

    def _select_company_name(self, sorted_signals: List["StoredSignal"]) -> str:
        """Select company_name from highest priority source that has it."""
        for signal in sorted_signals:
            if signal.company_name and signal.company_name.strip():
                return signal.company_name.strip()
        return "Unknown Company"

    def _detect_conflicts(self, signals: List["StoredSignal"]) -> List[ConflictFlag]:
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
