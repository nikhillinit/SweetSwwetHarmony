"""
Public Router

Public-facing endpoints that don't require authentication.
Used for "View Details" links in email digests.

Endpoints:
- GET /companies/{canonical_key}/public - Branded HTML company summary
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from storage.signal_store import SignalStore

router = APIRouter(prefix="/companies", tags=["public"])

# Template setup
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


async def get_store() -> SignalStore:
    """Get initialized SignalStore."""
    store = SignalStore()
    await store.initialize()
    return store


@router.get("/{canonical_key}/public", response_class=HTMLResponse)
async def get_public_company_profile(
    canonical_key: str,
    store: SignalStore = Depends(get_store),
):
    """
    Get a public HTML view of a company.

    This is the destination for "View Details" links in email digests.
    No authentication required - provides limited public information.
    """
    # Get company data
    company = await store.get_company_by_key(canonical_key)

    if not company:
        return _render_not_found_page(canonical_key)

    # Get signals for this company
    signals = await store.get_signals_by_canonical_key(canonical_key)

    # Get company state if exists
    state = await store.get_company_state(canonical_key)

    # Render the profile page
    return _render_company_profile(
        company=company,
        signals=signals,
        state=state,
        canonical_key=canonical_key,
    )


def _render_company_profile(
    company: dict,
    signals: list,
    state: Optional[object],
    canonical_key: str,
) -> str:
    """Render the company profile HTML page."""
    company_name = company.get("company_name") or canonical_key
    website = company.get("website") or ""
    one_liner = company.get("one_liner") or "No description available."
    confidence = company.get("max_confidence") or 0
    sources = company.get("sources") or ""

    # Parse sources string into list
    source_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else []

    # Get status from state
    status = "Inbox"
    if state:
        status = getattr(state, "status", "inbox").replace("_", " ").title()

    # Format confidence as percentage
    confidence_pct = int(confidence * 100)
    confidence_color = _get_confidence_color(confidence)

    # Build signal cards HTML
    signal_html = _build_signal_cards(signals)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>{company_name} | Press On Ventures</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #F9FAFB;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
        }}
        .header {{
            background: linear-gradient(135deg, #292929 0%, #3d3d3d 100%);
            color: white;
            padding: 24px 32px;
            border-radius: 16px 16px 0 0;
        }}
        .header h1 {{
            font-size: 14px;
            font-weight: 600;
            letter-spacing: 1px;
            opacity: 0.8;
        }}
        .card {{
            background: white;
            border-radius: 0 0 16px 16px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            overflow: hidden;
        }}
        .company-header {{
            padding: 32px;
            border-bottom: 1px solid #E5E7EB;
        }}
        .company-name {{
            font-size: 32px;
            font-weight: 700;
            color: #292929;
            margin-bottom: 8px;
        }}
        .company-website {{
            color: #3B82F6;
            text-decoration: none;
            font-size: 14px;
        }}
        .company-website:hover {{
            text-decoration: underline;
        }}
        .meta-row {{
            display: flex;
            gap: 16px;
            margin-top: 16px;
            flex-wrap: wrap;
        }}
        .meta-badge {{
            display: inline-flex;
            align-items: center;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 500;
        }}
        .status-badge {{
            background: #E0D8D1;
            color: #292929;
        }}
        .confidence-badge {{
            color: white;
        }}
        .description {{
            padding: 24px 32px;
            border-bottom: 1px solid #E5E7EB;
        }}
        .description h3 {{
            font-size: 14px;
            font-weight: 600;
            color: #6B7280;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
        }}
        .description p {{
            color: #374151;
            font-size: 16px;
            line-height: 1.6;
        }}
        .sources {{
            padding: 24px 32px;
            border-bottom: 1px solid #E5E7EB;
        }}
        .sources h3 {{
            font-size: 14px;
            font-weight: 600;
            color: #6B7280;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
        }}
        .source-list {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .source-tag {{
            background: #F3F4F6;
            color: #374151;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
        }}
        .signals {{
            padding: 24px 32px;
        }}
        .signals h3 {{
            font-size: 14px;
            font-weight: 600;
            color: #6B7280;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
        }}
        .signal-card {{
            background: #F9FAFB;
            border: 1px solid #E5E7EB;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 12px;
        }}
        .signal-card:last-child {{
            margin-bottom: 0;
        }}
        .signal-type {{
            font-weight: 600;
            color: #292929;
            margin-bottom: 4px;
        }}
        .signal-date {{
            font-size: 12px;
            color: #9CA3AF;
        }}
        .signal-confidence {{
            font-size: 12px;
            color: #6B7280;
            margin-top: 8px;
        }}
        .footer {{
            text-align: center;
            padding: 24px;
            color: #9CA3AF;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>PRESS ON VENTURES</h1>
        </div>
        <div class="card">
            <div class="company-header">
                <h2 class="company-name">{company_name}</h2>
                {f'<a href="{website}" class="company-website" target="_blank">{website}</a>' if website else ''}
                <div class="meta-row">
                    <span class="meta-badge status-badge">{status}</span>
                    <span class="meta-badge confidence-badge" style="background: {confidence_color};">{confidence_pct}% Match</span>
                </div>
            </div>
            <div class="description">
                <h3>About</h3>
                <p>{one_liner}</p>
            </div>
            {f'''<div class="sources">
                <h3>Signal Sources</h3>
                <div class="source-list">
                    {"".join(f'<span class="source-tag">{s}</span>' for s in source_list)}
                </div>
            </div>''' if source_list else ''}
            {signal_html}
        </div>
        <div class="footer">
            <p>Press On Ventures Discovery Engine</p>
        </div>
    </div>
</body>
</html>"""


def _build_signal_cards(signals: list) -> str:
    """Build HTML for signal cards section."""
    if not signals:
        return ""

    cards = []
    for signal in signals[:5]:  # Limit to 5 most recent
        signal_type = signal.get("signal_type", "Unknown").replace("_", " ").title()
        source = signal.get("source_api", "")
        confidence = signal.get("confidence", 0)
        detected = signal.get("detected_at", "")[:10] if signal.get("detected_at") else ""

        cards.append(f"""
            <div class="signal-card">
                <div class="signal-type">{signal_type}</div>
                <div class="signal-date">{source} &middot; {detected}</div>
                <div class="signal-confidence">Confidence: {int(confidence * 100)}%</div>
            </div>
        """)

    return f"""
        <div class="signals">
            <h3>Recent Signals ({len(signals)} total)</h3>
            {"".join(cards)}
        </div>
    """


def _get_confidence_color(confidence: float) -> str:
    """Get color for confidence badge."""
    if confidence >= 0.7:
        return "#10B981"  # Green
    elif confidence >= 0.4:
        return "#F59E0B"  # Amber
    else:
        return "#6B7280"  # Gray


def _render_not_found_page(canonical_key: str) -> str:
    """Render a not found page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Not Found | Press On Ventures</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #F9FAFB;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            max-width: 480px;
            background: white;
            border-radius: 16px;
            padding: 48px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            text-align: center;
        }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        h1 {{ color: #292929; font-size: 24px; margin-bottom: 8px; }}
        p {{ color: #6B7280; }}
        .key {{ font-family: monospace; color: #9CA3AF; font-size: 12px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">&#128269;</div>
        <h1>Company Not Found</h1>
        <p>We couldn't find a company matching your request.</p>
        <p class="key">{canonical_key}</p>
    </div>
</body>
</html>"""
