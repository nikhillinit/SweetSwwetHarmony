"""
Label taxonomy for the Discovery Engine quality system.

Defines three label layers and their lag windows:
1. Operator labels — real-time triage decisions (approve/reject/defer)
2. Outcome labels — eventual ground truth from Notion status (funded/passed)
3. Gold labels — manually verified labels for evaluation sets

Each label type has a distinct lag window, provenance rules,
and conflict resolution policy.

See also: docs/label-taxonomy.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# =============================================================================
# LABEL TYPES
# =============================================================================

class LabelLayer(str, Enum):
    """Which layer of the label taxonomy a label belongs to."""

    OPERATOR = "operator"
    OUTCOME = "outcome"
    GOLD = "gold"


class OperatorLabel(str, Enum):
    """Labels applied by human operators during triage."""

    TP = "TP"  # True Positive — thesis-relevant company
    FP = "FP"  # False Positive — not thesis-relevant
    UNSURE = "UNSURE"  # Needs more information
    DEFERRED = "DEFERRED"  # Intentionally deferred for later review


class OutcomeLabel(str, Enum):
    """Labels derived from eventual Notion CRM status."""

    FUNDED = "funded"  # Company received investment (strong TP)
    COMMITTED = "committed"  # In active due diligence
    PASSED = "passed"  # Explicitly passed (could be TP that didn't fit)
    LOST = "lost"  # Lost deal
    TRACKING = "tracking"  # Still in pipeline (no outcome yet)
    NO_OUTCOME = "no_outcome"  # Pushed but no status update after lag window


class GoldLabel(str, Enum):
    """Manually verified labels for evaluation/canary sets."""

    TP = "TP"
    FP = "FP"
    BORDERLINE = "BORDERLINE"  # On the boundary — useful for calibration


# =============================================================================
# LAG WINDOWS
# =============================================================================

@dataclass(frozen=True)
class LagWindow:
    """Time window before a label is considered stable/trustworthy."""

    layer: LabelLayer
    min_days: int
    recommended_days: int
    description: str


LAG_WINDOWS = {
    LabelLayer.OPERATOR: LagWindow(
        layer=LabelLayer.OPERATOR,
        min_days=0,
        recommended_days=0,
        description="Immediate — applied at triage time",
    ),
    LabelLayer.OUTCOME: LagWindow(
        layer=LabelLayer.OUTCOME,
        min_days=30,
        recommended_days=90,
        description="30-90 days — wait for Notion status to settle",
    ),
    LabelLayer.GOLD: LagWindow(
        layer=LabelLayer.GOLD,
        min_days=0,
        recommended_days=0,
        description="Manual verification — no lag, but requires human review",
    ),
}


# =============================================================================
# CONFLICT RESOLUTION
# =============================================================================

# When labels disagree across layers, higher layers win.
# Gold > Outcome > Operator
LABEL_PRIORITY = {
    LabelLayer.GOLD: 3,
    LabelLayer.OUTCOME: 2,
    LabelLayer.OPERATOR: 1,
}


def resolve_label_conflict(
    operator_label: Optional[str],
    outcome_label: Optional[str],
    gold_label: Optional[str],
) -> tuple[Optional[str], LabelLayer]:
    """Resolve conflicting labels across layers.

    Returns (winning_label, winning_layer).
    Gold takes priority, then outcome, then operator.
    """
    if gold_label:
        return gold_label, LabelLayer.GOLD
    if outcome_label:
        # Map outcome to TP/FP for compatibility
        if outcome_label in ("funded", "committed"):
            return "TP", LabelLayer.OUTCOME
        elif outcome_label == "passed":
            return "FP", LabelLayer.OUTCOME
        return outcome_label, LabelLayer.OUTCOME
    if operator_label:
        return operator_label, LabelLayer.OPERATOR
    return None, LabelLayer.OPERATOR


# =============================================================================
# LABEL PROVENANCE
# =============================================================================

@dataclass
class LabelProvenance:
    """Tracks where a label came from for audit purposes."""

    layer: LabelLayer
    value: str
    source: str  # "operator:gp@example.com", "notion_sync", "manual_gold"
    created_at: str  # ISO 8601
    signal_id: int
    confidence: Optional[float] = None  # How confident the labeler was
    reason: Optional[str] = None  # Why this label was applied
