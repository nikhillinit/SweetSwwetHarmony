"""Hunter API data transfer objects.

DTOs for hunter runs, results, promotion, and budget endpoints.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HunterRunSummary(BaseModel):
    """Summary row for hunter run list."""

    run_id: str
    run_type: str
    status: str
    total_queries: int = 0
    completed_queries: int = 0
    total_results: int = 0
    promoted_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str


class HunterQuerySummary(BaseModel):
    """Summary of a hunter query within a run."""

    id: int
    run_id: str
    collector: str
    query_text: str
    status: str
    result_count: int = 0
    created_at: str
    executed_at: Optional[str] = None


class HunterResultSummary(BaseModel):
    """Summary of a hunter result."""

    id: int
    run_id: str
    query_id: int
    company_name: Optional[str] = None
    canonical_key: Optional[str] = None
    company_id: Optional[str] = None
    source_api: Optional[str] = None
    confidence_score: Optional[float] = None
    thesis_fit_score: Optional[float] = None
    status: str
    already_known: bool = False
    operator_feedback: Optional[str] = None
    promoted_signal_id: Optional[int] = None
    created_at: str


class PromoteRequest(BaseModel):
    """Request body for promoting a hunter result to signals."""

    reason: str = Field(default="", description="Reason for promotion")


class PromoteResponse(BaseModel):
    """Response for a promotion attempt."""

    success: bool
    signal_id: Optional[int] = None
    result_id: int
    status: str
    message: str
    collision: bool = False


class FeedbackRequest(BaseModel):
    """Request body for submitting feedback on a hunter result."""

    feedback: str = Field(
        ...,
        description="Operator feedback: 'relevant', 'not_relevant', 'already_known'",
    )
    reason: str = Field(default="", description="Optional reason for feedback")


class FeedbackResponse(BaseModel):
    """Response after submitting feedback."""

    result_id: int
    new_status: str
    message: str


class BudgetSummary(BaseModel):
    """Budget usage summary for today."""

    budget_date: str
    global_info: dict = Field(default_factory=dict, alias="global")
    collectors: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
