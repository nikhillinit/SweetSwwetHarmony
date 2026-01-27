"""
Company Card Component

Renders a company card for the inbox view with:
- Company name and confidence score
- Signal sources and count
- Thesis fit indicator
- Action buttons
"""

import streamlit as st
from datetime import datetime
from typing import Dict, Any, Optional

from dashboard.components.action_buttons import render_action_buttons


# Source name mapping
SOURCE_FRIENDLY_NAMES = {
    "github": "GitHub",
    "sec_edgar": "SEC Filings",
    "companies_house": "UK Companies",
    "producthunt": "Product Hunt",
    "hacker_news": "Hacker News",
    "crunchbase": "Crunchbase",
    "linkedin": "LinkedIn",
    "job_postings": "Job Boards",
    "arxiv": "Research Papers",
    "uspto": "Patents",
    "domain_whois": "Domain Registration",
    "opencorporates": "OpenCorporates",
}


def get_confidence_style(confidence: float) -> tuple[str, str, str]:
    """Get color, background, and label based on confidence score."""
    if confidence >= 0.8:
        return "#065F46", "#D1FAE5", "High Match"  # Dark green on light green
    elif confidence >= 0.6:
        return "#1E40AF", "#DBEAFE", "Good Match"  # Dark blue on light blue
    elif confidence >= 0.4:
        return "#92400E", "#FEF3C7", "Moderate"  # Dark amber on light amber
    else:
        return "#374151", "#F3F4F6", "Low"  # Dark gray on light gray


def format_sources(sources_str: str) -> str:
    """Format source string with friendly names."""
    if not sources_str:
        return "Unknown"
    sources = sources_str.split(",")
    friendly = [SOURCE_FRIENDLY_NAMES.get(s.strip(), s.strip()) for s in sources]
    return ", ".join(friendly[:3])


def format_time_ago(dt) -> str:
    """Format datetime as relative time."""
    if not dt:
        return "Unknown"

    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except:
            return "Unknown"

    now = datetime.utcnow()
    if hasattr(dt, 'replace'):
        dt = dt.replace(tzinfo=None)
    diff = now - dt
    days = diff.days
    if days == 0:
        hours = diff.seconds // 3600
        if hours == 0:
            return "Just now"
        return f"{hours}h ago"
    elif days == 1:
        return "Yesterday"
    elif days < 7:
        return f"{days}d ago"
    elif days < 30:
        weeks = days // 7
        return f"{weeks}w ago"
    else:
        return dt.strftime("%b %d")


def render_company_card(
    company: Dict[str, Any],
    show_actions: bool = True,
    expanded: bool = False,
    on_select: Optional[callable] = None,
) -> Optional[str]:
    """
    Render a company card using Streamlit components.
    """
    action_taken = None

    # Extract data safely
    canonical_key = company.get("canonical_key", "unknown")
    company_name = company.get("company_name") or canonical_key[:20]
    status = company.get("status", "inbox")
    confidence = float(company.get("max_confidence", 0) or 0)
    signal_count = int(company.get("signal_count", 0) or 0)
    sources = company.get("sources", "") or ""
    thesis_fit = company.get("thesis_fit_score")
    vertical = company.get("vertical")
    owner = company.get("owner")
    first_seen = company.get("first_seen")

    # Get styling
    conf_color, conf_bg, conf_label = get_confidence_style(confidence)
    conf_pct = int(confidence * 100)

    # Build info string
    info_parts = [
        f"{signal_count} signal{'s' if signal_count != 1 else ''}",
        format_sources(sources),
        f"First seen {format_time_ago(first_seen)}",
    ]
    if owner:
        info_parts.append(f"Owner: {owner}")
    info_text = " · ".join(info_parts)

    # Render card with custom HTML for better contrast
    vertical_badge = f'<span style="background:#E5E7EB; color:#374151; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px;">{vertical}</span>' if vertical else ''

    st.markdown(f"""
    <div style="
        background: #FFFFFF;
        border: 1px solid #D1D5DB;
        border-left: 4px solid {conf_color};
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
    ">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
            <div>
                <span style="font-size:18px; font-weight:700; color:#111827;">{company_name}</span>
                {vertical_badge}
            </div>
            <span style="
                background:{conf_bg};
                color:{conf_color};
                padding:4px 12px;
                border-radius:12px;
                font-weight:600;
                font-size:13px;
            ">{conf_pct}% {conf_label}</span>
        </div>
        <div style="color:#4B5563; font-size:14px;">{info_text}</div>
    </div>
    """, unsafe_allow_html=True)

    # Action buttons (using Streamlit native)
    if show_actions and status != "passed":
        action_taken = render_action_buttons(
            canonical_key=canonical_key,
            current_status=status,
            compact=True,
        )

    # Thesis fit
    if thesis_fit is not None and thesis_fit > 0:
        st.markdown(f"<div style='color:#6B7280; font-size:13px; margin-top:4px;'>Thesis fit: {int(thesis_fit * 100)}%</div>", unsafe_allow_html=True)

    return action_taken
