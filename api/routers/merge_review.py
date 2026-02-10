"""
Merge Review Router — Shadow runs, merge suggestion, and merge proposal endpoints.

Endpoints:
- GET /entities/merge-suggestions — Paginated list (Permission.VIEW)
- GET /entities/merge-suggestions/{id} — Detail with blast radius (Permission.ENTITY_MERGE)
- GET /entities/shadow-runs — Paginated shadow run list (Permission.VIEW)
- GET /entities/merge-proposals — Paginated proposal list (Permission.VIEW)
- POST /entities/merge-suggestions/{id}/propose — Create proposal (Permission.ENTITY_MERGE)
- POST /entities/merge-proposals/{id}/approve — Approve proposal (Permission.ENTITY_MERGE)
- POST /entities/merge-proposals/{id}/apply — Apply merge cascade (Permission.ENTITY_MERGE)
- POST /entities/merge-proposals/{id}/rollback — Rollback applied merge (Permission.ENTITY_MERGE)

All endpoints use cursor pagination: default limit=50, max limit=200,
sort key (created_at DESC, id DESC).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import (
    BaseResponse,
    ListMeta,
    ListResponse,
    check_version,
    error_response,
    feature_disabled_response,
    get_idempotency_key,
)
from api.models.merge import (
    ApplyResponse,
    ApproveRequest,
    BlastRadius,
    MergeProposalSummary,
    MergeSuggestionDetail,
    MergeSuggestionSummary,
    ProposeRequest,
    ProposeResponse,
    RollbackRequest,
    RollbackResponse,
    ShadowRunSummary,
)
from api.pagination import decode_cursor, encode_cursor
from workflows.feature_guards import (
    FeatureDisabledError,
    WriteFeature,
    WriteMode,
    assert_write_enabled,
)

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


# =============================================================================
# MERGE PROPOSALS — Write Lifecycle (Wave 4)
# =============================================================================

# Default rollback TTL: 24 hours
_DEFAULT_ROLLBACK_TTL_HOURS = 24

# Snapshot schema version for before/after captures
_SNAPSHOT_SCHEMA_VERSION = 1


async def _capture_entity_snapshot(tx, company_id: str) -> dict:
    """Capture current entity state for rollback.

    Collects signal IDs, active review evidence, and company file metadata
    into a dict suitable for JSON serialization.

    Args:
        tx: Active database transaction connection.
        company_id: The company to snapshot.

    Returns:
        Dict with company_id, signal_ids, review info, and company_file data.
    """
    # Signal IDs
    cursor = await tx.execute(
        "SELECT id FROM signals WHERE company_id = ?", (company_id,)
    )
    signal_ids = [row[0] for row in await cursor.fetchall()]

    # Active review
    cursor = await tx.execute(
        """SELECT id, status, evidence_bundle FROM review_items
           WHERE company_id = ? AND status IN ('pending','approved','publish_queued')
           ORDER BY updated_at DESC LIMIT 1""",
        (company_id,),
    )
    review = await cursor.fetchone()
    review_data = {}
    if review:
        bundle = json.loads(review[2]) if review[2] else {}
        review_data = {
            "review_id": review[0],
            "review_status": review[1],
            "review_evidence_signal_ids": bundle.get("signal_ids", []),
        }

    # Company file
    cursor = await tx.execute(
        "SELECT source_apis, first_seen_at, last_seen_at FROM company_files WHERE company_id = ?",
        (company_id,),
    )
    file_row = await cursor.fetchone()
    file_data = None
    if file_row:
        file_data = {
            "source_apis": json.loads(file_row[0]) if file_row[0] else [],
            "first_seen_at": file_row[1],
            "last_seen_at": file_row[2],
        }

    return {
        "company_id": company_id,
        "signal_ids": signal_ids,
        **review_data,
        "company_file": file_data,
    }


async def _fetch_proposal(db, proposal_id: int) -> Optional[dict]:
    """Fetch a merge proposal row as a dict. Returns None if not found."""
    cursor = await db.execute(
        """SELECT id, suggestion_id, entity_a_company_id, entity_b_company_id,
                  winner_company_id, loser_company_id, status, reason,
                  proposed_by, proposed_at, approved_by, approved_at,
                  applied_at, rolled_back_at, rollback_reason,
                  before_snapshot, after_snapshot, target_version_snapshot,
                  cascade_report, correlation_id, updated_at
           FROM merge_proposals WHERE id = ?""",
        (proposal_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "suggestion_id": row[1],
        "entity_a_company_id": row[2],
        "entity_b_company_id": row[3],
        "winner_company_id": row[4],
        "loser_company_id": row[5],
        "status": row[6],
        "reason": row[7],
        "proposed_by": row[8],
        "proposed_at": row[9],
        "approved_by": row[10],
        "approved_at": row[11],
        "applied_at": row[12],
        "rolled_back_at": row[13],
        "rollback_reason": row[14],
        "before_snapshot": row[15],
        "after_snapshot": row[16],
        "target_version_snapshot": row[17],
        "cascade_report": row[18],
        "correlation_id": row[19],
        "updated_at": row[20],
    }


def _row_to_proposal_summary(row) -> MergeProposalSummary:
    """Convert a raw DB row tuple to a MergeProposalSummary DTO."""
    return MergeProposalSummary(
        id=row[0],
        suggestion_id=row[1],
        entity_a_company_id=row[2],
        entity_b_company_id=row[3],
        winner_company_id=row[4],
        loser_company_id=row[5],
        status=row[6],
        reason=row[7],
        proposed_by=row[8],
        proposed_at=row[9],
        approved_by=row[10],
        approved_at=row[11],
        applied_at=row[12],
        rolled_back_at=row[13],
        rollback_reason=row[14],
        correlation_id=row[15],
        updated_at=row[16],
    )


# ---- GET /entities/merge-proposals ----

@router.get("/merge-proposals")
async def list_merge_proposals(
    request: Request,
    status: Optional[str] = Query(default=None, description="Filter by proposal status"),
    cursor: Optional[str] = Query(default=None, description="Pagination cursor"),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List merge proposals with optional status filter and cursor pagination."""
    store = request.app.state.store
    db = store._db

    cursor_data = decode_cursor(cursor) if cursor else {}
    cursor_created_at = cursor_data.get("created_at")
    cursor_id = cursor_data.get("id")

    conditions: list[str] = []
    params: list = []

    if status:
        conditions.append("status = ?")
        params.append(status)

    if cursor_created_at and cursor_id is not None:
        conditions.append(
            "(proposed_at < ? OR (proposed_at = ? AND id < ?))"
        )
        params.extend([cursor_created_at, cursor_created_at, int(cursor_id)])

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(min(max(limit, 1), 200))

    db_cursor = await db.execute(
        f"""
        SELECT id, suggestion_id, entity_a_company_id, entity_b_company_id,
               winner_company_id, loser_company_id, status, reason,
               proposed_by, proposed_at, approved_by, approved_at,
               applied_at, rolled_back_at, rollback_reason,
               correlation_id, updated_at
        FROM merge_proposals
        WHERE {where}
        ORDER BY proposed_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = await db_cursor.fetchall()

    items = [_row_to_proposal_summary(row) for row in rows]

    next_cursor = None
    if items and len(items) == limit:
        last = rows[-1]
        # proposed_at is at index 9, id at index 0
        next_cursor = encode_cursor({"created_at": last[9], "id": str(last[0])})

    return ListResponse(
        data=items,
        meta=ListMeta(
            total=len(items),
            cursor=next_cursor,
            has_more=len(items) == limit,
        ),
    ).model_dump()


# ---- POST /entities/merge-suggestions/{id}/propose ----

@router.post("/merge-suggestions/{suggestion_id}/propose")
async def propose_merge(
    request: Request,
    suggestion_id: int,
    body: ProposeRequest,
    operator: OperatorContext = Depends(require_permission(Permission.ENTITY_MERGE)),
):
    """Create a merge proposal from a merge suggestion.

    Requires ENTITY_MERGE permission (GP only). The feature guard
    MERGE_WRITES must be in 'active' or 'shadow' mode.
    """
    try:
        assert_write_enabled(WriteFeature.MERGE_WRITES, allow_shadow=True)
    except FeatureDisabledError as exc:
        raise feature_disabled_response(exc.feature.value, exc.env_var)

    store = request.app.state.store
    db = store._db

    # Validate suggestion exists
    cursor = await db.execute(
        "SELECT id, entity_a_company_id, entity_b_company_id FROM merge_suggestions WHERE id = ?",
        (suggestion_id,),
    )
    suggestion = await cursor.fetchone()
    if not suggestion:
        raise error_response(
            404, "not_found", "SUGGESTION_NOT_FOUND",
            f"Merge suggestion {suggestion_id} not found",
        )

    # Validate body.suggestion_id matches path param
    if body.suggestion_id != suggestion_id:
        raise error_response(
            400, "bad_request", "SUGGESTION_ID_MISMATCH",
            f"Body suggestion_id ({body.suggestion_id}) does not match path ({suggestion_id})",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    correlation_id = operator.request_id or hashlib.sha256(
        f"propose:{suggestion_id}:{now_iso}".encode()
    ).hexdigest()[:16]

    try:
        async with store.transaction_immediate() as tx:
            cursor = await tx.execute(
                """INSERT INTO merge_proposals
                   (suggestion_id, entity_a_company_id, entity_b_company_id,
                    winner_company_id, loser_company_id, status, reason,
                    proposed_by, proposed_at, correlation_id, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?)""",
                (
                    suggestion_id,
                    suggestion[1],  # entity_a_company_id
                    suggestion[2],  # entity_b_company_id
                    body.winner_company_id,
                    body.loser_company_id,
                    body.reason,
                    operator.actor_label,
                    now_iso,
                    correlation_id,
                    now_iso,
                ),
            )
            proposal_id = cursor.lastrowid
    except (sqlite3.IntegrityError, Exception) as e:
        if "UNIQUE constraint" in str(e):
            raise error_response(
                409, "conflict", "DUPLICATE_ACTIVE_PROPOSAL",
                f"An active merge proposal already exists for suggestion {suggestion_id}.",
            )
        raise

    logger.info(
        "Merge proposal %d created for suggestion %d by %s (correlation=%s)",
        proposal_id, suggestion_id, operator.actor_label, correlation_id,
    )

    return JSONResponse(
        status_code=201,
        content=BaseResponse(
            data=ProposeResponse(
                proposal_id=proposal_id,
                status="proposed",
                message=f"Merge proposal {proposal_id} created for suggestion {suggestion_id}.",
            )
        ).model_dump(),
    )


# ---- POST /entities/merge-proposals/{id}/approve ----

@router.post("/merge-proposals/{proposal_id}/approve")
async def approve_merge_proposal(
    request: Request,
    proposal_id: int,
    body: ApproveRequest,
    operator: OperatorContext = Depends(require_permission(Permission.ENTITY_MERGE)),
):
    """Approve a merge proposal, transitioning it from 'proposed' to 'approved'.

    Uses optimistic concurrency via updated_at in the request body.
    """
    try:
        assert_write_enabled(WriteFeature.MERGE_WRITES, allow_shadow=True)
    except FeatureDisabledError as exc:
        raise feature_disabled_response(exc.feature.value, exc.env_var)

    store = request.app.state.store
    db = store._db

    proposal = await _fetch_proposal(db, proposal_id)
    if not proposal:
        raise error_response(
            404, "not_found", "PROPOSAL_NOT_FOUND",
            f"Merge proposal {proposal_id} not found",
        )

    if proposal["status"] != "proposed":
        raise error_response(
            409, "conflict", "INVALID_TRANSITION",
            f"Cannot approve proposal in status '{proposal['status']}'. "
            "Only proposals in 'proposed' status can be approved.",
            detail={"current_status": proposal["status"]},
        )

    # Optimistic concurrency check
    check_version(body.updated_at, proposal["updated_at"], "merge_proposal")

    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        await tx.execute(
            """UPDATE merge_proposals
               SET status = 'approved',
                   approved_by = ?,
                   approved_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (operator.actor_label, now_iso, now_iso, proposal_id),
        )

    logger.info(
        "Merge proposal %d approved by %s", proposal_id, operator.actor_label,
    )

    return BaseResponse(
        data=ProposeResponse(
            proposal_id=proposal_id,
            status="approved",
            message=f"Merge proposal {proposal_id} approved.",
        )
    ).model_dump()


