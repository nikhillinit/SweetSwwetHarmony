"""Merge Review API data transfer objects.

DTOs for shadow entity runs and merge suggestion review workflow.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ShadowRunSummary(BaseModel):
    """Summary of a shadow entity comparison run."""

    id: int
    run_id: str
    status: str
    total_signals: int = 0
    agreements: int = 0
    disagreements: int = 0
    agreement_rate: Optional[float] = None
    duration_ms: Optional[float] = None
    inputs_hash: Optional[str] = None
    truncated: bool = False
    created_at: str


class MergeSuggestionSummary(BaseModel):
    """Compact row for the merge suggestion list."""

    id: int
    pair_key: str
    entity_a_company_id: str
    entity_b_company_id: str
    entity_a_canonical_key: str
    entity_b_canonical_key: str
    entity_a_company_name: Optional[str] = None
    entity_b_company_name: Optional[str] = None
    match_type: str
    similarity_score: float
    status: str
    created_at: str


class BlastRadius(BaseModel):
    """Impact assessment for a merge operation."""

    signals_a: int = 0
    signals_b: int = 0
    reviews_a: int = 0
    reviews_b: int = 0
    files_a: int = 0
    files_b: int = 0
    total_affected: int = 0
    capped: bool = False
    timeout: bool = False


class MergeSuggestionDetail(MergeSuggestionSummary):
    """Full detail for merge review, including blast radius and evidence."""

    scoring_version: str = "1.0.0"
    evidence: Optional[dict[str, Any]] = None
    blast_radius: Optional[BlastRadius] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    shadow_run_id: Optional[int] = None


# =============================================================================
# MERGE PROPOSAL DTOs (Wave 4 — Write Activation)
# =============================================================================


class MergeProposalSummary(BaseModel):
    """Summary of a merge proposal for list views."""

    id: int
    suggestion_id: int
    entity_a_company_id: str
    entity_b_company_id: str
    winner_company_id: str
    loser_company_id: str
    status: str
    reason: Optional[str] = None
    proposed_by: str
    proposed_at: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    applied_at: Optional[str] = None
    rolled_back_at: Optional[str] = None
    rollback_reason: Optional[str] = None
    correlation_id: Optional[str] = None
    updated_at: str


class ProposeRequest(BaseModel):
    """Request body for creating a merge proposal from a suggestion."""

    suggestion_id: int = Field(..., description="ID of the merge suggestion to propose from")
    winner_company_id: str = Field(..., description="Company ID that will survive the merge")
    loser_company_id: str = Field(..., description="Company ID that will be absorbed")
    reason: Optional[str] = Field(default=None, description="Justification for the merge")


class ProposeResponse(BaseModel):
    """Response after creating or updating a merge proposal."""

    proposal_id: int
    status: str
    message: str


class ApproveRequest(BaseModel):
    """Request body for approving a merge proposal."""

    updated_at: str = Field(
        ..., description="Last-known updated_at for optimistic concurrency"
    )


class ApplyResponse(BaseModel):
    """Response after applying a merge proposal."""

    proposal_id: int
    status: str
    cascade_report: Optional[dict[str, Any]] = None
    shadow_mode: bool = False
    message: str


class RollbackRequest(BaseModel):
    """Request body for rolling back an applied merge."""

    reason: str = Field(..., description="Justification for rolling back the merge")
    updated_at: str = Field(
        ..., description="Last-known updated_at for optimistic concurrency"
    )


class RollbackResponse(BaseModel):
    """Response after rolling back a merge."""

    proposal_id: int
    status: str
    rollback_report: Optional[dict[str, Any]] = None
    message: str
