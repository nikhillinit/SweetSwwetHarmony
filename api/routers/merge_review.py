"""
Merge Review Router — Shadow runs and merge suggestion endpoints.

Endpoints:
- GET /entities/merge-suggestions — Paginated list (Permission.VIEW)
- GET /entities/merge-suggestions/{id} — Detail with blast radius (Permission.ENTITY_MERGE)
- GET /entities/shadow-runs — Paginated shadow run list (Permission.VIEW)

All endpoints use cursor pagination: default limit=50, max limit=200,
sort key (created_at DESC, id DESC).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import BaseResponse, ListMeta, ListResponse, error_response
from api.models.merge import (
    BlastRadius,
    MergeSuggestionDetail,
    MergeSuggestionSummary,
    ShadowRunSummary,
)
from api.pagination import decode_cursor, encode_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entities", tags=["entities"])


# =============================================================================
# MERGE SUGGESTIONS
# =============================================================================

@router.get("/merge-suggestions")
async def list_merge_suggestions(
    request: Request,
    status: Optional[str] = Query(default=None, description="Filter by status"),
    cursor: Optional[str] = Query(default=None, description="Pagination cursor"),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List merge suggestions with cursor pagination."""
    from intelligence.merge_suggestions import list_merge_suggestions as _list

    store = request.app.state.store

    cursor_data = decode_cursor(cursor) if cursor else {}
    cursor_created_at = cursor_data.get("created_at")
    cursor_id = cursor_data.get("id")
    if cursor_id is not None:
        cursor_id = int(cursor_id)

    suggestions = await _list(
        store,
        status=status,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        limit=limit,
    )

    items = [
        MergeSuggestionSummary(
            id=s.id,
            pair_key=s.pair_key,
            entity_a_company_id=s.entity_a_company_id,
            entity_b_company_id=s.entity_b_company_id,
            entity_a_canonical_key=s.entity_a_canonical_key,
            entity_b_canonical_key=s.entity_b_canonical_key,
            entity_a_company_name=s.entity_a_company_name,
            entity_b_company_name=s.entity_b_company_name,
            match_type=s.match_type,
            similarity_score=s.similarity_score,
            status=s.status,
            created_at=s.created_at,
        )
        for s in suggestions
    ]

    next_cursor = None
    if items and len(items) == limit:
        last = suggestions[-1]
        next_cursor = encode_cursor({"created_at": last.created_at, "id": str(last.id)})

    return ListResponse(
        data=items,
        meta=ListMeta(
            total=len(items),
            cursor=next_cursor,
            has_more=len(items) == limit,
        ),
    ).model_dump()


@router.get("/merge-suggestions/{suggestion_id}")
async def get_merge_suggestion_detail(
    request: Request,
    suggestion_id: int,
    operator: OperatorContext = Depends(require_permission(Permission.ENTITY_MERGE)),
):
    """Get merge suggestion detail with lazy blast radius computation."""
    from intelligence.merge_suggestions import (
        get_merge_suggestion, compute_blast_radius,
    )

    store = request.app.state.store
    suggestion = await get_merge_suggestion(store, suggestion_id)

    if not suggestion:
        raise error_response(404, "not_found", "SUGGESTION_NOT_FOUND",
                           f"Merge suggestion {suggestion_id} not found")

    # Lazy blast radius computation
    blast = None
    if suggestion.blast_radius_json:
        try:
            br_data = json.loads(suggestion.blast_radius_json)
            blast = BlastRadius(**br_data)
        except (json.JSONDecodeError, TypeError):
            pass

    if blast is None:
        br_data = await compute_blast_radius(
            store,
            suggestion.entity_a_company_id,
            suggestion.entity_b_company_id,
        )
        blast = BlastRadius(**br_data)

        # Cache in DB
        try:
            db = store._db
            await db.execute(
                "UPDATE merge_suggestions SET blast_radius_json = ? WHERE id = ?",
                (json.dumps(br_data), suggestion_id),
            )
            await db.commit()
        except Exception as e:
            logger.warning("Failed to cache blast radius: %s", e)

    evidence = None
    if suggestion.evidence_json:
        try:
            evidence = json.loads(suggestion.evidence_json)
        except (json.JSONDecodeError, TypeError):
            pass

    detail = MergeSuggestionDetail(
        id=suggestion.id,
        pair_key=suggestion.pair_key,
        entity_a_company_id=suggestion.entity_a_company_id,
        entity_b_company_id=suggestion.entity_b_company_id,
        entity_a_canonical_key=suggestion.entity_a_canonical_key,
        entity_b_canonical_key=suggestion.entity_b_canonical_key,
        entity_a_company_name=suggestion.entity_a_company_name,
        entity_b_company_name=suggestion.entity_b_company_name,
        match_type=suggestion.match_type,
        similarity_score=suggestion.similarity_score,
        status=suggestion.status,
        created_at=suggestion.created_at,
        scoring_version=suggestion.scoring_version,
        evidence=evidence,
        blast_radius=blast,
        reviewed_by=suggestion.reviewed_by,
        reviewed_at=suggestion.reviewed_at,
        shadow_run_id=suggestion.shadow_run_id,
    )

    return BaseResponse(data=detail).model_dump()


# =============================================================================
# SHADOW RUNS
# =============================================================================

@router.get("/shadow-runs")
async def list_shadow_runs(
    request: Request,
    cursor: Optional[str] = Query(default=None, description="Pagination cursor"),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List shadow entity comparison runs with cursor pagination."""
    store = request.app.state.store
    db = store._db

    cursor_data = decode_cursor(cursor) if cursor else {}
    cursor_created_at = cursor_data.get("created_at")
    cursor_id = cursor_data.get("id")

    conditions = []
    params = []

    if cursor_created_at and cursor_id is not None:
        conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
        params.extend([cursor_created_at, cursor_created_at, int(cursor_id)])

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(min(max(limit, 1), 200))

    db_cursor = await db.execute(
        f"""
        SELECT id, run_id, status, total_signals, agreements, disagreements,
               agreement_rate, duration_ms, inputs_hash, truncated, created_at
        FROM shadow_entity_runs
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = await db_cursor.fetchall()

    items = [
        ShadowRunSummary(
            id=row[0],
            run_id=row[1],
            status=row[2],
            total_signals=row[3] or 0,
            agreements=row[4] or 0,
            disagreements=row[5] or 0,
            agreement_rate=row[6],
            duration_ms=row[7],
            inputs_hash=row[8],
            truncated=bool(row[9]),
            created_at=row[10],
        )
        for row in rows
    ]

    next_cursor = None
    if items and len(items) == limit:
        last = rows[-1]
        next_cursor = encode_cursor({"created_at": last[10], "id": str(last[0])})

    return ListResponse(
        data=items,
        meta=ListMeta(
            total=len(items),
            cursor=next_cursor,
            has_more=len(items) == limit,
        ),
    ).model_dump()
