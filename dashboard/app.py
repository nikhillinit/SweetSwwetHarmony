"""
Discovery Engine Dashboard - Deal Pipeline for Press On Ventures

A refined, editorial-style dashboard for viewing deals and signals.
Designed for non-technical users with clear guidance and plain language.

Design Direction: Editorial/Refined
- Magazine-style typography (DM Serif Display + DM Sans)
- Dark mode with warm accent colors
- Card-based layout with generous whitespace
- Status-driven color coding with clear legends
- Contextual help and tooltips throughout
- Plain-language labels for all technical concepts

Run:
    streamlit run dashboard/app.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

import streamlit as st
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

from storage.signal_store import SignalStore
from utils.signal_health import SignalHealthMonitor
from dashboard.mini_scout import render_mini_scout_page

# =============================================================================
# CONFIG
# =============================================================================

DB_PATH = os.environ.get("DISCOVERY_DB_PATH", "signals.db")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# Notion statuses in pipeline order
NOTION_STATUSES = [
    "Source", "Initial Meeting / Call", "Dilligence", "Tracking",
    "Committed", "Funded", "Passed", "Lost",
]

# Status colors (warm, refined palette)
STATUS_COLORS = {
    "Source": "#F59E0B",           # Amber - new opportunities
    "Initial Meeting / Call": "#3B82F6",  # Blue - in conversation
    "Dilligence": "#8B5CF6",       # Purple - deep dive
    "Tracking": "#6B7280",         # Gray - watching
    "Committed": "#10B981",        # Emerald - committed
    "Funded": "#059669",           # Green - portfolio
    "Passed": "#EF4444",           # Red - passed
    "Lost": "#991B1B",             # Dark red - lost
}

# User-friendly status descriptions for non-technical users
STATUS_DESCRIPTIONS = {
    "Source": "New companies we just discovered - ready for your review",
    "Initial Meeting / Call": "Companies we're actively talking to",
    "Dilligence": "Deep-dive research in progress",
    "Tracking": "Interesting companies we're keeping an eye on",
    "Committed": "We've decided to invest - paperwork in progress",
    "Funded": "Portfolio companies - investment complete!",
    "Passed": "Companies we decided not to pursue",
    "Lost": "Deals that didn't work out",
}

# User-friendly names for technical data sources
SOURCE_FRIENDLY_NAMES = {
    "github": "GitHub (Developer Activity)",
    "sec_edgar": "SEC Filings",
    "companies_house": "UK Company Registry",
    "product_hunt": "Product Hunt Launches",
    "hacker_news": "Hacker News Mentions",
    "arxiv": "Research Papers",
    "uspto": "Patent Filings",
    "linkedin": "LinkedIn Profiles",
    "crunchbase": "Crunchbase",
    "domain_whois": "New Domain Registrations",
}

# Help text for key concepts
HELP_TEXT = {
    "confidence": "How well this company matches our investment criteria (higher = better fit)",
    "signals": "Data points that help us discover and evaluate companies",
    "pipeline": "All the companies we're evaluating, organized by stage",
    "sector": "The industry or market this company operates in",
    "stage": "How much funding the company has raised (Pre-Seed → Series A)",
}

# =============================================================================
# PAGE CONFIG & CUSTOM CSS
# =============================================================================

st.set_page_config(
    page_title="Discovery Engine | Press On Ventures",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for editorial design
st.markdown("""
<style>
    /* Import distinctive fonts */
    @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&display=swap');

    /* Root variables */
    :root {
        --bg-primary: #0F0F0F;
        --bg-secondary: #1A1A1A;
        --bg-card: #242424;
        --bg-hover: #2A2A2A;
        --text-primary: #FAFAFA;
        --text-secondary: #A3A3A3;
        --text-muted: #737373;
        --border-color: #333333;
        --accent-gold: #F59E0B;
        --accent-emerald: #10B981;
        --accent-blue: #3B82F6;
    }

    /* Global styles */
    .stApp {
        background-color: var(--bg-primary);
    }

    /* Hide default Streamlit elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* Typography overrides */
    h1, h2, h3 {
        font-family: 'DM Serif Display', serif !important;
        color: var(--text-primary) !important;
        letter-spacing: -0.02em;
    }

    h1 {
        font-size: 2.5rem !important;
        font-weight: 400 !important;
        margin-bottom: 0.5rem !important;
    }

    p, span, div, label {
        font-family: 'DM Sans', sans-serif !important;
    }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background-color: var(--bg-secondary) !important;
        border-right: 1px solid var(--border-color);
    }

    [data-testid="stSidebar"] .stRadio > label {
        color: var(--text-secondary) !important;
    }

    /* Metric cards */
    [data-testid="stMetricValue"] {
        font-family: 'DM Serif Display', serif !important;
        font-size: 2.25rem !important;
        color: var(--text-primary) !important;
    }

    [data-testid="stMetricLabel"] {
        font-family: 'DM Sans', sans-serif !important;
        color: var(--text-secondary) !important;
        text-transform: uppercase;
        font-size: 0.7rem !important;
        letter-spacing: 0.1em;
    }

    /* Custom card styling */
    .deal-card {
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        transition: all 0.2s ease;
    }

    .deal-card:hover {
        background: var(--bg-hover);
        border-color: var(--accent-gold);
        transform: translateY(-2px);
    }

    .deal-name {
        font-family: 'DM Serif Display', serif;
        font-size: 1.25rem;
        color: var(--text-primary);
        margin-bottom: 0.25rem;
    }

    .deal-meta {
        font-family: 'DM Sans', sans-serif;
        font-size: 0.85rem;
        color: var(--text-secondary);
    }

    .deal-link {
        color: var(--text-muted);
        text-decoration: none;
        font-size: 0.8rem;
    }

    .deal-link:hover {
        color: var(--accent-gold);
    }

    /* Status badges */
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.7rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Confidence indicator */
    .confidence-high { color: #10B981; }
    .confidence-med { color: #F59E0B; }
    .confidence-low { color: #EF4444; }

    /* Section headers */
    .section-header {
        font-family: 'DM Sans', sans-serif;
        font-size: 0.75rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.15em;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid var(--border-color);
    }

    /* Expander styling */
    .streamlit-expanderHeader {
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 500 !important;
        background-color: var(--bg-card) !important;
        border-radius: 8px !important;
    }

    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 1px solid var(--border-color);
    }

    .stTabs [data-baseweb="tab"] {
        font-family: 'DM Sans', sans-serif !important;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        padding: 1rem 1.5rem;
        color: var(--text-secondary);
        border-bottom: 2px solid transparent;
    }

    .stTabs [aria-selected="true"] {
        color: var(--accent-gold) !important;
        border-bottom-color: var(--accent-gold) !important;
    }

    /* Button styling */
    .stButton > button {
        font-family: 'DM Sans', sans-serif !important;
        background-color: var(--bg-card) !important;
        border: 1px solid var(--border-color) !important;
        color: var(--text-primary) !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
    }

    .stButton > button:hover {
        background-color: var(--bg-hover) !important;
        border-color: var(--accent-gold) !important;
    }

    /* Select boxes */
    .stSelectbox > div > div {
        background-color: var(--bg-card) !important;
        border-color: var(--border-color) !important;
    }

    /* Hero section */
    .hero-section {
        padding: 2rem 0;
        margin-bottom: 2rem;
        border-bottom: 1px solid var(--border-color);
    }

    .hero-title {
        font-family: 'DM Serif Display', serif;
        font-size: 2.5rem;
        color: var(--text-primary);
        margin-bottom: 0.5rem;
    }

    .hero-subtitle {
        font-family: 'DM Sans', sans-serif;
        font-size: 1rem;
        color: var(--text-secondary);
    }

    /* Stats grid */
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 1.5rem;
        margin: 2rem 0;
    }

    .stat-card {
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
    }

    .stat-value {
        font-family: 'DM Serif Display', serif;
        font-size: 2.5rem;
        color: var(--text-primary);
    }

    .stat-label {
        font-family: 'DM Sans', sans-serif;
        font-size: 0.7rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-top: 0.5rem;
    }

    /* Divider */
    hr {
        border: none;
        border-top: 1px solid var(--border-color);
        margin: 2rem 0;
    }

    /* =========================================
       USABILITY ENHANCEMENTS FOR NON-TECHNICAL USERS
       ========================================= */

    /* Welcome/onboarding banner */
    .welcome-banner {
        background: linear-gradient(135deg, #1a1a1a 0%, #242424 100%);
        border: 1px solid var(--border-color);
        border-left: 4px solid var(--accent-gold);
        border-radius: 12px;
        padding: 1.5rem 2rem;
        margin-bottom: 2rem;
        animation: fadeIn 0.5s ease;
    }

    .welcome-banner h3 {
        font-family: 'DM Serif Display', serif !important;
        font-size: 1.25rem !important;
        color: var(--text-primary) !important;
        margin-bottom: 0.5rem !important;
    }

    .welcome-banner p {
        color: var(--text-secondary);
        font-size: 0.9rem;
        line-height: 1.6;
        margin: 0;
    }

    .welcome-banner .dismiss-btn {
        color: var(--text-muted);
        font-size: 0.75rem;
        cursor: pointer;
        margin-top: 1rem;
        display: inline-block;
    }

    .welcome-banner .dismiss-btn:hover {
        color: var(--text-primary);
    }

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(-10px); }
        to { opacity: 1; transform: translateY(0); }
    }

    /* Help tooltips */
    .help-tooltip {
        position: relative;
        display: inline-flex;
        align-items: center;
        cursor: help;
    }

    .help-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: var(--bg-hover);
        border: 1px solid var(--border-color);
        color: var(--text-muted);
        font-size: 10px;
        font-weight: 600;
        margin-left: 6px;
        transition: all 0.2s ease;
    }

    .help-icon:hover {
        background: var(--accent-gold);
        color: var(--bg-primary);
        border-color: var(--accent-gold);
    }

    .tooltip-text {
        visibility: hidden;
        opacity: 0;
        position: absolute;
        bottom: 125%;
        left: 50%;
        transform: translateX(-50%);
        background: #333;
        color: var(--text-primary);
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 0.75rem;
        white-space: nowrap;
        max-width: 250px;
        white-space: normal;
        z-index: 1000;
        transition: all 0.2s ease;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }

    .help-tooltip:hover .tooltip-text {
        visibility: visible;
        opacity: 1;
    }

    /* Status legend card */
    .status-legend {
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 1.25rem;
        margin-bottom: 1.5rem;
    }

    .status-legend-title {
        font-family: 'DM Sans', sans-serif;
        font-size: 0.7rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.15em;
        margin-bottom: 1rem;
    }

    .status-legend-item {
        display: flex;
        align-items: center;
        padding: 0.5rem 0;
        border-bottom: 1px solid var(--border-color);
    }

    .status-legend-item:last-child {
        border-bottom: none;
    }

    .status-legend-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 10px;
        flex-shrink: 0;
    }

    .status-legend-name {
        color: var(--text-primary);
        font-size: 0.85rem;
        flex: 1;
    }

    .status-legend-desc {
        color: var(--text-muted);
        font-size: 0.75rem;
        max-width: 200px;
        text-align: right;
    }

    /* Confidence meter visual */
    .confidence-meter {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .confidence-bar {
        width: 60px;
        height: 6px;
        background: var(--bg-secondary);
        border-radius: 3px;
        overflow: hidden;
    }

    .confidence-fill {
        height: 100%;
        border-radius: 3px;
        transition: width 0.3s ease;
    }

    .confidence-fill.high { background: #10B981; }
    .confidence-fill.medium { background: #F59E0B; }
    .confidence-fill.low { background: #EF4444; }

    .confidence-label {
        font-size: 0.75rem;
        color: var(--text-secondary);
    }

    /* Quick actions bar */
    .quick-actions {
        display: flex;
        gap: 0.75rem;
        margin-bottom: 1.5rem;
        flex-wrap: wrap;
    }

    .quick-action-btn {
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 20px;
        padding: 0.5rem 1rem;
        color: var(--text-secondary);
        font-size: 0.8rem;
        cursor: pointer;
        transition: all 0.2s ease;
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .quick-action-btn:hover {
        background: var(--bg-hover);
        border-color: var(--accent-gold);
        color: var(--text-primary);
    }

    .quick-action-btn.active {
        background: var(--accent-gold);
        border-color: var(--accent-gold);
        color: var(--bg-primary);
    }

    /* Empty state with guidance */
    .empty-state {
        text-align: center;
        padding: 4rem 2rem;
        background: var(--bg-card);
        border: 1px dashed var(--border-color);
        border-radius: 12px;
        margin: 2rem 0;
    }

    .empty-state-icon {
        font-size: 3rem;
        margin-bottom: 1rem;
        opacity: 0.5;
    }

    .empty-state h3 {
        font-family: 'DM Serif Display', serif !important;
        color: var(--text-primary) !important;
        margin-bottom: 0.5rem !important;
    }

    .empty-state p {
        color: var(--text-secondary);
        max-width: 400px;
        margin: 0 auto 1.5rem;
        line-height: 1.6;
    }

    .empty-state-action {
        display: inline-block;
        background: var(--accent-gold);
        color: var(--bg-primary);
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        font-weight: 500;
        text-decoration: none;
        transition: all 0.2s ease;
    }

    .empty-state-action:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
    }

    /* Section intro text */
    .section-intro {
        color: var(--text-secondary);
        font-size: 0.9rem;
        line-height: 1.6;
        margin-bottom: 1.5rem;
        max-width: 600px;
    }

    /* Feature highlight card */
    .feature-card {
        background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 1.25rem;
        margin-bottom: 1rem;
        transition: all 0.2s ease;
    }

    .feature-card:hover {
        border-color: var(--accent-gold);
    }

    .feature-card-title {
        color: var(--text-primary);
        font-weight: 500;
        margin-bottom: 0.25rem;
    }

    .feature-card-desc {
        color: var(--text-secondary);
        font-size: 0.8rem;
    }

    /* Notification badge */
    .notification-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 20px;
        height: 20px;
        background: var(--accent-gold);
        color: var(--bg-primary);
        border-radius: 10px;
        font-size: 0.7rem;
        font-weight: 600;
        padding: 0 6px;
        margin-left: 8px;
    }

    /* Animated loading state */
    .loading-shimmer {
        background: linear-gradient(90deg, var(--bg-card) 0%, var(--bg-hover) 50%, var(--bg-card) 100%);
        background-size: 200% 100%;
        animation: shimmer 1.5s infinite;
        border-radius: 8px;
    }

    @keyframes shimmer {
        0% { background-position: 200% 0; }
        100% { background-position: -200% 0; }
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# ASYNC HELPERS
# =============================================================================

def run_async(coro):
    """Run async function in sync context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@st.cache_resource
def get_store():
    """Get or create signal store (cached)."""
    store = SignalStore(DB_PATH)
    run_async(store.initialize())
    return store


# =============================================================================
# DATA LOADING
# =============================================================================

@st.cache_data(ttl=60)
def load_signals(_store, days_back: int = 7):
    """Load signals from database."""
    async def _load():
        if not _store._db:
            await _store.initialize()

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        cursor = await _store._db.execute(
            """
            SELECT s.id, s.signal_type, s.source_api, s.canonical_key,
                   s.company_name, s.confidence, s.raw_data,
                   s.detected_at, s.created_at,
                   p.status as processing_status, p.notion_page_id
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.created_at >= ?
            ORDER BY s.confidence DESC, s.created_at DESC
            """,
            (cutoff.isoformat(),)
        )

        rows = await cursor.fetchall()
        signals = []
        for row in rows:
            import json
            signals.append({
                "id": row[0],
                "signal_type": row[1],
                "source_api": row[2],
                "canonical_key": row[3],
                "company_name": row[4] or "Unknown",
                "confidence": row[5],
                "raw_data": json.loads(row[6]) if row[6] else {},
                "detected_at": row[7],
                "created_at": row[8],
                "processing_status": row[9] or "pending",
                "notion_page_id": row[10],
            })
        return signals

    return run_async(_load())


@st.cache_data(ttl=60)
def load_health_report(_store, lookback_days: int = 30):
    """Load health report."""
    async def _load():
        monitor = SignalHealthMonitor(_store)
        return await monitor.generate_report(lookback_days=lookback_days)
    return run_async(_load())


@st.cache_data(ttl=120)
def load_notion_deals(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load deals from Notion pipeline."""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return []

    async def _load():
        import httpx

        headers = {
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        all_deals = []
        has_more = True
        start_cursor = None

        if status_filter and status_filter not in ["All", "All Active"]:
            filter_obj = {"property": "Status", "select": {"equals": status_filter}}
        else:
            active_statuses = ["Source", "Initial Meeting / Call", "Dilligence",
                              "Tracking", "Committed", "Funded"]
            filter_obj = {"or": [{"property": "Status", "select": {"equals": s}}
                                 for s in active_statuses]}

        async with httpx.AsyncClient(timeout=30.0) as client:
            while has_more:
                payload = {
                    "filter": filter_obj,
                    "page_size": 100,
                    "sorts": [{"property": "Status", "direction": "ascending"}]
                }
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = await client.post(
                    f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                    headers=headers,
                    json=payload,
                )

                if resp.status_code != 200:
                    return []

                data = resp.json()
                all_deals.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")
                await asyncio.sleep(0.35)

        deals = []
        for page in all_deals:
            props = page.get("properties", {})

            def get_title(p):
                title = p.get("title", [])
                return title[0].get("text", {}).get("content", "") if title else ""

            def get_select(p):
                sel = p.get("select")
                return sel.get("name") if sel else ""

            def get_text(p):
                rt = p.get("rich_text", [])
                return rt[0].get("text", {}).get("content", "") if rt else ""

            def get_url(p):
                return p.get("url", "")

            def get_number(p):
                return p.get("number", 0) or 0

            def get_multi_select(p):
                ms = p.get("multi_select", [])
                return [item.get("name", "") for item in ms]

            deals.append({
                "page_id": page["id"],
                "company_name": get_title(props.get("Company Name", {})),
                "website": get_url(props.get("Website", {})),
                "status": get_select(props.get("Status", {})),
                "stage": get_select(props.get("Investment Stage", {})),
                "sector": get_select(props.get("Sector", {})),
                "confidence": get_number(props.get("Confidence Score", {})),
                "signal_types": get_multi_select(props.get("Signal Types", {})),
                "why_now": get_text(props.get("Why Now", {})),
                "location": get_text(props.get("Location", {})),
                "created_time": page.get("created_time", ""),
            })

        return deals

    return run_async(_load())


# =============================================================================
# UI COMPONENTS
# =============================================================================

def render_hero(title: str, subtitle: str):
    """Render hero section."""
    st.markdown(f"""
    <div class="hero-section">
        <div class="hero-title">{title}</div>
        <div class="hero-subtitle">{subtitle}</div>
    </div>
    """, unsafe_allow_html=True)


def render_welcome_banner(view: str):
    """Render contextual welcome/help banner for first-time users."""
    # Check if user has dismissed this banner
    dismiss_key = f"dismissed_welcome_{view}"
    if st.session_state.get(dismiss_key, False):
        return

    messages = {
        "Pipeline": {
            "title": "Welcome to Your Deal Pipeline",
            "body": "This is where you'll see all the companies you're evaluating for investment. "
                   "Companies are organized by stage — from newly discovered <strong>'Source'</strong> companies "
                   "all the way to your <strong>'Funded'</strong> portfolio. Click any company card to see details, "
                   "or use the filters on the left to narrow down what you see."
        },
        "Signals": {
            "title": "Understanding Investment Signals",
            "body": "Signals are data points that help us discover promising companies automatically. "
                   "We scan sources like SEC filings, GitHub, Product Hunt, and more. "
                   "The <strong>Match Score</strong> shows how well each company fits our investment criteria — "
                   "higher scores mean better potential fits."
        },
        "Mini-Scout": {
            "title": "Search & Explore Companies",
            "body": "Use Mini-Scout to search through all discovered companies. "
                   "Type a company name, keyword, or industry to find matches. "
                   "You can save your favorite filter combinations as <strong>Presets</strong> for quick access later."
        }
    }

    msg = messages.get(view, messages["Pipeline"])

    col1, col2 = st.columns([20, 1])
    with col1:
        st.markdown(f"""
        <div class="welcome-banner">
            <h3>{msg['title']}</h3>
            <p>{msg['body']}</p>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        if st.button("✕", key=f"dismiss_{view}", help="Dismiss this tip"):
            st.session_state[dismiss_key] = True
            st.rerun()


def render_tooltip(text: str, help_text: str) -> str:
    """Return HTML for text with a help tooltip."""
    return f"""
    <span class="help-tooltip">
        {text}
        <span class="help-icon">?</span>
        <span class="tooltip-text">{help_text}</span>
    </span>
    """


def render_confidence_meter(confidence: float) -> str:
    """Render a visual confidence meter with bar and label."""
    pct = int(confidence * 100)
    if confidence >= 0.7:
        level = "high"
        label = "Strong match"
    elif confidence >= 0.4:
        level = "medium"
        label = "Moderate match"
    else:
        level = "low"
        label = "Weak match"

    return f"""
    <div class="confidence-meter">
        <div class="confidence-bar">
            <div class="confidence-fill {level}" style="width: {pct}%;"></div>
        </div>
        <span class="confidence-label">{pct}% — {label}</span>
    </div>
    """


def render_status_legend():
    """Render a legend explaining all status colors."""
    items_html = ""
    for status in ["Source", "Initial Meeting / Call", "Dilligence", "Tracking", "Committed", "Funded"]:
        color = STATUS_COLORS.get(status, "#6B7280")
        desc = STATUS_DESCRIPTIONS.get(status, "")
        items_html += f"""
        <div class="status-legend-item">
            <div class="status-legend-dot" style="background-color: {color};"></div>
            <span class="status-legend-name">{status}</span>
            <span class="status-legend-desc">{desc}</span>
        </div>
        """

    st.markdown(f"""
    <div class="status-legend">
        <div class="status-legend-title">Status Guide</div>
        {items_html}
    </div>
    """, unsafe_allow_html=True)


def render_empty_state(title: str, message: str, action_text: str = None, icon: str = "📋"):
    """Render a helpful empty state with guidance."""
    action_html = ""
    if action_text:
        action_html = f'<span class="empty-state-action">{action_text}</span>'

    st.markdown(f"""
    <div class="empty-state">
        <div class="empty-state-icon">{icon}</div>
        <h3>{title}</h3>
        <p>{message}</p>
        {action_html}
    </div>
    """, unsafe_allow_html=True)


def get_friendly_source_name(source: str) -> str:
    """Convert technical source name to user-friendly version."""
    return SOURCE_FRIENDLY_NAMES.get(source, source.replace("_", " ").title())


def render_deal_card(deal: Dict, show_status: bool = True):
    """Render a single deal card with refined styling and user-friendly labels."""
    company = deal.get("company_name") or "Unnamed Company"
    website = deal.get("website", "")
    status = deal.get("status", "")
    stage = deal.get("stage", "")
    sector = deal.get("sector", "")
    confidence = deal.get("confidence", 0)
    why_now = deal.get("why_now", "")
    location = deal.get("location", "")
    signal_types = deal.get("signal_types", [])
    page_id = deal.get("page_id", "").replace("-", "")

    # Confidence with user-friendly label
    if confidence >= 0.7:
        conf_class = "confidence-high"
        conf_label = "Strong fit"
        conf_color = "#10B981"
    elif confidence >= 0.4:
        conf_class = "confidence-med"
        conf_label = "Moderate fit"
        conf_color = "#F59E0B"
    else:
        conf_class = "confidence-low"
        conf_label = "Exploring"
        conf_color = "#6B7280"

    # Status color
    status_color = STATUS_COLORS.get(status, "#6B7280")
    status_desc = STATUS_DESCRIPTIONS.get(status, "")

    # Build card HTML with user-friendly elements
    website_html = f'<a href="{website}" target="_blank" class="deal-link">{website}</a>' if website else ""

    # Status badge with tooltip showing description
    status_html = ""
    if show_status and status:
        status_html = f'''
        <span class="help-tooltip">
            <span class="status-badge" style="background-color: {status_color}20; color: {status_color};">{status}</span>
            <span class="tooltip-text">{status_desc}</span>
        </span>
        '''

    meta_parts = []
    if stage:
        meta_parts.append(f"Stage: {stage}")
    if sector:
        meta_parts.append(sector)
    if location:
        meta_parts.append(f"📍 {location}")

    # Convert signal types to friendly names
    friendly_signals = []
    for sig in signal_types[:3]:
        friendly_signals.append(get_friendly_source_name(sig) if sig in SOURCE_FRIENDLY_NAMES else sig)
    signals_html = ""
    if friendly_signals:
        signals_html = f'<div class="deal-meta" style="margin-top: 0.5rem;">Found via: {" · ".join(friendly_signals)}</div>'

    notion_url = f"https://notion.so/{page_id}" if page_id else ""

    # Why now with better formatting
    why_now_html = ""
    if why_now:
        truncated = why_now[:120] + "..." if len(why_now) > 120 else why_now
        why_now_html = f'<div class="deal-meta" style="margin-top: 0.75rem; color: #737373; font-style: italic;">"Why now: {truncated}"</div>'

    st.markdown(f"""
    <div class="deal-card">
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div style="flex: 1;">
                <div class="deal-name">{company}</div>
                {f'<div class="deal-meta" style="margin-bottom: 0.5rem;">{website_html}</div>' if website_html else ''}
                <div class="deal-meta">{" · ".join(meta_parts)}</div>
                {why_now_html}
            </div>
            <div style="text-align: right; min-width: 140px;">
                {status_html}
                <div style="margin-top: 0.75rem;">
                    <div class="confidence-meter" style="justify-content: flex-end;">
                        <div class="confidence-bar" style="width: 50px;">
                            <div class="confidence-fill {conf_class.replace('confidence-', '')}" style="width: {int(confidence * 100)}%;"></div>
                        </div>
                    </div>
                    <div style="font-size: 0.75rem; color: {conf_color}; margin-top: 4px;">{conf_label} ({confidence:.0%})</div>
                </div>
                {signals_html}
                {f'<a href="{notion_url}" target="_blank" class="deal-link" style="display: block; margin-top: 0.75rem;">View full details →</a>' if notion_url else ''}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_pipeline_section(deals: List[Dict], status: str):
    """Render a pipeline section with deals and helpful context."""
    status_deals = [d for d in deals if d.get("status") == status]
    if not status_deals:
        return

    status_color = STATUS_COLORS.get(status, "#6B7280")
    status_desc = STATUS_DESCRIPTIONS.get(status, "")
    count = len(status_deals)

    # More descriptive expander header
    deal_word = "company" if count == 1 else "companies"
    with st.expander(f"**{status}** — {count} {deal_word}", expanded=(status == "Source")):
        # Show status description as context
        if status_desc:
            st.markdown(f'<p class="section-intro" style="margin-top: 0;">{status_desc}</p>', unsafe_allow_html=True)
        for deal in status_deals:
            render_deal_card(deal, show_status=False)


def render_stats_overview(deals: List[Dict]):
    """Render statistics overview."""
    total = len(deals)
    by_status = {}
    by_stage = {}
    by_sector = {}

    for deal in deals:
        status = deal.get("status", "Unknown")
        stage = deal.get("stage") or "Unknown"
        sector = deal.get("sector") or "Unknown"

        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_sector[sector] = by_sector.get(sector, 0) + 1

    # Top metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Deals", total)
    with col2:
        active = sum(v for k, v in by_status.items() if k not in ["Passed", "Lost", "Funded"])
        st.metric("Active Pipeline", active)
    with col3:
        st.metric("Portfolio", by_status.get("Funded", 0))
    with col4:
        st.metric("New This Week", by_status.get("Source", 0))

    st.markdown("---")

    # Breakdown
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown('<div class="section-header">By Status</div>', unsafe_allow_html=True)
        for status in ["Source", "Initial Meeting / Call", "Dilligence", "Tracking", "Committed", "Funded"]:
            count = by_status.get(status, 0)
            if count > 0:
                color = STATUS_COLORS.get(status, "#6B7280")
                st.markdown(f"""
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span style="color: {color};">● {status}</span>
                    <span style="color: #A3A3A3;">{count}</span>
                </div>
                """, unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="section-header">By Stage</div>', unsafe_allow_html=True)
        for stage, count in sorted(by_stage.items(), key=lambda x: -x[1])[:6]:
            if stage and stage != "Unknown":
                st.markdown(f"""
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span style="color: #FAFAFA;">{stage}</span>
                    <span style="color: #A3A3A3;">{count}</span>
                </div>
                """, unsafe_allow_html=True)

    with col3:
        st.markdown('<div class="section-header">By Sector</div>', unsafe_allow_html=True)
        for sector, count in sorted(by_sector.items(), key=lambda x: -x[1])[:6]:
            if sector and sector != "Unknown":
                st.markdown(f"""
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span style="color: #FAFAFA;">{sector}</span>
                    <span style="color: #A3A3A3;">{count}</span>
                </div>
                """, unsafe_allow_html=True)


def render_signals_view(signals: List[Dict], filters: Dict):
    """Render signals view with filtering and user-friendly labels."""
    filtered = signals

    if filters.get("source_filter"):
        filtered = [s for s in filtered if s["source_api"] == filters["source_filter"]]
    if filters.get("min_confidence", 0) > 0:
        filtered = [s for s in filtered if s["confidence"] >= filters["min_confidence"]]

    if not filtered:
        render_empty_state(
            "No companies found",
            "Try adjusting your filters or check back later as new signals are discovered automatically.",
            icon="🔍"
        )
        return

    st.markdown(f'<div class="section-header">Discovered Companies ({len(filtered)})</div>', unsafe_allow_html=True)

    for signal in filtered[:30]:
        company = signal["company_name"]
        source = signal["source_api"]
        sig_type = signal["signal_type"]
        confidence = signal["confidence"]
        status = signal["processing_status"]

        # User-friendly source name
        friendly_source = get_friendly_source_name(source)

        # User-friendly confidence labels
        if confidence >= 0.7:
            conf_class = "high"
            conf_label = "Strong match"
            conf_color = "#10B981"
        elif confidence >= 0.4:
            conf_class = "medium"
            conf_label = "Worth reviewing"
            conf_color = "#F59E0B"
        else:
            conf_class = "low"
            conf_label = "Early signal"
            conf_color = "#6B7280"

        # User-friendly status labels
        status_labels = {
            "pending": ("⏳", "Awaiting review"),
            "pushed": ("✓", "Added to pipeline"),
            "rejected": ("✕", "Not a fit")
        }
        status_icon, status_text = status_labels.get(status, ("○", status))

        st.markdown(f"""
        <div class="deal-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <div class="deal-name">{company}</div>
                    <div class="deal-meta">Found via {friendly_source}</div>
                </div>
                <div style="text-align: right;">
                    <div class="confidence-meter" style="justify-content: flex-end; margin-bottom: 4px;">
                        <div class="confidence-bar" style="width: 50px;">
                            <div class="confidence-fill {conf_class}" style="width: {int(confidence * 100)}%;"></div>
                        </div>
                    </div>
                    <div style="font-size: 0.75rem; color: {conf_color};">{conf_label}</div>
                    <div class="deal-meta" style="margin-top: 0.5rem;">{status_icon} {status_text}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main dashboard entry point."""
    # Initialize session state for onboarding
    if "first_visit" not in st.session_state:
        st.session_state.first_visit = True

    # Check data sources
    has_notion = bool(NOTION_API_KEY and NOTION_DATABASE_ID)
    has_db = Path(DB_PATH).exists()

    if not has_notion and not has_db:
        render_empty_state(
            "Welcome to Discovery Engine",
            "Connect your Notion database to start seeing your deal pipeline. "
            "You'll need to set up your NOTION_API_KEY and NOTION_DATABASE_ID environment variables.",
            action_text="View setup guide",
            icon="🚀"
        )
        return

    # Sidebar with improved UX
    with st.sidebar:
        st.markdown("""
        <div style="padding: 1rem 0; border-bottom: 1px solid #333;">
            <div style="font-family: 'DM Serif Display', serif; font-size: 1.5rem; color: #FAFAFA;">
                ◆ Discovery Engine
            </div>
            <div style="font-family: 'DM Sans', sans-serif; font-size: 0.8rem; color: #737373; margin-top: 0.25rem;">
                Press On Ventures Deal Sourcing
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # View selector with helpful descriptions
        st.markdown('<p class="filter-header" style="font-size: 0.65rem; color: #737373; margin-bottom: 0.5rem;">CHOOSE A VIEW</p>', unsafe_allow_html=True)

        view_options = []
        view_descriptions = {
            "Pipeline": "Your deal flow organized by stage",
            "Signals": "Newly discovered companies",
            "Mini-Scout": "Search all companies"
        }

        if has_notion and has_db:
            view_options = ["Pipeline", "Signals", "Mini-Scout"]
        elif has_notion:
            view_options = ["Pipeline", "Mini-Scout"]
        else:
            view_options = ["Signals", "Mini-Scout"]

        view = st.radio(
            "View",
            view_options,
            label_visibility="collapsed",
            help="Switch between different views of your data"
        )

        # Show description for current view
        st.markdown(f'<p style="font-size: 0.75rem; color: #A3A3A3; margin-top: -0.5rem;">{view_descriptions.get(view, "")}</p>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("↻ Refresh Data", use_container_width=True, help="Reload the latest data"):
            st.cache_data.clear()
            st.rerun()

        # Quick help section in sidebar
        st.markdown("---")
        with st.expander("💡 Quick Help"):
            st.markdown("""
            **Match Score** shows how well a company fits our investment thesis.
            - 🟢 **Strong** (70%+): Great fit, prioritize review
            - 🟡 **Moderate** (40-70%): Worth investigating
            - 🔴 **Early** (<40%): Needs more signals

            **Need help?** Contact the team for support.
            """)

    # ==========================================================================
    # PIPELINE VIEW
    # ==========================================================================
    if view == "Pipeline":
        render_hero("Deal Pipeline", "All the companies you're evaluating, organized by stage")

        # Welcome banner for first-time users
        render_welcome_banner("Pipeline")

        # Filters in sidebar with better labels
        with st.sidebar:
            st.markdown('<div class="section-header">Filter Companies</div>', unsafe_allow_html=True)

            # Friendly status filter with descriptions
            status_options = ["All Active", "All"] + NOTION_STATUSES
            status_filter = st.selectbox(
                "Show companies in stage",
                status_options,
                label_visibility="collapsed",
                help="Filter to see only companies in a specific stage"
            )

            # Show legend button
            if st.checkbox("Show stage guide", value=False, help="Learn what each stage means"):
                render_status_legend()

        # Load data
        deals = load_notion_deals(status_filter)

        if not deals:
            if not NOTION_API_KEY:
                render_empty_state(
                    "Connect Your Pipeline",
                    "Set up your Notion API key to see your deal pipeline here. "
                    "Once connected, you'll see all your companies organized by stage.",
                    action_text="View setup instructions",
                    icon="🔗"
                )
            else:
                render_empty_state(
                    "No Companies Yet",
                    "Your pipeline is empty. As you discover and add companies, they'll appear here organized by stage.",
                    icon="📋"
                )
            return

        # Tabs with clearer labels
        tab1, tab2 = st.tabs(["📋 COMPANIES", "📊 OVERVIEW"])

        with tab1:
            # Quick summary
            source_count = len([d for d in deals if d.get("status") == "Source"])
            if source_count > 0:
                st.markdown(f"""
                <div style="background: rgba(245, 158, 11, 0.1); border-left: 3px solid #F59E0B; padding: 0.75rem 1rem; border-radius: 0 8px 8px 0; margin-bottom: 1.5rem;">
                    <strong style="color: #F59E0B;">{source_count} new {'company' if source_count == 1 else 'companies'}</strong>
                    <span style="color: #A3A3A3;"> ready for your review in "Source"</span>
                </div>
                """, unsafe_allow_html=True)

            # Pipeline sections
            for status in ["Source", "Initial Meeting / Call", "Dilligence", "Tracking", "Committed", "Funded"]:
                render_pipeline_section(deals, status)

        with tab2:
            st.markdown('<p class="section-intro">A quick overview of your entire pipeline — see where your deals are concentrated.</p>', unsafe_allow_html=True)
            render_stats_overview(deals)

    # ==========================================================================
    # SIGNALS VIEW
    # ==========================================================================
    elif view == "Signals":
        render_hero("Discovered Companies", "Companies found automatically from 10+ data sources")

        # Welcome banner
        render_welcome_banner("Signals")

        if not has_db:
            render_empty_state(
                "Database Not Found",
                "The signals database hasn't been set up yet. Run the discovery pipeline to start finding companies.",
                icon="⚙️"
            )
            return

        store = get_store()

        # Filters with user-friendly labels
        with st.sidebar:
            st.markdown('<div class="section-header">Filter Results</div>', unsafe_allow_html=True)

            days = st.selectbox(
                "Time period",
                [7, 14, 30, 90],
                format_func=lambda x: f"Last {x} days",
                help="Show companies discovered in this time period"
            )

            # User-friendly source names
            source_options = ["All Sources"] + list(SOURCE_FRIENDLY_NAMES.keys())
            source_labels = ["All Sources"] + list(SOURCE_FRIENDLY_NAMES.values())
            source_idx = st.selectbox(
                "Found via",
                range(len(source_options)),
                format_func=lambda i: source_labels[i],
                help="Filter by where we found the company"
            )
            source = source_options[source_idx] if source_idx > 0 else "All"

            st.markdown('<p style="font-size: 0.7rem; color: #737373; margin-top: 1rem; margin-bottom: 0.25rem;">MINIMUM MATCH SCORE</p>', unsafe_allow_html=True)
            min_conf = st.slider(
                "Min Match Score",
                0.0, 1.0, 0.0, 0.1,
                format="%.0f%%",
                label_visibility="collapsed",
                help="Only show companies with at least this match score"
            )

            # Visual legend for match scores
            st.markdown("""
            <div style="font-size: 0.7rem; color: #737373; margin-top: 0.5rem;">
                <div style="display: flex; align-items: center; margin-bottom: 4px;">
                    <span style="color: #10B981;">●</span>
                    <span style="margin-left: 6px;">70%+ Strong match</span>
                </div>
                <div style="display: flex; align-items: center; margin-bottom: 4px;">
                    <span style="color: #F59E0B;">●</span>
                    <span style="margin-left: 6px;">40-70% Worth reviewing</span>
                </div>
                <div style="display: flex; align-items: center;">
                    <span style="color: #6B7280;">●</span>
                    <span style="margin-left: 6px;">&lt;40% Early signal</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        signals = load_signals(store, days_back=days)
        health = load_health_report(store)

        # Top metrics with user-friendly labels
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Companies Found", len(signals), help="Total companies discovered")
        with col2:
            high_conf = sum(1 for s in signals if s["confidence"] >= 0.7)
            st.metric("Strong Matches", high_conf, help="Companies with 70%+ match score")
        with col3:
            pending = sum(1 for s in signals if s["processing_status"] == "pending")
            st.metric("Awaiting Review", pending, help="Companies not yet added to pipeline")
        with col4:
            # User-friendly health status
            health_labels = {
                "HEALTHY": ("✓ System OK", "#10B981"),
                "DEGRADED": ("⚠ Some issues", "#F59E0B"),
                "CRITICAL": ("✕ Problems", "#EF4444")
            }
            health_status = health.overall_status if health else "UNKNOWN"
            health_label, health_color = health_labels.get(health_status, ("Unknown", "#6B7280"))
            st.markdown(f"""
            <div style="text-align: center;">
                <div style="font-size: 0.7rem; color: #A3A3A3; text-transform: uppercase; letter-spacing: 0.1em;">System Status</div>
                <div style="font-size: 1.5rem; color: {health_color}; font-family: 'DM Serif Display', serif; margin-top: 4px;">{health_label}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        filters = {
            "source_filter": source if source != "All Sources" and source != "All" else None,
            "min_confidence": min_conf,
        }
        render_signals_view(signals, filters)

    # ==========================================================================
    # MINI-SCOUT VIEW
    # ==========================================================================
    elif view == "Mini-Scout":
        # Show welcome banner before Mini-Scout page renders
        render_welcome_banner("Mini-Scout")
        store = get_store()
        render_mini_scout_page(store)

    # Footer
    st.markdown("""
    <div style="text-align: center; padding: 2rem 0; color: #525252; font-size: 0.75rem;">
        Updated {timestamp}
    </div>
    """.format(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M")), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
