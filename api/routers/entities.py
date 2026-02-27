"""
Entities Router

Unified entity management endpoints:
- GET /entities - List with filters, pagination
- GET /entities/{key} - Full entity with building blocks
- PATCH /entities/{key} - Update stage, owner, notes
- GET /entities/{key}/snapshots - History with diffs
- GET /entities/{key}/alerts - Pending alerts for entity
- POST /entities/{key}/alerts/{alert_id}/resolve - Resolve an alert

Consolidates companies.py patterns with new Command Center features.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from pydantic import BaseModel

from api.auth.jwt_auth import get_current_user, require_role, User, Role
from api.db import execute_write_with_version, OptimisticLockError, handle_optimistic_lock_error, write_transaction, get_store
from storage.signal_store import SignalStore, EntityStage, EntitySnapshot, EntityAlert

router = APIRouter(prefix="/entities", tags=["entities"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class EntitySummary(BaseModel):
    """Entity summary for list view."""
    canonical_key: str
    company_name: Optional[str]
    website: Optional[str]
    stage: str
    owner: Optional[str]
    max_confidence: float
    signal_count: int
    sources: str
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    thesis_fit_score: Optional[float]
    vertical: Optional[str]
    next_step: Optional[str]
    due_date: Optional[str]
    has_alerts: bool = False


class EntityDetail(EntitySummary):
    """Detailed entity information."""
    one_liner: Optional[str]
    description: Optional[str]
    notes: Optional[str]
    notion_page_id: Optional[str]
    notion_synced: bool
    building_blocks: List[Dict[str, Any]] = []
    recent_signals: List[Dict[str, Any]] = []
    relationship_badge: Optional[str] = None
    exit_prediction_score: Optional[float] = None


class EntityListResponse(BaseModel):
    """Paginated entity list."""
    entities: List[EntitySummary]
    total: int
    page: int
    page_size: int
    stages: Dict[str, int]  # Count by stage


class StageUpdateRequest(BaseModel):
    """Request to update entity stage."""
    stage: str
    owner: Optional[str] = None
    notes: Optional[str] = None
    next_step: Optional[str] = None
    due_date: Optional[str] = None
    reason: Optional[str] = None


class StageUpdateResponse(BaseModel):
    """Response from stage update."""
    success: bool
    entity_key: str
    old_stage: Optional[str]
    new_stage: str
    version: int
    message: str


class SnapshotResponse(BaseModel):
    """Entity snapshot."""
    id: str
    source: str
    url: Optional[str]
    captured_at: Optional[datetime]
    significance_score: float
    diff_summary: Optional[str]
    has_content: bool


class AlertResponse(BaseModel):
    """Entity alert."""
    id: str
    alert_type: str
    severity: str
    summary: str
    status: str
    created_at: Optional[datetime]
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]


class ResolveAlertRequest(BaseModel):
    """Request to resolve an alert."""
    action: str  # 'accept', 'reject', 'snooze'
    snooze_until: Optional[datetime] = None
    reason: Optional[str] = None


# =============================================================================
# VALID STAGES
# =============================================================================

VALID_STAGES = [
    "Inbox",
    "Tracking",
    "Review",
    "Meeting",
    "Diligence",
    "IC",
    "Won",
    "Lost",
    "Passed",
]


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("", response_model=EntityListResponse)
async def list_entities(
    stage: Optional[str] = Query(None, description="Filter by stage"),
    owner: Optional[str] = Query(None, description="Filter by owner"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    search: Optional[str] = Query(None, description="Search company name"),
    has_alerts: Optional[bool] = Query(None, description="Filter to entities with pending alerts"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_user),
    store: SignalStore = Depends(get_store),
):
    """
    List entities with filtering and pagination.

    Supports filtering by stage, owner, confidence, and search.
    Returns counts by stage for the sidebar.
    """
    offset = (page - 1) * page_size
    db = await store._get_db()

    # Build query
    query = """
        SELECT
            s.canonical_key,
            s.company_name,
            MAX(s.confidence) as max_confidence,
            COUNT(*) as signal_count,
            GROUP_CONCAT(DISTINCT s.source_api) as sources,
            MIN(s.created_at) as first_seen,
            MAX(s.created_at) as last_seen,
            es.stage,
            es.owner,
            es.next_step,
            es.due_date,
            es.notes,
            es.notion_synced,
            es.notion_page_id,
            tc.thesis_fit_score,
            tc.category as vertical,
            (SELECT COUNT(*) FROM entity_alerts ea WHERE ea.entity_key = s.canonical_key AND ea.status = 'pending') as alert_count
        FROM signals s
        LEFT JOIN entity_stages es ON s.canonical_key = es.entity_key
        LEFT JOIN thesis_classifications tc ON s.id = tc.signal_id
        WHERE 1=1
    """
    params: List[Any] = []

    if stage:
        query += " AND es.stage = ?"
        params.append(stage)

    if owner:
        query += " AND es.owner = ?"
        params.append(owner)

    if search:
        query += " AND s.company_name LIKE ?"
        params.append(f"%{search}%")

    if has_alerts:
        query += " AND (SELECT COUNT(*) FROM entity_alerts ea WHERE ea.entity_key = s.canonical_key AND ea.status = 'pending') > 0"

    query += """
        GROUP BY s.canonical_key
        HAVING max_confidence >= ?
        ORDER BY max_confidence DESC
        LIMIT ? OFFSET ?
    """
    params.extend([min_confidence, page_size, offset])

    cursor = await db.execute(query, params)
    entities = []

    async for row in cursor:
        entities.append(EntitySummary(
            canonical_key=row[0],
            company_name=row[1],
            website=_extract_website(row[0]),
            stage=row[7] or "Inbox",
            owner=row[8],
            max_confidence=row[2] or 0.0,
            signal_count=row[3] or 0,
            sources=row[4] or "",
            first_seen=_parse_datetime(row[5]),
            last_seen=_parse_datetime(row[6]),
            thesis_fit_score=row[14],
            vertical=row[15],
            next_step=row[9],
            due_date=row[10],
            has_alerts=(row[16] or 0) > 0,
        ))

    # Count total matching entities (same filters, no LIMIT/OFFSET)
    count_query = """
        SELECT COUNT(*) FROM (
            SELECT s.canonical_key
            FROM signals s
            LEFT JOIN entity_stages es ON s.canonical_key = es.entity_key
            LEFT JOIN thesis_classifications tc ON s.id = tc.signal_id
            WHERE 1=1
    """
    count_params: List[Any] = []

    if stage:
        count_query += " AND es.stage = ?"
        count_params.append(stage)
    if owner:
        count_query += " AND es.owner = ?"
        count_params.append(owner)
    if search:
        count_query += " AND s.company_name LIKE ?"
        count_params.append(f"%{search}%")
    if has_alerts:
        count_query += " AND (SELECT COUNT(*) FROM entity_alerts ea WHERE ea.entity_key = s.canonical_key AND ea.status = 'pending') > 0"

    count_query += """
            GROUP BY s.canonical_key
            HAVING MAX(s.confidence) >= ?
        )
    """
    count_params.append(min_confidence)

    cursor = await db.execute(count_query, count_params)
    total = (await cursor.fetchone())[0]

    # Get stage counts
    cursor = await db.execute("""
        SELECT COALESCE(es.stage, 'Inbox') as stage, COUNT(DISTINCT s.canonical_key)
        FROM signals s
        LEFT JOIN entity_stages es ON s.canonical_key = es.entity_key
        GROUP BY COALESCE(es.stage, 'Inbox')
    """)
    stages = {row[0]: row[1] async for row in cursor}

    return EntityListResponse(
        entities=entities,
        total=total,
        page=page,
        page_size=page_size,
        stages=stages,
    )


@router.get("/{entity_key:path}", response_model=EntityDetail)
async def get_entity(
    entity_key: str,
    user: User = Depends(get_current_user),
    store: SignalStore = Depends(get_store),
):
    """
    Get detailed entity information.

    Includes building blocks, recent signals, and relationship data.
    """
    db = await store._get_db()

    # Get entity summary
    cursor = await db.execute("""
        SELECT
            s.canonical_key,
            s.company_name,
            MAX(s.confidence) as max_confidence,
            COUNT(*) as signal_count,
            GROUP_CONCAT(DISTINCT s.source_api) as sources,
            MIN(s.created_at) as first_seen,
            MAX(s.created_at) as last_seen
        FROM signals s
        WHERE s.canonical_key = ?
        GROUP BY s.canonical_key
    """, (entity_key,))
    row = await cursor.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_key}",
        )

    # Get stage info
    cursor = await db.execute("""
        SELECT stage, owner, notes, next_step, due_date, notion_synced, notion_page_id
        FROM entity_stages
        WHERE entity_key = ?
    """, (entity_key,))
    stage_row = await cursor.fetchone()

    # Get thesis classification
    cursor = await db.execute("""
        SELECT thesis_fit_score, category, rationale
        FROM thesis_classifications
        WHERE canonical_key = ?
        ORDER BY classified_at DESC
        LIMIT 1
    """, (entity_key,))
    thesis_row = await cursor.fetchone()

    # Get building blocks (from claims table)
    building_blocks = []
    cursor = await db.execute("""
        SELECT predicate, value, confidence
        FROM claims
        WHERE entity_key = ? AND status = 'active'
        ORDER BY confidence DESC
    """, (entity_key,))
    async for claim_row in cursor:
        building_blocks.append({
            "type": claim_row[0],
            "value": claim_row[1],
            "confidence": claim_row[2],
        })

    # Get recent signals
    cursor = await db.execute("""
        SELECT id, signal_type, source_api, confidence, created_at, raw_data
        FROM signals
        WHERE canonical_key = ?
        ORDER BY created_at DESC
        LIMIT 10
    """, (entity_key,))
    recent_signals = []
    async for sig_row in cursor:
        recent_signals.append({
            "id": sig_row[0],
            "type": sig_row[1],
            "source": sig_row[2],
            "confidence": sig_row[3],
            "created_at": sig_row[4],
        })

    # Get relationship badge (if warm intro boost is available)
    relationship_badge = None
    try:
        cursor = await db.execute("""
            SELECT warmth_score, badge
            FROM warm_intro_boosts
            WHERE entity_key = ?
        """, (entity_key,))
        rel_row = await cursor.fetchone()
        if rel_row:
            relationship_badge = rel_row[1]
    except Exception:
        pass  # Table may not exist

    # Get exit prediction
    exit_score = None
    try:
        cursor = await db.execute("""
            SELECT deal_quality_score
            FROM exit_predictions
            WHERE canonical_key = ?
        """, (entity_key,))
        exit_row = await cursor.fetchone()
        if exit_row:
            exit_score = exit_row[0]
    except Exception:
        pass

    # Check for alerts
    cursor = await db.execute("""
        SELECT COUNT(*) FROM entity_alerts
        WHERE entity_key = ? AND status = 'pending'
    """, (entity_key,))
    alert_count = (await cursor.fetchone())[0]

    return EntityDetail(
        canonical_key=row[0],
        company_name=row[1],
        website=_extract_website(row[0]),
        stage=stage_row[0] if stage_row else "Inbox",
        owner=stage_row[1] if stage_row else None,
        max_confidence=row[2] or 0.0,
        signal_count=row[3] or 0,
        sources=row[4] or "",
        first_seen=_parse_datetime(row[5]),
        last_seen=_parse_datetime(row[6]),
        thesis_fit_score=thesis_row[0] if thesis_row else None,
        vertical=thesis_row[1] if thesis_row else None,
        one_liner=thesis_row[2] if thesis_row else None,
        description=None,  # TODO: Extract from raw_data
        notes=stage_row[2] if stage_row else None,
        next_step=stage_row[3] if stage_row else None,
        due_date=stage_row[4] if stage_row else None,
        notion_page_id=stage_row[6] if stage_row else None,
        notion_synced=bool(stage_row[5]) if stage_row else False,
        building_blocks=building_blocks,
        recent_signals=recent_signals,
        relationship_badge=relationship_badge,
        exit_prediction_score=exit_score,
        has_alerts=alert_count > 0,
    )


@router.patch("/{entity_key:path}", response_model=StageUpdateResponse)
async def update_entity_stage(
    entity_key: str,
    request: Request,
    update: StageUpdateRequest,
    user: User = Depends(require_role([Role.GP, Role.ANALYST])),
    store: SignalStore = Depends(get_store),
):
    """
    Update entity stage, owner, or notes.

    Uses optimistic locking to prevent concurrent update conflicts.
    """
    if update.stage not in VALID_STAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stage. Must be one of: {VALID_STAGES}",
        )

    db = await store._get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Get current stage
    cursor = await db.execute("""
        SELECT stage, owner, _version FROM entity_stages WHERE entity_key = ?
    """, (entity_key,))
    current = await cursor.fetchone()

    old_stage = None
    current_version = 0

    if current:
        old_stage = current[0]
        current_version = current[2]

        # Update with optimistic locking
        try:
            async with write_transaction(request):
                cursor = await db.execute("""
                    UPDATE entity_stages
                    SET stage = ?,
                        owner = COALESCE(?, owner),
                        notes = COALESCE(?, notes),
                        next_step = COALESCE(?, next_step),
                        due_date = COALESCE(?, due_date),
                        changed_by = ?,
                        changed_at = ?,
                        _version = _version + 1,
                        notion_synced = 0
                    WHERE entity_key = ? AND _version = ?
                """, (
                    update.stage,
                    update.owner,
                    update.notes,
                    update.next_step,
                    update.due_date,
                    user.email,
                    now,
                    entity_key,
                    current_version,
                ))
                await db.commit()

                # Check if update happened (cursor.rowcount is statement-scoped)
                if cursor.rowcount == 0:
                    raise OptimisticLockError()

        except OptimisticLockError:
            handle_optimistic_lock_error(OptimisticLockError())
    else:
        # Insert new stage record
        stage_id = str(uuid.uuid4())
        async with write_transaction(request):
            await db.execute("""
                INSERT INTO entity_stages
                (id, entity_key, stage, owner, notes, next_step, due_date, changed_by, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stage_id,
                entity_key,
                update.stage,
                update.owner,
                update.notes,
                update.next_step,
                update.due_date,
                user.email,
                now,
            ))
            await db.commit()

    # Record stage change in history
    await db.execute("""
        INSERT INTO entity_stage_history
        (entity_key, old_stage, new_stage, old_owner, new_owner, reason, changed_by, changed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entity_key,
        old_stage,
        update.stage,
        current[1] if current else None,
        update.owner,
        update.reason,
        user.email,
        now,
    ))
    await db.commit()

    return StageUpdateResponse(
        success=True,
        entity_key=entity_key,
        old_stage=old_stage,
        new_stage=update.stage,
        version=current_version + 1,
        message=f"Stage updated to {update.stage}",
    )


@router.get("/{entity_key:path}/snapshots", response_model=List[SnapshotResponse])
async def get_entity_snapshots(
    entity_key: str,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    store: SignalStore = Depends(get_store),
):
    """
    Get snapshot history for an entity.

    Shows chronological history of captured states with diff summaries.
    """
    db = await store._get_db()

    cursor = await db.execute("""
        SELECT id, source, url, captured_at, significance_score, diff_summary, content_hash
        FROM entity_snapshots
        WHERE entity_key = ?
        ORDER BY captured_at DESC
        LIMIT ?
    """, (entity_key, limit))

    snapshots = []
    async for row in cursor:
        snapshots.append(SnapshotResponse(
            id=row[0],
            source=row[1],
            url=row[2],
            captured_at=_parse_datetime(row[3]),
            significance_score=row[4] or 0.0,
            diff_summary=row[5],
            has_content=bool(row[6]),
        ))

    return snapshots


@router.get("/{entity_key:path}/alerts", response_model=List[AlertResponse])
async def get_entity_alerts(
    entity_key: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    user: User = Depends(get_current_user),
    store: SignalStore = Depends(get_store),
):
    """
    Get alerts for an entity.

    Returns pending alerts by default, or all if status filter is provided.
    """
    db = await store._get_db()

    query = """
        SELECT id, alert_type, severity, summary, status, created_at, reviewed_by, reviewed_at
        FROM entity_alerts
        WHERE entity_key = ?
    """
    params = [entity_key]

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    else:
        query += " AND status = 'pending'"

    query += " ORDER BY created_at DESC"

    cursor = await db.execute(query, params)

    alerts = []
    async for row in cursor:
        alerts.append(AlertResponse(
            id=row[0],
            alert_type=row[1],
            severity=row[2],
            summary=row[3],
            status=row[4],
            created_at=_parse_datetime(row[5]),
            reviewed_by=row[6],
            reviewed_at=_parse_datetime(row[7]),
        ))

    return alerts


@router.post("/{entity_key:path}/alerts/{alert_id}/resolve")
async def resolve_alert(
    entity_key: str,
    alert_id: str,
    request: Request,
    resolve: ResolveAlertRequest,
    user: User = Depends(require_role([Role.GP, Role.ANALYST])),
    store: SignalStore = Depends(get_store),
):
    """
    Resolve an alert (accept, reject, or snooze).
    """
    if resolve.action not in ["accept", "reject", "snooze"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action must be 'accept', 'reject', or 'snooze'",
        )

    if resolve.action == "snooze" and not resolve.snooze_until:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="snooze_until is required for snooze action",
        )

    db = await store._get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Map action to status
    status_map = {
        "accept": "accepted",
        "reject": "rejected",
        "snooze": "snoozed",
    }
    new_status = status_map[resolve.action]

    async with write_transaction(request):
        await db.execute("""
            UPDATE entity_alerts
            SET status = ?,
                reviewed_by = ?,
                reviewed_at = ?,
                snooze_until = ?
            WHERE id = ? AND entity_key = ?
        """, (
            new_status,
            user.email,
            now,
            resolve.snooze_until.isoformat() if resolve.snooze_until else None,
            alert_id,
            entity_key,
        ))
        await db.commit()

    return {
        "success": True,
        "alert_id": alert_id,
        "action": resolve.action,
        "new_status": new_status,
    }


# =============================================================================
# HELPERS
# =============================================================================

def _extract_website(canonical_key: str) -> Optional[str]:
    """Extract website URL from canonical key if domain-based."""
    if canonical_key.startswith("domain:"):
        domain = canonical_key[7:]
        return f"https://{domain}"
    return None


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
