"""
Hunter API Router — read endpoints + promotion write.

Endpoints:
- GET /hunter/runs — List recent hunter runs (cursor-paginated)
- GET /hunter/runs/{run_id}/queries — Queries for a run
- GET /hunter/runs/{run_id}/results — Results with status/score filters
- POST /hunter/results/{result_id}/feedback — Submit feedback
- POST /hunter/results/{result_id}/promote — Promote to signals
- GET /hunter/budget — Budget usage summary
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import (
    BaseResponse,
    ListMeta,
    ListResponse,
    error_response,
    feature_disabled_response,
    get_idempotency_key,
)
from api.models.hunter import (
    BudgetSummary,
    FeedbackRequest,
    FeedbackResponse,
    HunterQuerySummary,
    HunterResultSummary,
    HunterRunSummary,
    PromoteRequest,
    PromoteResponse,
)
from api.pagination import decode_cursor, encode_cursor
from workflows.feature_guards import (
    FeatureDisabledError,
    WriteFeature,
    assert_write_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hunter", tags=["hunter"])


# =============================================================================
# READ ENDPOINTS
# =============================================================================

@router.get("/runs", response_model=ListResponse[HunterRunSummary])
async def list_hunter_runs(
    request: Request,
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List recent hunter runs with cursor pagination."""
    from workflows.run_manager import list_runs

    store = request.app.state.store
    cursor_data = decode_cursor(cursor) if cursor else {}

    runs = await list_runs(
        store,
        run_type="hunter",
        limit=limit + 1,
    )

    # Manual cursor pagination on created_at, id
    if cursor_data:
        c_ts = cursor_data.get("created_at")
        c_id = cursor_data.get("id")
        filtered = []
        for r in runs:
            if r.created_at < c_ts or (r.created_at == c_ts and r.id < c_id):
                filtered.append(r)
        runs = filtered

    has_more = len(runs) > limit
    page = runs[:limit]

    items = []
    db = store._db
    for r in page:
        # Get result counts
        rc = await db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status='promoted' THEN 1 ELSE 0 END)"
            " FROM hunter_results WHERE run_id = ?",
            (r.id,),
        )
        row = await rc.fetchone()
        total_results = row[0] or 0 if row else 0
        promoted_count = row[1] or 0 if row else 0

        items.append(HunterRunSummary(
            run_id=r.id,
            run_type=r.run_type,
            status=r.status.value if hasattr(r.status, "value") else r.status,
            total_queries=0,
            completed_queries=0,
            total_results=total_results,
            promoted_count=promoted_count,
            started_at=r.started_at,
            completed_at=r.completed_at,
            created_at=r.created_at,
        ))

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor({"created_at": last.created_at, "id": last.id})

    return ListResponse(
        data=items,
        meta=ListMeta(next_cursor=next_cursor, has_more=has_more),
    )


