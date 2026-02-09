"""
Triage API Router — Fast Pass + Deep Review endpoints.

Endpoints:
- GET /triage — Paginated list with correlated subqueries (CI-2)
- GET /triage/{review_id} — Detail with capped signals (CI-5)
- POST /triage/{review_id}/approve — Atomic approve (CI-1, CI-3, CI-4)
- POST /triage/{review_id}/reject — Atomic reject
- POST /triage/{review_id}/defer — Atomic defer

All action endpoints execute status update + audit event + idempotency
inside a single BEGIN IMMEDIATE transaction (CI-1). They inline the SQL
instead of calling update_review_status() or record_event() to avoid
nested transactions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import (
    BaseResponse,
    ListMeta,
    ListResponse,
    check_idempotency_db,
    check_idempotency_conflict,
    check_version,
    error_response,
    get_idempotency_key,
    payload_fingerprint,
    store_idempotency_db,
)
from api.models.triage import (
    AuditEntry,
    SignalEvidence,
    TriageActionRequest,
    TriageActionResponse,
    TriageItemDetail,
    TriageItemSummary,
)
from api.pagination import decode_cursor, encode_cursor
from storage.review_store import VALID_TRANSITIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/triage", tags=["triage"])

# Action name → target status
_ACTION_MAP = {
    "approve": "approved",
    "reject": "rejected",
    "defer": "deferred",
}


# =============================================================================
# HELPERS
# =============================================================================

def _extract_excerpt(raw_data: Optional[str], max_len: int = 200) -> Optional[str]:
    """Extract a human-readable excerpt from signal raw_data JSON."""
    if not raw_data:
        return None
    try:
        data = json.loads(raw_data)
        for key in ("description", "summary", "title", "text", "content"):
            if key in data and data[key]:
                text = str(data[key])
                return text[:max_len] if len(text) > max_len else text
        text = str(data)
        return text[:max_len] if len(text) > max_len else text
    except (json.JSONDecodeError, TypeError):
        text = str(raw_data)
        return text[:max_len] if len(text) > max_len else text


async def _execute_triage_action(
    request: Request,
    review_id: int,
    action: str,
    body: TriageActionRequest,
    operator: OperatorContext,
    idempotency_key: Optional[str],
) -> BaseResponse[TriageActionResponse]:
    """Atomic triage action: status update + audit event + idempotency.

    Everything inside a single BEGIN IMMEDIATE transaction (CI-1).
    Inlines SQL to avoid nested transactions from review_store or
    audit_events modules.
    """
    store = request.app.state.store
    db = store._db
    new_status = _ACTION_MAP[action]
    route = f"triage_{action}"
    resource_id = str(review_id)
    p_hash = payload_fingerprint(action, body.reason, operator.user_id)

    # 1. Check idempotency BEFORE transaction (may commit during TTL cleanup)
    if idempotency_key:
        # Conflict check first: same key + different payload → 409
        await check_idempotency_conflict(
            db, idempotency_key, route, resource_id, p_hash,
        )
        # Then cache hit: same key + same payload → return cached result
        cached = await check_idempotency_db(db, idempotency_key, route, resource_id)
        if cached:
            return BaseResponse(data=TriageActionResponse(**cached.body))

    now = datetime.now(timezone.utc).isoformat()

    # 2. Atomic action inside BEGIN IMMEDIATE (CI-1)
    async with store.transaction_immediate() as tx:
        # a. Fetch current review
        cursor = await tx.execute(
            "SELECT status, updated_at FROM review_items WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise error_response(
                404, "not_found", "REVIEW_NOT_FOUND",
                f"Review {review_id} not found",
            )

        current_status, current_updated_at = row[0], row[1]

        # b. Optimistic concurrency (CI-3)
        check_version(body.updated_at, current_updated_at, "review_item")

        # c. Validate transition
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise error_response(
                409, "conflict", "INVALID_TRANSITION",
                f"Cannot {action} review {review_id}: current status "
                f"'{current_status}', allowed transitions: {allowed}",
                detail={
                    "current_status": current_status,
                    "allowed_transitions": allowed,
                },
            )

        # d. Update review status (inlined — avoids nested transaction)
        await tx.execute(
            """UPDATE review_items
               SET status = ?, updated_at = ?,
                   decided_at = ?, decided_by = ?, reason = ?
               WHERE id = ?""",
            (new_status, now, now, operator.actor_label, body.reason, review_id),
        )

        # e. Insert audit event (inlined — avoids separate commit)
        cursor = await tx.execute(
            """INSERT INTO audit_events (
                action_type, entity_type, entity_id,
                actor_id, actor_email, actor_role,
                before_state, after_state,
                reason, correlation_id, metadata,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                route,
                "review_item",
                resource_id,
                operator.user_id,
                operator.email,
                operator.role.value
                if hasattr(operator.role, "value")
                else str(operator.role),
                json.dumps({"status": current_status}),
                json.dumps({"status": new_status}),
                body.reason,
                operator.request_id,
                None,
                now,
            ),
        )
        audit_event_id = cursor.lastrowid

        # f. Store idempotency result (INSERT OR IGNORE, no separate commit)
        response_body = {
            "review_id": review_id,
            "action": action,
            "new_status": new_status,
            "audit_event_id": audit_event_id,
            "message": f"Review {review_id} {action}d successfully",
        }
        if idempotency_key:
            await store_idempotency_db(
                tx, idempotency_key, route, resource_id,
                p_hash, 200, response_body,
            )

    # Transaction committed
    logger.info(
        "Triage %s: review %d %s -> %s by %s",
        action, review_id, current_status, new_status, operator.actor_label,
    )
    return BaseResponse(data=TriageActionResponse(**response_body))


