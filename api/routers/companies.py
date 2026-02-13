"""
Companies Router

Endpoints for company data retrieval:
- GET /inbox - List inbox companies
- GET /{canonical_key} - Get company details
- GET /{canonical_key}/actions - Get action history
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from storage.signal_store import SignalStore, InboxCompany, CompanyState, CompanyAction

router = APIRouter(prefix="/companies", tags=["companies"])


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class InboxCompanyResponse(BaseModel):
    """Company summary for inbox view."""
    canonical_key: str
    company_name: Optional[str]
    status: str
    max_confidence: float
    signal_count: int
    sources: str
    first_seen: datetime
    last_seen: datetime
    owner: Optional[str]
    thesis_fit_score: Optional[float]
    vertical: Optional[str]


class InboxResponse(BaseModel):
    """Paginated inbox response."""
    companies: List[InboxCompanyResponse]
    total: int
    page: int
    page_size: int


class CompanyDetailResponse(BaseModel):
    """Detailed company information."""
    canonical_key: str
    company_name: Optional[str]
    website: Optional[str]
    max_confidence: float
    signal_count: int
    sources: str
    first_seen: Optional[str]
    last_seen: Optional[str]
    thesis_fit_score: Optional[float]
    vertical: Optional[str]
    one_liner: Optional[str]
    status: Optional[str]
    owner: Optional[str]
    pass_reason: Optional[str]


class CompanyActionResponse(BaseModel):
    """Action history entry."""
    id: int
    action: str
    occurred_at: datetime
    actor: Optional[str]
    metadata: Optional[dict]


class ActionHistoryResponse(BaseModel):
    """Action history for a company."""
    canonical_key: str
    actions: List[CompanyActionResponse]


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

async def get_store() -> SignalStore:
    """Get initialized SignalStore."""
    store = SignalStore()
    await store.initialize()
    return store


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/inbox", response_model=InboxResponse)
async def get_inbox(
    status: str = Query("inbox", description="Filter by status: inbox, tracking, passed"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0, description="Minimum confidence score"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    store: SignalStore = Depends(get_store),
):
    """
    Get companies in the inbox.

    Supports filtering by status and minimum confidence score.
    Returns paginated results sorted by confidence (highest first).
    """
    valid_statuses = {"inbox", "tracking", "passed", "pipeline_requested", "funded"}
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
        )

    offset = (page - 1) * page_size

    companies = await store.get_inbox_companies(
        status=status,
        min_confidence=min_confidence,
        limit=page_size,
        offset=offset,
    )

    # Count total matching companies (same filters, no LIMIT/OFFSET)
    db = store._db
    if db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT s.canonical_key
                   FROM signals s
                   LEFT JOIN company_state cs ON s.canonical_key = cs.canonical_key
                   WHERE COALESCE(cs.status, 'inbox') = ?
                     AND s.confidence >= ?
                   GROUP BY s.canonical_key
               )""",
            (status, min_confidence),
        )
        total = (await cursor.fetchone())[0]
    else:
        total = len(companies)

    return InboxResponse(
        companies=[
            InboxCompanyResponse(
                canonical_key=c.canonical_key,
                company_name=c.company_name,
                status=c.status,
                max_confidence=c.max_confidence,
                signal_count=c.signal_count,
                sources=c.sources,
                first_seen=c.first_seen,
                last_seen=c.last_seen,
                owner=c.owner,
                thesis_fit_score=c.thesis_fit_score,
                vertical=c.vertical,
            )
            for c in companies
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{canonical_key}", response_model=CompanyDetailResponse)
async def get_company(
    canonical_key: str,
    store: SignalStore = Depends(get_store),
):
    """
    Get detailed information about a specific company.

    Includes signal aggregation, thesis classification, and current state.
    """
    # Get aggregated company data
    company = await store.get_company_by_key(canonical_key)
    if not company:
        raise HTTPException(
            status_code=404,
            detail=f"Company not found: {canonical_key}",
        )

    # Get company state
    state = await store.get_company_state(canonical_key)

    return CompanyDetailResponse(
        canonical_key=company.get("canonical_key"),
        company_name=company.get("company_name"),
        website=company.get("website"),
        max_confidence=company.get("max_confidence", 0.0),
        signal_count=company.get("signal_count", 0),
        sources=company.get("sources", ""),
        first_seen=company.get("first_seen"),
        last_seen=company.get("last_seen"),
        thesis_fit_score=company.get("thesis_fit_score"),
        vertical=company.get("vertical"),
        one_liner=company.get("one_liner"),
        status=state.status if state else "inbox",
        owner=state.owner if state else None,
        pass_reason=state.pass_reason if state else None,
    )


@router.get("/{canonical_key}/actions", response_model=ActionHistoryResponse)
async def get_company_actions(
    canonical_key: str,
    limit: int = Query(50, ge=1, le=200, description="Max actions to return"),
    store: SignalStore = Depends(get_store),
):
    """
    Get action history for a company.

    Returns actions in reverse chronological order (most recent first).
    """
    actions = await store.get_company_actions(
        canonical_key=canonical_key,
        limit=limit,
    )

    return ActionHistoryResponse(
        canonical_key=canonical_key,
        actions=[
            CompanyActionResponse(
                id=a.id,
                action=a.action,
                occurred_at=a.occurred_at,
                actor=a.actor,
                metadata=a.metadata,
            )
            for a in actions
        ],
    )


@router.get("/{canonical_key}/signals")
async def get_company_signals(
    canonical_key: str,
    limit: int = Query(50, ge=1, le=200, description="Max signals to return"),
    store: SignalStore = Depends(get_store),
):
    """
    Get all signals for a company.

    Returns signals in reverse chronological order (most recent first).
    """
    signals = await store.get_signals_for_company(canonical_key)

    return {
        "canonical_key": canonical_key,
        "signals": [
            {
                "id": s.id,
                "signal_type": s.signal_type,
                "source_api": s.source_api,
                "confidence": s.confidence,
                "detected_at": s.detected_at.isoformat() if s.detected_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "raw_data": s.raw_data,
            }
            for s in signals[:limit]
        ],
        "total": len(signals),
    }
