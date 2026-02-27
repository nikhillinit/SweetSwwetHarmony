"""
Actions Router

Endpoints for company inbox actions:
- POST /track - Move to tracking
- POST /pass - Pass with reason
- POST /pipeline - Queue for Notion
- GET /execute - Show confirmation page (NO state mutation)
- POST /execute - Execute magic link action (consumes token)

Security Note:
GET /execute intentionally does NOT mutate state to protect against
email security scanners that pre-fetch links. The actual action
execution happens via POST after user confirmation.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

from api.auth.magic_tokens import consume_token, peek_token, TokenError
from api.services.action_handler import CompanyActionHandler, ActionResult
from api.db import get_store
from storage.signal_store import SignalStore

# Template setup for HTML responses
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "distribution" / "templates"
_jinja_env = None

def get_jinja_env() -> Environment:
    """Get or create Jinja2 environment."""
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
    return _jinja_env

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


@router.get("/execute", response_class=HTMLResponse)
async def show_action_confirmation(
    token: str = Query(..., description="Magic link token"),
    store: SignalStore = Depends(get_store),
):
    """
    Show confirmation page for a magic link action.

    IMPORTANT: This endpoint does NOT mutate state. It only validates
    the token and shows a confirmation page. This protects against
    email security scanners that pre-fetch links.

    The actual action execution happens via POST /execute.
    """
    try:
        # Peek at token (validate WITHOUT consuming)
        payload = peek_token(token)
    except TokenError as e:
        # Return error HTML page
        return _render_error_page(str(e))

    # Get company info for display
    company = await store.get_company_by_key(payload.canonical_key)
    company_name = "Unknown Company"
    if company:
        company_name = company.get("company_name") or payload.canonical_key

    # Get action display info
    action_info = _get_action_display_info(payload.action)

    # Render confirmation page
    return _render_confirmation_page(
        token=token,
        company_name=company_name,
        canonical_key=payload.canonical_key,
        action=payload.action,
        action_label=action_info["label"],
        action_color=action_info["color"],
        action_description=action_info["description"],
    )


@router.post("/execute", response_class=HTMLResponse)
async def execute_magic_link(
    token: str = Form(..., description="Magic link token"),
    store: SignalStore = Depends(get_store),
):
    """
    Execute an action from a magic link (email).

    This endpoint consumes the token (one-time use) and executes
    the action. Called after user confirms on the GET page.
    """
    try:
        # Validate and consume token (one-time use)
        payload = await consume_token(store, token)
    except TokenError as e:
        return _render_error_page(str(e))

    # Execute the action
    handler = CompanyActionHandler(store)

    if payload.action == "track":
        result = await handler.track(
            canonical_key=payload.canonical_key,
            actor="email_link",
        )
    elif payload.action == "pass":
        # Pass requires a reason - show form or use default
        result = await handler.pass_company(
            canonical_key=payload.canonical_key,
            reason="Passed via email link",
            actor="email_link",
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
        return _render_error_page(f"Unknown action: {payload.action}")

    # Get company name for display
    company = await store.get_company_by_key(payload.canonical_key)
    company_name = "Unknown Company"
    if company:
        company_name = company.get("company_name") or payload.canonical_key

    # Render success/failure page
    return _render_result_page(
        success=result.success,
        company_name=company_name,
        action=payload.action,
        message=result.message,
        new_status=result.new_status,
    )


# =============================================================================
# HTML RENDERING HELPERS
# =============================================================================

def _get_action_display_info(action: str) -> dict:
    """Get display info for an action type."""
    actions = {
        "track": {
            "label": "Track",
            "color": "#10B981",  # Green
            "description": "Add this company to your tracking list for monitoring.",
        },
        "pass": {
            "label": "Pass",
            "color": "#6B7280",  # Gray
            "description": "Pass on this company and suppress future signals.",
        },
        "view": {
            "label": "View",
            "color": "#3B82F6",  # Blue
            "description": "View company details.",
        },
    }
    return actions.get(action, {
        "label": action.title(),
        "color": "#6B7280",
        "description": f"Execute {action} action.",
    })


def _render_confirmation_page(
    token: str,
    company_name: str,
    canonical_key: str,
    action: str,
    action_label: str,
    action_color: str,
    action_description: str,
) -> str:
    """Render the action confirmation HTML page."""
    try:
        env = get_jinja_env()
        template = env.get_template("action_confirm.html")
        return template.render(
            token=token,
            company_name=company_name,
            canonical_key=canonical_key,
            action=action,
            action_label=action_label,
            action_color=action_color,
            action_description=action_description,
        )
    except Exception:
        # Fallback inline HTML if template not found
        return _inline_confirmation_html(
            token, company_name, action_label, action_color, action_description
        )


def _render_error_page(error_message: str) -> str:
    """Render an error HTML page."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Error | Press On Ventures</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F9FAFB; margin: 0; padding: 40px 20px; }}
        .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; }}
        .icon {{ font-size: 48px; margin-bottom: 20px; }}
        h1 {{ color: #DC2626; font-size: 24px; margin: 0 0 16px 0; }}
        p {{ color: #6B7280; line-height: 1.6; }}
        .error-detail {{ background: #FEF2F2; color: #991B1B; padding: 12px 16px; border-radius: 8px; margin-top: 20px; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">&#9888;</div>
        <h1>Unable to Process Request</h1>
        <p>We couldn't process your action request.</p>
        <div class="error-detail">{error_message}</div>
    </div>
</body>
</html>"""


def _render_result_page(
    success: bool,
    company_name: str,
    action: str,
    message: str,
    new_status: Optional[str],
) -> str:
    """Render the action result HTML page."""
    if success:
        icon = "&#10004;"  # Checkmark
        title = "Action Completed"
        bg_color = "#ECFDF5"
        text_color = "#065F46"
    else:
        icon = "&#9888;"  # Warning
        title = "Action Failed"
        bg_color = "#FEF2F2"
        text_color = "#991B1B"

    status_html = ""
    if new_status:
        status_html = f'<p style="color: #6B7280; margin-top: 16px;">New status: <strong>{new_status}</strong></p>'

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} | Press On Ventures</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F9FAFB; margin: 0; padding: 40px 20px; }}
        .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; }}
        .icon {{ font-size: 48px; margin-bottom: 20px; color: {text_color}; }}
        h1 {{ color: #292929; font-size: 24px; margin: 0 0 8px 0; }}
        .company {{ color: #6B7280; font-size: 18px; margin-bottom: 20px; }}
        .message {{ background: {bg_color}; color: {text_color}; padding: 16px; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <p class="company">{company_name}</p>
        <div class="message">{message}</div>
        {status_html}
    </div>
</body>
</html>"""


def _inline_confirmation_html(
    token: str,
    company_name: str,
    action_label: str,
    action_color: str,
    action_description: str,
) -> str:
    """Fallback inline HTML for confirmation page."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Confirm Action | Press On Ventures</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F9FAFB; margin: 0; padding: 40px 20px; }}
        .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .header {{ background: linear-gradient(135deg, #292929 0%, #3d3d3d 100%); color: white; padding: 24px; border-radius: 8px; margin-bottom: 24px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 20px; }}
        .company {{ font-size: 24px; font-weight: bold; color: #292929; margin-bottom: 8px; }}
        .description {{ color: #6B7280; margin-bottom: 24px; }}
        .btn {{ display: inline-block; padding: 12px 32px; border-radius: 8px; font-weight: 600; text-decoration: none; cursor: pointer; border: none; font-size: 16px; }}
        .btn-primary {{ background: {action_color}; color: white; }}
        .btn-primary:hover {{ opacity: 0.9; }}
        form {{ text-align: center; }}
        .footer {{ margin-top: 24px; text-align: center; color: #9CA3AF; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Press On Ventures</h1>
        </div>
        <div style="text-align: center;">
            <p class="company">{company_name}</p>
            <p class="description">{action_description}</p>
            <form method="POST" action="/api/v1/actions/execute">
                <input type="hidden" name="token" value="{token}">
                <button type="submit" class="btn btn-primary">{action_label} This Company</button>
            </form>
        </div>
        <p class="footer">This link can only be used once.</p>
    </div>
    <script>
        // Auto-submit after 1 second for streamlined UX
        // Comment out if you want manual-only confirmation
        // setTimeout(function() {{ document.querySelector('form').submit(); }}, 1000);
    </script>
</body>
</html>"""
