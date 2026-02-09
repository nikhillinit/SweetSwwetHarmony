"""Triage API data transfer objects.

BFF models for the triage workflow — stable schema for both dashboard
and CLI consumers. Internal storage objects are never exposed directly.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# SIGNAL EVIDENCE (embedded in detail view)
# =============================================================================

class SignalEvidence(BaseModel):
    """A signal contributing to a company's triage evaluation."""

    signal_id: int
    signal_type: str
    source_api: str
    confidence: float
    detected_at: Optional[str] = None
    excerpt: Optional[str] = Field(
        default=None,
        description="First 200 chars of relevant raw_data description",
    )


class CaseLawMatch(BaseModel):
    """A precedent case matched by similarity."""

    precedent_id: int
    label: str  # TP or FP
    similarity: float
    company_name: str


class AuditEntry(BaseModel):
    """An audit trail entry for a triage action."""

    action_type: str
    actor: str
    reason: Optional[str] = None
    created_at: str


# =============================================================================
# TRIAGE LIST / DETAIL RESPONSES
# =============================================================================

class TriageItemSummary(BaseModel):
    """Compact row for the Fast Pass triage table."""

    review_id: int
    company_id: str
    company_name: Optional[str] = None
    canonical_key: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    sources: Optional[str] = Field(
        default=None,
        description="Comma-separated source_api values",
    )
    signal_count: int = 0
    thesis_category: Optional[str] = None
    detected_at: Optional[str] = None
    created_at: str
    updated_at: str


class ACHSummaryDTO(BaseModel):
    """Compact ACH analysis summary for embedding in triage detail."""

    top_hypothesis: Optional[str] = None
    top_score: Optional[float] = None
    bull_summary: Optional[str] = None
    bear_summary: Optional[str] = None
    differentiator_count: int = 0
    builder_version: Optional[str] = None
    created_at: Optional[str] = None


class TriageItemDetail(TriageItemSummary):
    """Full intelligence for the Deep Review view."""

    signals: list[SignalEvidence] = Field(default_factory=list)
    total_signal_count: int = 0
    thesis_rationale: Optional[str] = None
    functional_approach: Optional[str] = None
    case_law_matches: list[CaseLawMatch] = Field(default_factory=list)
    ach_summary: Optional[ACHSummaryDTO] = None
    audit_history: list[AuditEntry] = Field(default_factory=list)


# =============================================================================
# TRIAGE ACTION REQUEST / RESPONSE
# =============================================================================

class TriageActionRequest(BaseModel):
    """Request body for approve/reject/defer actions.

    updated_at is REQUIRED for optimistic concurrency (CI-3).
    """

    reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Justification for the triage decision",
    )
    updated_at: str = Field(
        ...,
        description="Last-known updated_at from the review item (for optimistic concurrency)",
    )


class TriageActionResponse(BaseModel):
    """Response after a triage action is executed."""

    review_id: int
    action: str
    new_status: str
    audit_event_id: int
    message: str
