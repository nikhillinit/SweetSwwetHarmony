"""
Synthesis Types Module for Phase G Synthesis Enhancement

Defines the core provenance data structures used for audit-grade synthesis:
- FieldProvenance: tracks where a value came from
- FieldChoice: records which value was chosen and why
- ConflictRecord: documents conflicts between values

These primitives enable the "one-hop explanation" requirement:
- "Why is this value here?" → FieldChoice.decision_reason
- "What were the alternatives?" → ConflictRecord.candidates
- "Why did X win over Y?" → FieldChoice.decision_rule

Usage:
    from utils.synthesis_types import FieldProvenance, FieldChoice, ConflictRecord

    # Create provenance for a field value
    prov = FieldProvenance(
        value="Acme Inc",
        normalized_value="acme",
        source_key="companies_house",
        signal_id=123,
        confidence=0.85,
        detected_at=datetime.now(timezone.utc),
        evidence_ref="signal:123",
    )

    # Record a choice
    choice = FieldChoice(
        chosen=prov,
        decision_rule="g_v1.0:pick_highest_score",
        decision_reason="Selected by authority * confidence score",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


# =============================================================================
# CONSTANTS
# =============================================================================

# Conflict type constants
CONFLICT_VALUE_MISMATCH = "VALUE_MISMATCH"
CONFLICT_AUTHORITY_TIE = "AUTHORITY_TIE"
CONFLICT_TEMPORAL = "TEMPORAL_CONFLICT"
CONFLICT_TYPE_MISMATCH = "TYPE_MISMATCH"

# Severity constants (map from Materiality)
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass(frozen=True)
class FieldProvenance:
    """
    Tracks the origin of a single field value.

    This is the audit trail for a candidate value, recording:
    - The raw and normalized value
    - Where it came from (source, signal)
    - How confident we are in it
    - When it was detected
    - Evidence for debugging

    Attributes:
        value: The raw value as received
        normalized_value: Canonicalized value for comparison
        source_key: Source API identifier (e.g., "companies_house", "sec_edgar")
        signal_id: ID of the StoredSignal this came from
        confidence: Signal confidence [0.0-1.0]
        detected_at: When the signal was detected
        evidence_ref: Reference for debugging (e.g., "signal:123", URL)
        run_id: Optional pipeline run identifier for batch tracking
    """
    value: Any
    normalized_value: Any
    source_key: str
    signal_id: int
    confidence: float
    detected_at: datetime
    evidence_ref: str
    run_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "value": self.value,
            "normalized_value": self.normalized_value,
            "source_key": self.source_key,
            "signal_id": self.signal_id,
            "confidence": self.confidence,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "evidence_ref": self.evidence_ref,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class FieldChoice:
    """
    Records which value was chosen for a field and why.

    This is the "one-hop explanation" structure that answers:
    - "Why is this value here?" → decision_reason
    - "What rule was applied?" → decision_rule

    Attributes:
        chosen: The FieldProvenance that was selected as the winner
        decision_rule: Rule identifier (e.g., "g_v1.0:pick_highest_score")
        decision_reason: Human-readable explanation of the decision
    """
    chosen: FieldProvenance
    decision_rule: str
    decision_reason: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "chosen": self.chosen.to_dict(),
            "decision_rule": self.decision_rule,
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True)
class ConflictRecord:
    """
    Documents a conflict between candidate values for a field.

    When multiple signals provide different values for the same field,
    a conflict is recorded if the values are not equivalent (after
    normalization and tolerance checks).

    Attributes:
        field_name: Name of the field with conflict
        candidates: List of FieldProvenance objects (bounded by max_candidates)
        conflict_type: Type of conflict (VALUE_MISMATCH, AUTHORITY_TIE, etc.)
        severity: Severity level (CRITICAL, WARNING, INFO)
        resolution: Optional FieldChoice if conflict was resolved
    """
    field_name: str
    candidates: List[FieldProvenance]
    conflict_type: str
    severity: str
    resolution: Optional[FieldChoice] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "field_name": self.field_name,
            "candidates": [c.to_dict() for c in self.candidates],
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "resolution": self.resolution.to_dict() if self.resolution else None,
        }
