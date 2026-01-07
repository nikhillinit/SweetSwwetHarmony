"""
Discovery Engine Dashboard - Deal Pipeline for Press On Ventures

A refined, editorial-style dashboard for viewing deals and signals.

Design Direction: Editorial/Refined
- Magazine-style typography (DM Serif Display + DM Sans)
- Dark mode with warm accent colors
- Card-based layout with generous whitespace
- Status-driven color coding
- Subtle animations and hover states

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


def render_deal_card(deal: Dict, show_status: bool = True):
    """Render a single deal card with refined styling."""
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

    # Confidence color
    if confidence >= 0.7:
        conf_class = "confidence-high"
        conf_icon = "●"
    elif confidence >= 0.4:
        conf_class = "confidence-med"
        conf_icon = "●"
    else:
        conf_class = "confidence-low"
        conf_icon = "○"

    # Status color
    status_color = STATUS_COLORS.get(status, "#6B7280")

    # Build card HTML
    website_html = f'<a href="{website}" target="_blank" class="deal-link">{website}</a>' if website else ""
    status_html = f'<span class="status-badge" style="background-color: {status_color}20; color: {status_color};">{status}</span>' if show_status and status else ""

    meta_parts = []
    if stage:
        meta_parts.append(stage)
    if sector:
        meta_parts.append(sector)
    if location:
        meta_parts.append(f"📍 {location}")

    signals_html = ""
    if signal_types:
        signals_html = " · ".join(signal_types[:3])

    notion_url = f"https://notion.so/{page_id}" if page_id else ""

    st.markdown(f"""
    <div class="deal-card">
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div style="flex: 1;">
                <div class="deal-name">{company}</div>
                {f'<div class="deal-meta" style="margin-bottom: 0.5rem;">{website_html}</div>' if website_html else ''}
                <div class="deal-meta">{" · ".join(meta_parts)}</div>
                {f'<div class="deal-meta" style="margin-top: 0.75rem; color: #737373; font-style: italic;">"{why_now[:120]}..."</div>' if why_now else ''}
            </div>
            <div style="text-align: right; min-width: 120px;">
                {status_html}
                <div style="margin-top: 0.75rem;">
                    <span class="{conf_class}" style="font-size: 1.5rem;">{conf_icon}</span>
                    <span style="font-family: 'DM Serif Display', serif; font-size: 1.25rem; color: #FAFAFA; margin-left: 0.25rem;">{confidence:.0%}</span>
                </div>
                {f'<div class="deal-meta" style="margin-top: 0.5rem;">{signals_html}</div>' if signals_html else ''}
                {f'<a href="{notion_url}" target="_blank" class="deal-link" style="display: block; margin-top: 0.75rem;">Open in Notion →</a>' if notion_url else ''}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_pipeline_section(deals: List[Dict], status: str):
    """Render a pipeline section with deals."""
    status_deals = [d for d in deals if d.get("status") == status]
    if not status_deals:
        return

    status_color = STATUS_COLORS.get(status, "#6B7280")
    count = len(status_deals)

    with st.expander(f"**{status}** — {count} {'deal' if count == 1 else 'deals'}", expanded=(status == "Source")):
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
    """Render signals view with filtering."""
    filtered = signals

    if filters.get("source_filter"):
        filtered = [s for s in filtered if s["source_api"] == filters["source_filter"]]
    if filters.get("min_confidence", 0) > 0:
        filtered = [s for s in filtered if s["confidence"] >= filters["min_confidence"]]

    if not filtered:
        st.info("No signals match the current filters.")
        return

    st.markdown(f'<div class="section-header">Signals ({len(filtered)})</div>', unsafe_allow_html=True)

    for signal in filtered[:30]:
        company = signal["company_name"]
        source = signal["source_api"]
        sig_type = signal["signal_type"]
        confidence = signal["confidence"]
        status = signal["processing_status"]

        conf_class = "confidence-high" if confidence >= 0.7 else "confidence-med" if confidence >= 0.4 else "confidence-low"
        status_icon = {"pending": "○", "pushed": "●", "rejected": "✕"}.get(status, "○")

        st.markdown(f"""
        <div class="deal-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <div class="deal-name">{company}</div>
                    <div class="deal-meta">{source} · {sig_type}</div>
                </div>
                <div style="text-align: right;">
                    <span class="{conf_class}" style="font-size: 1.25rem;">{confidence:.0%}</span>
                    <div class="deal-meta" style="margin-top: 0.25rem;">{status_icon} {status}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main dashboard entry point."""
    # Check data sources
    has_notion = bool(NOTION_API_KEY and NOTION_DATABASE_ID)
    has_db = Path(DB_PATH).exists()

    if not has_notion and not has_db:
        st.error("No data sources configured")
        return

    # Sidebar
    with st.sidebar:
        st.markdown("""
        <div style="padding: 1rem 0; border-bottom: 1px solid #333;">
            <div style="font-family: 'DM Serif Display', serif; font-size: 1.5rem; color: #FAFAFA;">
                ◆ Discovery
            </div>
            <div style="font-family: 'DM Sans', sans-serif; font-size: 0.8rem; color: #737373; margin-top: 0.25rem;">
                Press On Ventures
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # View selector
        if has_notion and has_db:
            view = st.radio("View", ["Pipeline", "Signals"], label_visibility="collapsed")
        elif has_notion:
            view = "Pipeline"
        else:
            view = "Signals"

        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("↻ Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # ==========================================================================
    # PIPELINE VIEW
    # ==========================================================================
    if view == "Pipeline":
        render_hero("Deal Pipeline", "Live view of your Notion deal flow")

        # Filters in sidebar
        with st.sidebar:
            st.markdown('<div class="section-header">Filters</div>', unsafe_allow_html=True)
            status_filter = st.selectbox(
                "Status",
                ["All Active", "All"] + NOTION_STATUSES,
                label_visibility="collapsed"
            )

        # Load data
        deals = load_notion_deals(status_filter)

        if not deals:
            if not NOTION_API_KEY:
                st.warning("Configure NOTION_API_KEY to view pipeline")
            else:
                st.info("No deals found")
            return

        # Tabs
        tab1, tab2 = st.tabs(["DEALS", "ANALYTICS"])

        with tab1:
            # Pipeline sections
            for status in ["Source", "Initial Meeting / Call", "Dilligence", "Tracking", "Committed", "Funded"]:
                render_pipeline_section(deals, status)

        with tab2:
            render_stats_overview(deals)

    # ==========================================================================
    # SIGNALS VIEW
    # ==========================================================================
    else:
        render_hero("Discovery Signals", "Automated signal detection from 10+ sources")

        if not has_db:
            st.error(f"Database not found: {DB_PATH}")
            return

        store = get_store()

        # Filters
        with st.sidebar:
            st.markdown('<div class="section-header">Filters</div>', unsafe_allow_html=True)
            days = st.selectbox("Time Range", [7, 14, 30, 90], format_func=lambda x: f"Last {x} days")
            source = st.selectbox("Source", ["All", "github", "sec_edgar", "companies_house",
                                             "product_hunt", "hacker_news", "arxiv", "uspto"])
            min_conf = st.slider("Min Confidence", 0.0, 1.0, 0.0, 0.1)

        signals = load_signals(store, days_back=days)
        health = load_health_report(store)

        # Top metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Signals", len(signals))
        with col2:
            st.metric("High Confidence", sum(1 for s in signals if s["confidence"] >= 0.7))
        with col3:
            st.metric("Pending", sum(1 for s in signals if s["processing_status"] == "pending"))
        with col4:
            status_icon = {"HEALTHY": "●", "DEGRADED": "◐", "CRITICAL": "○"}.get(
                health.overall_status if health else "UNKNOWN", "○")
            st.metric("Health", f"{status_icon} {health.overall_status if health else 'N/A'}")

        st.markdown("---")

        filters = {
            "source_filter": source if source != "All" else None,
            "min_confidence": min_conf,
        }
        render_signals_view(signals, filters)

    # Footer
    st.markdown("""
    <div style="text-align: center; padding: 2rem 0; color: #525252; font-size: 0.75rem;">
        Updated {timestamp}
    </div>
    """.format(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M")), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
