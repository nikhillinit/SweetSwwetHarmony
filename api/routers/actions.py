"""
Actions Router

Endpoints for company inbox actions:
- POST /track - Move to tracking
- POST /pass - Pass with reason
- POST /pipeline - Queue for Notion
- GET /execute - Execute magic link action
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth.magic_tokens import consume_token, TokenError
from api.services.action_handler import CompanyActionHandler, ActionResult
from storage.signal_store import SignalStore

router = APIRouter(prefix="/actions", tags=["actions"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class TrackRequest(BaseModel):
    """Request to track a company."""
    canonical_key: str
    actor: Optional[str] = None


class PassRequest(BaseModel):
    """Request to pass on a company."""
    canonical_key: str
    reason: str
    actor: Optional[str] = None


class PipelineRequest(BaseModel):
    """Request to add company to pipeline."""
    canonical_key: str
    actor: Optional[str] = None


class SnoozeRequest(BaseModel):
    """Request to snooze a company."""
    canonical_key: str
    until: datetime
    actor: Optional[str] = None


class ActionResponse(BaseModel):
    """Response from an action."""
    success: bool
    canonical_key: str
    action: str
    message: str
    new_status: Optional[str] = None


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

async def get_store() -> SignalStore:
    """Get initialized SignalStore."""
    store = SignalStore()
    await store.initialize()
    return store


async def get_handler(store: SignalStore = Depends(get_store)) -> CompanyActionHandler:
    """Get action handler with store."""
    return CompanyActionHandler(store)


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/track", response_model=ActionResponse)
async def track_company(
    request: TrackRequest,
    handler: CompanyActionHandler = Depends(get_handler),
):
    """
    Move a company from inbox to tracking.

    The company will be flagged for monitoring but not yet pushed to Notion.
    """
    result = await handler.track(
        canonical_key=request.canonical_key,
        actor=request.actor,
    )
    return ActionResponse(
        success=result.success,
        canonical_key=result.canonical_key,
        action=result.action,
        message=result.message,
        new_status=result.new_status,
    )


@router.post("/pass", response_model=ActionResponse)
async def pass_company(
    request: PassRequest,
    handler: CompanyActionHandler = Depends(get_handler),
):
    """
    Pass on a company.

    The company will be marked as passed and future signals will be suppressed.
    Requires a reason for the pass.
    """
    if not request.reason or len(request.reason.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="Pass reason is required (min 3 characters)",
        )

    result = await handler.pass_company(
        canonical_key=request.canonical_key,
        reason=request.reason,
        actor=request.actor,
    )
    return ActionResponse(
        success=result.success,
        canonical_key=result.canonical_key,
        action=result.action,
        message=result.message,
        new_status=result.new_status,
    )


@router.post("/pipeline", response_model=ActionResponse)
async def add_to_pipeline(
    request: PipelineRequest,
    handler: CompanyActionHandler = Depends(get_handler),
):
    """
    Queue a company for Notion pipeline push.

    The company will be added to the outbox and processed asynchronously.
    """
    result = await handler.add_to_pipeline(
        canonical_key=request.canonical_key,
        actor=request.actor,
    )
    return ActionResponse(
        success=result.success,
        canonical_key=result.canonical_key,
        action=result.action,
        message=result.message,
        new_status=result.new_status,
    )


@router.post("/snooze", response_model=ActionResponse)
async def snooze_company(
    request: SnoozeRequest,
    handler: CompanyActionHandler = Depends(get_handler),
):
    """
    Snooze a company until a specific date.

    The company will be hidden from the inbox until the snooze expires.
    """
    result = await handler.snooze(
        canonical_key=request.canonical_key,
        until=request.until,
        actor=request.actor,
    )
    return ActionResponse(
        success=result.success,
        canonical_key=result.canonical_key,
        action=result.action,
        message=result.message,
        new_status=result.new_status,
    )


@router.get("/execute", response_model=ActionResponse)
async def execute_magic_link(
    token: str = Query(..., description="Magic link token"),
    store: SignalStore = Depends(get_store),
):
    """
    Execute an action from a magic link (email).

    The token is validated and consumed (one-time use).
    The action encoded in the token is then executed.
    """
    try:
        # Validate and consume token
        payload = await consume_token(store, token)
    except TokenError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Execute the action
    handler = CompanyActionHandler(store)

    if payload.action == "track":
        result = await handler.track(
            canonical_key=payload.canonical_key,
            actor="email_link",
        )
    elif payload.action == "pass":
        # For email links, we might want a default reason or redirect to form
        result = ActionResult(
            success=False,
            canonical_key=payload.canonical_key,
            action="pass",
            message="Pass action requires reason - please use the dashboard",
        )
    elif payload.action == "view":
        # View action just validates the token, no state change
        result = ActionResult(
            success=True,
            canonical_key=payload.canonical_key,
            action="view",
            message="Token validated successfully",
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {payload.action}",
        )

    return ActionResponse(
        success=result.success,
        canonical_key=result.canonical_key,
        action=result.action,
        message=result.message,
        new_status=result.new_status,
    )