# =============================================================================
# LIST ENDPOINT (CI-2: correlated subqueries, no join explosion)
# =============================================================================

@router.get("", response_model=ListResponse[TriageItemSummary])
async def list_triage(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0),
    source_api: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search company name"),
    cursor: Optional[str] = Query(None, description="Opaque cursor from previous page"),
    limit: int = Query(50, ge=1, le=200),
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Paginated triage list with correlated subqueries for aggregates."""
    store = request.app.state.store
    db = store._db

    conditions: list[str] = []
    params: list[Any] = []

    if status_filter:
        conditions.append("ri.status = ?")
        params.append(status_filter)

    if min_confidence is not None:
        conditions.append(
            "(SELECT MAX(s.confidence) FROM signals s"
            " WHERE s.company_id = ri.company_id) >= ?"
        )
        params.append(min_confidence)

    if source_api:
        conditions.append(
            "EXISTS (SELECT 1 FROM signals s"
            " WHERE s.company_id = ri.company_id AND s.source_api = ?)"
        )
        params.append(source_api)

    if search:
        conditions.append(
            "EXISTS (SELECT 1 FROM signals s"
            " WHERE s.company_id = ri.company_id AND s.company_name LIKE ?)"
        )
        params.append(f"%{search}%")

    # Cursor-based pagination (keyset on updated_at, id)
    cursor_values = decode_cursor(cursor)
    if cursor_values:
        conditions.append(
            "(ri.updated_at < ? OR (ri.updated_at = ? AND ri.id < ?))"
        )
        params.extend([
            cursor_values.get("updated_at"),
            cursor_values.get("updated_at"),
            cursor_values.get("id"),
        ])

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            ri.id,
            ri.company_id,
            ri.status,
            ri.created_at,
            ri.updated_at,
            (SELECT s.company_name FROM signals s
             WHERE s.company_id = ri.company_id
             ORDER BY s.confidence DESC LIMIT 1) AS company_name,
            (SELECT s.canonical_key FROM signals s
             WHERE s.company_id = ri.company_id LIMIT 1) AS canonical_key,
            (SELECT MAX(s.confidence) FROM signals s
             WHERE s.company_id = ri.company_id) AS confidence,
            (SELECT COUNT(*) FROM signals s
             WHERE s.company_id = ri.company_id) AS signal_count,
            (SELECT GROUP_CONCAT(DISTINCT s.source_api) FROM signals s
             WHERE s.company_id = ri.company_id) AS sources,
            (SELECT MIN(s.detected_at) FROM signals s
             WHERE s.company_id = ri.company_id) AS detected_at,
            (SELECT tc.category FROM thesis_classifications tc
             WHERE tc.canonical_key = (
                 SELECT s2.canonical_key FROM signals s2
                 WHERE s2.company_id = ri.company_id LIMIT 1
             )
             ORDER BY tc.classified_at DESC LIMIT 1) AS thesis_category
        FROM review_items ri
        {where_clause}
        ORDER BY ri.updated_at DESC, ri.id DESC
        LIMIT ?
    """
    params.append(limit + 1)  # one extra to detect has_more

    db_cursor = await db.execute(sql, params)
    rows = await db_cursor.fetchall()

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items = [
        TriageItemSummary(
            review_id=row[0],
            company_id=row[1],
            status=row[2],
            created_at=row[3],
            updated_at=row[4],
            company_name=row[5],
            canonical_key=row[6],
            confidence=row[7],
            signal_count=row[8] or 0,
            sources=row[9],
            detected_at=row[10],
            thesis_category=row[11],
        )
        for row in page_rows
    ]

    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor({"updated_at": last[4], "id": last[0]})

    return ListResponse(
        data=items,
        meta=ListMeta(next_cursor=next_cursor, has_more=has_more),
    )