# ---- POST /entities/merge-proposals/{id}/apply ----

@router.post("/merge-proposals/{proposal_id}/apply")
async def apply_merge_proposal(
    request: Request,
    proposal_id: int,
    operator: OperatorContext = Depends(require_permission(Permission.ENTITY_MERGE)),
):
    """Apply an approved merge proposal, executing the cascade merge.

    In shadow mode (MERGE_WRITES_ENABLED=shadow), returns the merge plan
    without executing the cascade. In active mode, performs the full cascade
    merge with before/after snapshots for rollback support.
    """
    from storage.audit_events import record_event_from_context
    from storage.merge_cascade import cascade_merge
    from storage.merge_rollback import compute_entity_fingerprint

    try:
        mode = assert_write_enabled(WriteFeature.MERGE_WRITES)
    except FeatureDisabledError as exc:
        raise feature_disabled_response(exc.feature.value, exc.env_var)

    store = request.app.state.store
    db = store._db

    proposal = await _fetch_proposal(db, proposal_id)
    if not proposal:
        raise error_response(
            404, "not_found", "PROPOSAL_NOT_FOUND",
            f"Merge proposal {proposal_id} not found",
        )

    if proposal["status"] != "approved":
        raise error_response(
            409, "conflict", "INVALID_TRANSITION",
            f"Cannot apply proposal in status '{proposal['status']}'. "
            "Only proposals in 'approved' status can be applied.",
            detail={"current_status": proposal["status"]},
        )

    winner_id = proposal["winner_company_id"]
    loser_id = proposal["loser_company_id"]

    # ---- Shadow mode: return plan without executing ----
    if mode == WriteMode.SHADOW:
        logger.info(
            "Merge proposal %d apply in SHADOW mode — returning plan only",
            proposal_id,
        )
        plan = {
            "proposal_id": proposal_id,
            "winner_company_id": winner_id,
            "loser_company_id": loser_id,
            "reason": proposal["reason"],
            "action": "cascade_merge",
            "tables_affected": ["signals", "review_items", "company_files", "audit_log"],
        }
        return JSONResponse(
            status_code=200,
            content=BaseResponse(
                data=ApplyResponse(
                    proposal_id=proposal_id,
                    status="shadow",
                    cascade_report=plan,
                    shadow_mode=True,
                    message="Shadow mode: merge plan computed but NOT executed.",
                )
            ).model_dump(),
        )

    # ---- Active mode: execute cascade merge ----
    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        # Capture before snapshots for both entities
        winner_snapshot = await _capture_entity_snapshot(tx, winner_id)
        loser_snapshot = await _capture_entity_snapshot(tx, loser_id)

        # Entity migration data (for rollback to delete the migration row)
        entity_migration_data = {
            "from_entity_id": loser_id,
            "to_entity_id": winner_id,
        }

        before_snapshot = {
            "snapshot_schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "winner": winner_snapshot,
            "loser": loser_snapshot,
            "entity_migration": entity_migration_data,
        }

        # Execute cascade merge
        cascade_report = await cascade_merge(
            store,
            winner_id,
            loser_id,
            proposal["reason"] or "Merge proposal applied",
            operator.actor_label,
            tx,
        )

        # Capture after snapshot and target version AFTER merge
        # (target_version represents the expected post-merge state for drift detection)
        winner_after = await _capture_entity_snapshot(tx, winner_id)
        target_version = await compute_entity_fingerprint(tx, winner_id)
        after_snapshot = {
            "snapshot_schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "winner": winner_after,
            "cascade_report": cascade_report,
        }

        # Transition to 'applied'
        await tx.execute(
            """UPDATE merge_proposals
               SET status = 'applied',
                   applied_at = ?,
                   before_snapshot = ?,
                   after_snapshot = ?,
                   target_version_snapshot = ?,
                   cascade_report = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                now_iso,
                json.dumps(before_snapshot, default=str),
                json.dumps(after_snapshot, default=str),
                target_version,
                json.dumps(cascade_report, default=str),
                now_iso,
                proposal_id,
            ),
        )

    # Emit audit event (outside transaction to avoid nesting)
    try:
        await record_event_from_context(
            store,
            action_type="merge_apply",
            entity_type="company",
            entity_id=winner_id,
            operator=operator,
            before_state=before_snapshot,
            after_state=after_snapshot,
            reason=proposal["reason"],
            metadata={
                "proposal_id": proposal_id,
                "loser_company_id": loser_id,
                "cascade_report": cascade_report,
            },
        )
    except Exception as e:
        logger.warning("Failed to emit audit event for merge apply: %s", e)

    logger.info(
        "Merge proposal %d applied: %s -> %s (%d signals reassigned)",
        proposal_id, loser_id, winner_id,
        cascade_report.get("signals_reassigned", 0),
    )

    return JSONResponse(
        status_code=201,
        content=BaseResponse(
            data=ApplyResponse(
                proposal_id=proposal_id,
                status="applied",
                cascade_report=cascade_report,
                shadow_mode=False,
                message=f"Merge proposal {proposal_id} applied successfully.",
            )
        ).model_dump(),
    )


# ---- POST /entities/merge-proposals/{id}/rollback ----

@router.post("/merge-proposals/{proposal_id}/rollback")
async def rollback_merge_proposal(
    request: Request,
    proposal_id: int,
    body: RollbackRequest,
    operator: OperatorContext = Depends(require_permission(Permission.ENTITY_MERGE)),
):
    """Rollback an applied merge proposal, reversing the cascade merge.

    Three safety gates must pass before rollback proceeds:
    1. TTL gate: Must be within MERGE_ROLLBACK_TTL_HOURS (default 24h)
    2. LIFO gate: No subsequent applied merges involving either entity
    3. Drift gate: Winner entity fingerprint must match post-apply snapshot
    """
    from storage.audit_events import record_event_from_context
    from storage.merge_rollback import (
        compute_entity_fingerprint,
        reverse_cascade,
    )

    try:
        assert_write_enabled(WriteFeature.MERGE_WRITES)
    except FeatureDisabledError as exc:
        raise feature_disabled_response(exc.feature.value, exc.env_var)

    store = request.app.state.store
    db = store._db

    proposal = await _fetch_proposal(db, proposal_id)
    if not proposal:
        raise error_response(
            404, "not_found", "PROPOSAL_NOT_FOUND",
            f"Merge proposal {proposal_id} not found",
        )

    if proposal["status"] != "applied":
        raise error_response(
            409, "conflict", "INVALID_TRANSITION",
            f"Cannot rollback proposal in status '{proposal['status']}'. "
            "Only proposals in 'applied' status can be rolled back.",
            detail={"current_status": proposal["status"]},
        )

    # Optimistic concurrency check
    check_version(body.updated_at, proposal["updated_at"], "merge_proposal")

    winner_id = proposal["winner_company_id"]
    loser_id = proposal["loser_company_id"]
    applied_at = proposal["applied_at"]

    # ---- Gate 1: TTL ----
    ttl_hours = int(os.environ.get("MERGE_ROLLBACK_TTL_HOURS", _DEFAULT_ROLLBACK_TTL_HOURS))
    if applied_at:
        try:
            applied_dt = datetime.fromisoformat(applied_at)
            # Ensure applied_dt is timezone-aware
            if applied_dt.tzinfo is None:
                applied_dt = applied_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - applied_dt).total_seconds() / 3600
            if age_hours > ttl_hours:
                raise error_response(
                    409, "conflict", "ROLLBACK_WINDOW_EXPIRED",
                    f"Rollback window expired. Merge was applied {age_hours:.1f}h ago "
                    f"(max: {ttl_hours}h).",
                    detail={
                        "applied_at": applied_at,
                        "ttl_hours": ttl_hours,
                        "age_hours": round(age_hours, 2),
                    },
                )
        except (ValueError, TypeError) as e:
            logger.warning("Could not parse applied_at for TTL check: %s", e)

    # ---- Gate 2: LIFO (no subsequent merges for either entity) ----
    cursor = await db.execute(
        """SELECT id FROM merge_proposals
           WHERE status = 'applied'
           AND applied_at > ?
           AND (winner_company_id IN (?, ?) OR loser_company_id IN (?, ?))
           AND id != ?
           LIMIT 1""",
        (applied_at, winner_id, loser_id, winner_id, loser_id, proposal_id),
    )
    subsequent = await cursor.fetchone()
    if subsequent:
        raise error_response(
            409, "conflict", "ROLLBACK_SUBSEQUENT_MERGE_EXISTS",
            f"Cannot rollback: a subsequent merge (proposal {subsequent[0]}) "
            "was applied involving one of the entities. "
            "Rollback the newer merge first (LIFO order).",
            detail={"blocking_proposal_id": subsequent[0]},
        )

    # ---- Gate 3: Drift detection ----
    target_version = proposal.get("target_version_snapshot")
    if target_version:
        current_fingerprint = await compute_entity_fingerprint(db, winner_id)
        if current_fingerprint != target_version:
            raise error_response(
                409, "conflict", "ROLLBACK_ENTITY_DRIFTED",
                "Cannot rollback: the winner entity has been modified since the merge "
                "was applied. Manual intervention required.",
                detail={
                    "expected_fingerprint": target_version,
                    "current_fingerprint": current_fingerprint,
                },
            )

    # ---- All gates passed: execute reverse cascade ----
    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        rollback_report = await reverse_cascade(store, proposal, operator.actor_label, tx)

        # Transition to 'rolled_back'
        await tx.execute(
            """UPDATE merge_proposals
               SET status = 'rolled_back',
                   rolled_back_at = ?,
                   rollback_reason = ?,
                   updated_at = ?
               WHERE id = ?""",
            (now_iso, body.reason, now_iso, proposal_id),
        )

    # Emit audit event (outside transaction)
    try:
        await record_event_from_context(
            store,
            action_type="merge_rollback",
            entity_type="company",
            entity_id=winner_id,
            operator=operator,
            before_state={"status": "applied", "proposal_id": proposal_id},
            after_state={"status": "rolled_back", "rollback_report": rollback_report},
            reason=body.reason,
            metadata={
                "proposal_id": proposal_id,
                "loser_company_id": loser_id,
                "rollback_report": rollback_report,
            },
        )
    except Exception as e:
        logger.warning("Failed to emit audit event for merge rollback: %s", e)

    logger.info(
        "Merge proposal %d rolled back by %s: %s <- %s",
        proposal_id, operator.actor_label, winner_id, loser_id,
    )

    return BaseResponse(
        data=RollbackResponse(
            proposal_id=proposal_id,
            status="rolled_back",
            rollback_report=rollback_report,
            message=f"Merge proposal {proposal_id} rolled back successfully.",
        )
    ).model_dump()
