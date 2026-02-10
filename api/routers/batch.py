"""
Batch Publish API Router — thin proxy to batch_publisher workflow.

Endpoints:
- GET /batches — List recent batches
- POST /batches — Create a new batch from approved reviews
- GET /batches/{batch_id} — Preview batch contents (with items_hash)
- POST /batches/{batch_id}/commit — Commit batch (CI-6: TOCTOU guard)
- POST /batches/{batch_id}/abort — Abort and revert reviews

All error mapping follows the contracts.py error_response pattern.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import BaseResponse, ListMeta, ListResponse, error_response
from api.models.batch import (
    BatchCommitRequest,
    BatchCreateRequest,
    BatchCreateResponse,
    BatchItemDTO,
    BatchPreview,
    BatchSummary,
)
from workflows.batch_publisher import (
    BatchError,
    BatchNotFoundError,
    BatchStateError,
    abort_batch,
    commit_batch,
    create_batch,
    list_batches,
    preview_batch,
)
from workflows.delivery_policy import DeliveryPolicyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])


# =============================================================================
# INLINE REQUEST MODELS
# =============================================================================

class AbortBatchRequest(BaseModel):
    """Request body for batch abort."""

    reason: str = Field(default="", description="Reason for aborting the batch")


# =============================================================================
# HELPERS
# =============================================================================

def _compute_items_hash(review_ids: list[int]) -> str:
    """Compute SHA256[:16] of sorted review IDs for TOCTOU guard (CI-6)."""
    payload = json.dumps(sorted(review_ids))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _extract_review_ids(items: list[dict]) -> list[int]:
    """Extract review_id values from batch items."""
    return [item["review_id"] for item in items]


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("", response_model=ListResponse[BatchSummary])
async def list_batches_endpoint(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List recent batches with optional status filter."""
    store = request.app.state.store
    batches = await list_batches(store, status=status_filter, limit=limit)
    items = [BatchSummary(**b) for b in batches]
    return ListResponse(
        data=items,
        meta=ListMeta(has_more=len(items) == limit),
    )


@router.post("", response_model=BaseResponse[BatchCreateResponse])
async def create_batch_endpoint(
    request: Request,
    body: BatchCreateRequest = BatchCreateRequest(),
    operator: OperatorContext = Depends(
        require_permission(Permission.BATCH_COMMIT)
    ),
):
    """Create a new batch from approved reviews."""
    store = request.app.state.store
    try:
        result = await create_batch(
            store, limit=body.limit, actor=operator.actor_label,
        )
    except BatchError as e:
        raise error_response(
            400, "bad_request", "BATCH_CREATE_FAILED", str(e),
        )

    review_ids = _extract_review_ids(result["items"])
    items_hash = _compute_items_hash(review_ids)

    return BaseResponse(data=BatchCreateResponse(
        batch_id=result["batch_id"],
        item_count=result["item_count"],
        items_hash=items_hash,
    ))


@router.get("/{batch_id}", response_model=BaseResponse[BatchPreview])
async def get_batch_preview(
    batch_id: str,
    request: Request,
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Preview batch contents with integrity hash for commit."""
    store = request.app.state.store
    try:
        result = await preview_batch(store, batch_id)
    except BatchNotFoundError as e:
        raise error_response(
            404, "not_found", "BATCH_NOT_FOUND", str(e),
        )

    items = [BatchItemDTO(**item) for item in result["items"]]
    review_ids = _extract_review_ids(result["items"])
    items_hash = _compute_items_hash(review_ids)

    preview = BatchPreview(
        batch_id=result["batch_id"],
        status=result["status"],
        item_count=result["item_count"],
        pushed_count=result.get("pushed_count"),
        error_count=result.get("error_count"),
        actor=result.get("actor"),
        created_at=result["created_at"],
        committed_at=result.get("committed_at"),
        items=items,
        items_hash=items_hash,
    )
    return BaseResponse(data=preview)


@router.post("/{batch_id}/commit", response_model=BaseResponse[dict])
async def commit_batch_endpoint(
    batch_id: str,
    body: BatchCommitRequest,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.BATCH_COMMIT)
    ),
):
    """Commit a batch with TOCTOU guard (CI-6)."""
    store = request.app.state.store

    # CI-6: Verify items haven't changed since preview
    try:
        current_preview = await preview_batch(store, batch_id)
    except BatchNotFoundError as e:
        raise error_response(
            404, "not_found", "BATCH_NOT_FOUND", str(e),
        )

    review_ids = _extract_review_ids(current_preview["items"])
    current_hash = _compute_items_hash(review_ids)
    if current_hash != body.expected_items_hash:
        raise error_response(
            409, "conflict", "BATCH_ITEMS_CHANGED",
            "Batch items changed since preview. Please re-preview and try again.",
            detail={
                "expected": body.expected_items_hash,
                "actual": current_hash,
            },
        )

    try:
        # TODO: Wire NotionPusher for real commits (pusher=None → dry-run only)
        result = await commit_batch(
            store, batch_id,
            dry_run=body.dry_run,
            actor=operator.actor_label,
        )
    except BatchNotFoundError as e:
        raise error_response(
            404, "not_found", "BATCH_NOT_FOUND", str(e),
        )
    except BatchStateError as e:
        raise error_response(
            409, "conflict", "BATCH_STATE_ERROR", str(e),
        )
    except DeliveryPolicyError as e:
        raise error_response(
            423, "locked", "FEATURE_DISABLED", str(e),
        )
    except BatchError as e:
        raise error_response(
            400, "bad_request", "BATCH_COMMIT_FAILED", str(e),
        )

    return BaseResponse(data=result)


@router.post("/{batch_id}/abort", response_model=BaseResponse[dict])
async def abort_batch_endpoint(
    batch_id: str,
    request: Request,
    body: AbortBatchRequest = AbortBatchRequest(),
    operator: OperatorContext = Depends(
        require_permission(Permission.BATCH_COMMIT)
    ),
):
    """Abort a draft batch and revert reviews to approved."""
    store = request.app.state.store
    try:
        result = await abort_batch(
            store, batch_id,
            reason=body.reason,
            actor=operator.actor_label,
        )
    except BatchNotFoundError as e:
        raise error_response(
            404, "not_found", "BATCH_NOT_FOUND", str(e),
        )
    except BatchStateError as e:
        raise error_response(
            409, "conflict", "BATCH_STATE_ERROR", str(e),
        )
    except BatchError as e:
        raise error_response(
            400, "bad_request", "BATCH_ABORT_FAILED", str(e),
        )

    return BaseResponse(data=result)
