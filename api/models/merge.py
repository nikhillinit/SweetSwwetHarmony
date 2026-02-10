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
