"""Batch publish API data transfer objects.

Models for the batch publish workflow: create, preview, commit, abort.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class BatchItemDTO(BaseModel):
    """A single item within a batch."""

    id: int
    review_id: int
    company_id: str
    canonical_key: Optional[str] = None
    company_name: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    notion_page_id: Optional[str] = None
    error_message: Optional[str] = None


class BatchSummary(BaseModel):
    """Compact batch summary for list view."""

    batch_id: str
    status: str
    item_count: int = 0
    pushed_count: Optional[int] = None
    error_count: Optional[int] = None
    actor: Optional[str] = None
    created_at: str
    committed_at: Optional[str] = None


class BatchPreview(BatchSummary):
    """Full batch preview with items and integrity hash."""

    items: list[BatchItemDTO] = Field(default_factory=list)
    items_hash: str = Field(
        ...,
        description="SHA256[:16] of sorted item IDs — required for commit TOCTOU guard",
    )


class BatchCreateRequest(BaseModel):
    """Request to create a new batch from approved reviews."""

    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of items in the batch",
    )


class BatchCommitRequest(BaseModel):
    """Request to commit a batch. expected_items_hash is REQUIRED (CI-6)."""

    expected_items_hash: str = Field(
        ...,
        description="SHA256[:16] of sorted item IDs from preview (TOCTOU guard)",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, validate without executing",
    )


class BatchCreateResponse(BaseModel):
    """Response after creating a batch."""

    batch_id: str
    item_count: int
    items_hash: str
