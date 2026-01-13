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