@router.get("/runs/{run_id}/queries", response_model=ListResponse[HunterQuerySummary])
async def list_run_queries(
    run_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List queries for a hunter run."""
    from storage.hunter_result_store import get_queries_for_run

    store = request.app.state.store
    queries = await get_queries_for_run(store, run_id)

    items = [
        HunterQuerySummary(
            id=q["id"],
            run_id=q["run_id"],
            collector=q.get("collector", "unknown"),
            query_text=q.get("query_text", ""),
            status=q["status"],
            result_count=q.get("results_count", 0),
            created_at=q["created_at"],
            executed_at=q.get("executed_at"),
        )
        for q in queries
    ]

    return ListResponse(
        data=items,
        meta=ListMeta(has_more=False),
    )


@router.get("/runs/{run_id}/results", response_model=ListResponse[HunterResultSummary])
async def list_run_results(
    run_id: str,
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List results for a hunter run with optional status filter."""
    from storage.hunter_result_store import get_results_for_run

    store = request.app.state.store
    results = await get_results_for_run(
        store, run_id, status=status_filter, limit=limit,
    )

    items = [
        HunterResultSummary(
            id=r["id"],
            run_id=r["run_id"],
            query_id=r["query_id"],
            company_name=r.get("company_name"),
            canonical_key=r.get("canonical_key"),
            company_id=r.get("company_id"),
            source_api=r.get("source_api"),
            confidence_score=r.get("confidence_score"),
            thesis_fit_score=r.get("thesis_fit_score"),
            status=r["status"],
            already_known=r.get("already_known", False),
            operator_feedback=r.get("operator_feedback"),
            promoted_signal_id=r.get("promoted_signal_id"),
            created_at=r["created_at"],
        )
        for r in results
    ]

    return ListResponse(
        data=items,
        meta=ListMeta(has_more=False),
    )


@router.get("/budget", response_model=BaseResponse[dict])
async def get_budget(
    request: Request,
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Get budget usage summary for today."""
    from storage.hunter_result_store import get_budget_summary

    store = request.app.state.store
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = await get_budget_summary(store, today)

    return BaseResponse(data=summary)


# =============================================================================
# WRITE ENDPOINTS
# =============================================================================

@router.post(
    "/results/{result_id}/feedback",
    response_model=BaseResponse[FeedbackResponse],
)
async def submit_feedback(
    result_id: int,
    body: FeedbackRequest,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.HUNTER_RUN)
    ),
):
    """Submit operator feedback on a hunter result."""
    from storage.hunter_result_store import update_result_status, get_result_by_id

    store = request.app.state.store

    result = await get_result_by_id(store, result_id)
    if not result:
        raise error_response(
            404, "not_found", "RESULT_NOT_FOUND",
            f"Hunter result {result_id} not found",
        )

    valid_feedback = {"relevant", "not_relevant", "already_known"}
    if body.feedback not in valid_feedback:
        raise error_response(
            422, "validation_error", "VALIDATION_ERROR",
            f"Invalid feedback '{body.feedback}'. Must be one of: {sorted(valid_feedback)}",
        )

    try:
        await update_result_status(
            store, result_id, body.feedback,
            operator_feedback=body.reason or body.feedback,
            actor=operator.actor_label,
        )
    except Exception as e:
        if "not found" in str(e).lower():
            raise error_response(
                404, "not_found", "RESULT_NOT_FOUND", str(e),
            )
        raise error_response(
            409, "conflict", "INVALID_TRANSITION",
            f"Cannot transition result {result_id}: {e}",
        )

    return BaseResponse(data=FeedbackResponse(
        result_id=result_id,
        new_status=body.feedback,
        message=f"Feedback recorded for result {result_id}",
    ))


@router.post(
    "/results/{result_id}/promote",
    response_model=BaseResponse[PromoteResponse],
    status_code=201,
)
async def promote_result(
    result_id: int,
    request: Request,
    body: PromoteRequest = PromoteRequest(),
    operator: OperatorContext = Depends(
        require_permission(Permission.HUNTER_PROMOTE)
    ),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
):
    """Promote a hunter result to the signals table.

    Requires HUNTER_PROMOTE permission and HUNTER_PROMOTE_ENABLED=active.
    """
    from storage.hunter_result_store import get_result_by_id, InvalidHunterTransition
    from workflows.hunter_promotion import promote_hunter_result

    store = request.app.state.store

    # Feature guard
    try:
        assert_write_enabled(WriteFeature.HUNTER_PROMOTE)
    except FeatureDisabledError as e:
        raise feature_disabled_response(
            e.feature.value, e.env_var,
            request_id=getattr(request.state, "request_id", None),
        )

    # Quality threshold gate
    min_confidence = float(os.environ.get("HUNTER_PROMOTE_MIN_CONFIDENCE", "0.0"))
    if min_confidence > 0:
        result = await get_result_by_id(store, result_id)
        if not result:
            raise error_response(
                404, "not_found", "RESULT_NOT_FOUND",
                f"Hunter result {result_id} not found",
            )
        score = result.get("confidence_score") or 0.0
        if score < min_confidence:
            raise error_response(
                422, "validation_error", "BELOW_QUALITY_THRESHOLD",
                f"Result confidence {score:.2f} is below minimum {min_confidence:.2f}",
                detail={"confidence_score": score, "threshold": min_confidence},
            )

    # Merge-lineage guard: check if company was merged away
    result_data = await get_result_by_id(store, result_id)
    if result_data and result_data.get("company_id"):
        db = store._db
        try:
            cursor = await db.execute(
                """SELECT winner_company_id FROM merge_proposals
                   WHERE loser_company_id = ? AND status = 'applied'
                   ORDER BY applied_at DESC LIMIT 1""",
                (result_data["company_id"],),
            )
            merged_row = await cursor.fetchone()
            if merged_row:
                raise error_response(
                    409, "conflict", "ENTITY_MERGED_AWAY",
                    f"Company {result_data['company_id']} was merged into {merged_row[0]}. "
                    "Resolve the merge before promoting.",
                    detail={
                        "merged_into": merged_row[0],
                        "original_company_id": result_data["company_id"],
                    },
                )
        except Exception as e:
            # merge_proposals table may not exist yet (pre-Phase A)
            if "no such table" in str(e).lower():
                pass
            elif hasattr(e, "status_code"):
                raise  # Re-raise HTTPException from error_response
            else:
                logger.debug("Merge-lineage check skipped: %s", e)

    # Promote
    try:
        promo_result = await promote_hunter_result(
            store,
            result_id,
            actor=operator.actor_label,
            idempotency_key=idempotency_key,
        )
    except InvalidHunterTransition as e:
        raise error_response(
            409, "conflict", "INVALID_TRANSITION", str(e),
        )
    except ValueError as e:
        raise error_response(
            404, "not_found", "RESULT_NOT_FOUND", str(e),
        )

    status_code = 201 if promo_result.status == "promoted" else 200
    return BaseResponse(data=PromoteResponse(
        success=promo_result.success,
        signal_id=promo_result.signal_id,
        result_id=promo_result.result_id,
        status=promo_result.status,
        message=promo_result.message,
        collision=promo_result.collision,
    ))