# =============================================================================
# DETAIL ENDPOINT (CI-5: cap signals at 50 + total_signal_count)
# =============================================================================

@router.get("/{review_id}", response_model=BaseResponse[TriageItemDetail])
async def get_triage_detail(
    review_id: int,
    request: Request,
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Full intelligence for deep review: signals, thesis, audit trail."""
    store = request.app.state.store
    db = store._db

    # Fetch review item
    cursor = await db.execute(
        "SELECT id, company_id, status, created_at, updated_at"
        " FROM review_items WHERE id = ?",
        (review_id,),
    )
    review = await cursor.fetchone()
    if not review:
        raise error_response(
            404, "not_found", "REVIEW_NOT_FOUND",
            f"Review {review_id} not found",
        )

    company_id = review[1]

    # Company summary via correlated subqueries
    cursor = await db.execute(
        """SELECT
            (SELECT s.company_name FROM signals s
             WHERE s.company_id = ?
             ORDER BY s.confidence DESC LIMIT 1),
            (SELECT s.canonical_key FROM signals s
             WHERE s.company_id = ? LIMIT 1),
            (SELECT MAX(s.confidence) FROM signals s
             WHERE s.company_id = ?),
            (SELECT COUNT(*) FROM signals s
             WHERE s.company_id = ?),
            (SELECT GROUP_CONCAT(DISTINCT s.source_api) FROM signals s
             WHERE s.company_id = ?),
            (SELECT MIN(s.detected_at) FROM signals s
             WHERE s.company_id = ?)""",
        (company_id,) * 6,
    )
    summary = await cursor.fetchone()

    company_name = summary[0] if summary else None
    canonical_key = summary[1] if summary else None
    confidence = summary[2] if summary else None
    total_signal_count = (summary[3] or 0) if summary else 0
    sources = summary[4] if summary else None
    detected_at = summary[5] if summary else None

    # Thesis classification
    thesis_category = None
    thesis_rationale = None
    if canonical_key:
        cursor = await db.execute(
            """SELECT category, rationale FROM thesis_classifications
               WHERE canonical_key = ?
               ORDER BY classified_at DESC LIMIT 1""",
            (canonical_key,),
        )
        tc_row = await cursor.fetchone()
        if tc_row:
            thesis_category = tc_row[0]
            thesis_rationale = tc_row[1]

    # Signals — 50 most recent (CI-5)
    cursor = await db.execute(
        """SELECT id, signal_type, source_api, confidence, detected_at, raw_data
           FROM signals
           WHERE company_id = ?
           ORDER BY detected_at DESC, id DESC
           LIMIT 50""",
        (company_id,),
    )
    signal_rows = await cursor.fetchall()
    signals = [
        SignalEvidence(
            signal_id=r[0],
            signal_type=r[1],
            source_api=r[2],
            confidence=r[3],
            detected_at=r[4],
            excerpt=_extract_excerpt(r[5]),
        )
        for r in signal_rows
    ]

    # Audit history from audit_events (v35)
    cursor = await db.execute(
        """SELECT action_type, actor_email, reason, created_at
           FROM audit_events
           WHERE entity_type = 'review_item' AND entity_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 20""",
        (str(review_id),),
    )
    audit_rows = await cursor.fetchall()
    audit_history = [
        AuditEntry(
            action_type=r[0],
            actor=r[1] or "system",
            reason=r[2],
            created_at=r[3],
        )
        for r in audit_rows
    ]

    detail = TriageItemDetail(
        review_id=review[0],
        company_id=review[1],
        status=review[2],
        created_at=review[3],
        updated_at=review[4],
        company_name=company_name,
        canonical_key=canonical_key,
        confidence=confidence,
        signal_count=total_signal_count,
        sources=sources,
        detected_at=detected_at,
        thesis_category=thesis_category,
        signals=signals,
        total_signal_count=total_signal_count,
        thesis_rationale=thesis_rationale,
        audit_history=audit_history,
    )

    return BaseResponse(data=detail)


# =============================================================================
# ACTION ENDPOINTS
# =============================================================================

@router.post(
    "/{review_id}/approve",
    response_model=BaseResponse[TriageActionResponse],
)
async def approve_review(
    review_id: int,
    body: TriageActionRequest,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.TRIAGE_APPROVE)
    ),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
):
    """Approve a pending review item."""
    return await _execute_triage_action(
        request, review_id, "approve", body, operator, idempotency_key,
    )


@router.post(
    "/{review_id}/reject",
    response_model=BaseResponse[TriageActionResponse],
)
async def reject_review(
    review_id: int,
    body: TriageActionRequest,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.TRIAGE_REJECT)
    ),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
):
    """Reject a pending review item."""
    return await _execute_triage_action(
        request, review_id, "reject", body, operator, idempotency_key,
    )


@router.post(
    "/{review_id}/defer",
    response_model=BaseResponse[TriageActionResponse],
)
async def defer_review(
    review_id: int,
    body: TriageActionRequest,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.TRIAGE_DEFER)
    ),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
):
    """Defer a pending review item for later evaluation."""
    return await _execute_triage_action(
        request, review_id, "defer", body, operator, idempotency_key,
    )


# =============================================================================
# ACH ENDPOINTS (A7: GET read-only, POST rebuild with side-effect)
# =============================================================================

@router.get(
    "/{review_id}/ach",
    response_model=BaseResponse[dict],
)
async def get_triage_ach(
    review_id: int,
    request: Request,
    _operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Read-only: return cached ACH analysis for review, or 404 if none."""
    store = request.app.state.store
    db = store._db

    # Resolve review → company_id
    cursor = await db.execute(
        "SELECT company_id FROM review_items WHERE id = ?",
        (review_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise error_response(
            404, "not_found", "REVIEW_NOT_FOUND",
            f"Review {review_id} not found",
        )
    company_id = row[0]

    from intelligence.ach_matrix import get_latest_ach

    cached = await get_latest_ach(db, company_id)
    if not cached:
        raise error_response(
            404, "not_found", "ACH_NOT_FOUND",
            f"No ACH analysis found for review {review_id}",
        )

    return BaseResponse(data=cached)


@router.post(
    "/{review_id}/ach/rebuild",
    response_model=BaseResponse[dict],
)
async def rebuild_triage_ach(
    review_id: int,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.TRIAGE_APPROVE)
    ),
):
    """Build fresh ACH analysis, store, return result.

    Idempotent via inputs_hash unique constraint — concurrent rebuilds
    with same DB state produce same result (INSERT OR IGNORE).
    """
    store = request.app.state.store
    db = store._db

    # Resolve review → company_id
    cursor = await db.execute(
        "SELECT company_id FROM review_items WHERE id = ?",
        (review_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise error_response(
            404, "not_found", "REVIEW_NOT_FOUND",
            f"Review {review_id} not found",
        )
    company_id = row[0]

    from intelligence.ach_matrix import ACHBuilder, store_ach_analysis, update_ach_narratives
    from intelligence.tribunal import narrate_summary

    builder = ACHBuilder()
    matrix = await builder.build(company_id, db)

    # Store matrix
    ach_id = await store_ach_analysis(db, matrix, review_id=review_id)

    # Generate narratives and update
    summary = narrate_summary(matrix)
    await update_ach_narratives(
        db, ach_id,
        bull_summary=summary.bull_summary,
        bear_summary=summary.bear_summary,
        differentiator_count=summary.differentiator_count,
    )

    logger.info(
        "ACH rebuild: review %d, company %s, top=%s (%.1f), %d differentiators",
        review_id, company_id, summary.top_hypothesis,
        summary.top_score or 0, summary.differentiator_count,
    )

    return BaseResponse(data={
        "ach_id": ach_id,
        "company_id": company_id,
        "review_id": review_id,
        "top_hypothesis": summary.top_hypothesis,
        "top_score": summary.top_score,
        "bull_summary": summary.bull_summary,
        "bear_summary": summary.bear_summary,
        "differentiator_count": summary.differentiator_count,
        "inputs_hash": matrix.inputs_hash,
        "builder_version": matrix.builder_version,
        "rubric_version": matrix.rubric_version,
        "evidence_count": matrix.evidence_count,
    })
