"""
Inbox Page

The primary view for the Discovery Engine inbox UX.
Displays companies as actionable cards with Track/Pass/Pipeline buttons.
Calls FastAPI backend for all data and actions.
"""

import streamlit as st
import httpx
from typing import List, Dict, Any, Optional

from dashboard.components.company_card import render_company_card
from dashboard.api_client import API_BASE_URL, check_api_connection


# =============================================================================
# CONFIGURATION
# =============================================================================

STATUS_OPTIONS = {
    "inbox": "New Deals",
    "tracking": "Tracking",
    "passed": "Passed",
    "pipeline_requested": "In Pipeline",
}

STATUS_DESCRIPTIONS = {
    "inbox": "New companies waiting for your review",
    "tracking": "Companies you're monitoring for updates",
    "passed": "Companies you've decided not to pursue",
    "pipeline_requested": "Companies queued for your Notion pipeline",
}


# =============================================================================
# API CLIENT
# =============================================================================

def get_api_client() -> httpx.Client:
    """Get HTTP client for API calls."""
    return httpx.Client(base_url=API_BASE_URL, timeout=10.0)


@st.cache_data(ttl=30)
def fetch_inbox_companies(
    status: str = "inbox",
    min_confidence: float = 0.0,
    page: int = 1,
    page_size: int = 25,
) -> Dict[str, Any]:
    """Fetch companies from API."""
    try:
        with get_api_client() as client:
            response = client.get(
                "/companies/inbox",
                params={
                    "status": status,
                    "min_confidence": min_confidence,
                    "page": page,
                    "page_size": page_size,
                }
            )
            if response.status_code == 200:
                return response.json()
            else:
                return {"companies": [], "error": f"API error: {response.status_code}"}
    except httpx.ConnectError:
        return {"companies": [], "error": "Cannot connect to API server"}
    except Exception as e:
        return {"companies": [], "error": str(e)}


def fetch_company_detail(canonical_key: str) -> Optional[Dict[str, Any]]:
    """Fetch single company details."""
    try:
        with get_api_client() as client:
            response = client.get(f"/companies/{canonical_key}")
            if response.status_code == 200:
                return response.json()
    except:
        pass
    return None


# =============================================================================
# CSS STYLES
# =============================================================================

def inject_inbox_css():
    """Inject custom CSS for inbox page."""
    st.markdown("""
    <style>
    /* Press On Ventures Theme */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@400;500&display=swap');

    /* Hero Section */
    .inbox-hero {
        background: linear-gradient(135deg, #292929 0%, #3d3d3d 100%);
        padding: 32px;
        border-radius: 16px;
        margin-bottom: 24px;
        color: white;
    }
    .inbox-hero h1 {
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        font-size: 28px;
        margin: 0 0 8px 0;
    }
    .inbox-hero p {
        font-family: 'Poppins', sans-serif;
        color: #E0D8D1;
        margin: 0;
        font-size: 16px;
    }

    /* Stats Bar */
    .stats-bar {
        display: flex;
        gap: 24px;
        margin-bottom: 24px;
    }
    .stat-item {
        background: white;
        border: 1px solid #E0D8D1;
        border-radius: 12px;
        padding: 16px 24px;
        text-align: center;
        flex: 1;
    }
    .stat-value {
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        font-size: 32px;
        color: #292929;
    }
    .stat-label {
        font-family: 'Poppins', sans-serif;
        font-size: 14px;
        color: #6B7280;
        margin-top: 4px;
    }

    /* Tab Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: #F9FAFB;
        padding: 8px;
        border-radius: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        font-family: 'Inter', sans-serif;
        font-weight: 500;
        border-radius: 8px;
        padding: 8px 16px;
    }
    .stTabs [aria-selected="true"] {
        background: white !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }

    /* Empty State */
    .empty-state {
        text-align: center;
        padding: 48px;
        color: #6B7280;
    }
    .empty-state-icon {
        font-size: 48px;
        margin-bottom: 16px;
    }

    /* Filter Pills */
    .filter-pill {
        display: inline-block;
        background: #E0D8D1;
        color: #292929;
        padding: 4px 12px;
        border-radius: 16px;
        font-size: 12px;
        margin-right: 8px;
    }
    </style>
    """, unsafe_allow_html=True)


# =============================================================================
# UI COMPONENTS
# =============================================================================

def render_hero():
    """Render hero section."""
    st.markdown("""
    <div class="inbox-hero">
        <h1>Deal Inbox</h1>
        <p>Review and act on discovered companies</p>
    </div>
    """, unsafe_allow_html=True)


def render_stats_bar(stats: Dict[str, int]):
    """Render stats overview bar."""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("New Deals", stats.get("inbox", 0))
    with col2:
        st.metric("Tracking", stats.get("tracking", 0))
    with col3:
        st.metric("In Pipeline", stats.get("pipeline_requested", 0))
    with col4:
        st.metric("Passed", stats.get("passed", 0))


def render_empty_state(status: str):
    """Render empty state for a status."""
    messages = {
        "inbox": ("No new deals", "New companies will appear here when discovered"),
        "tracking": ("Nothing tracked yet", "Track companies to monitor them here"),
        "passed": ("No passed companies", "Companies you pass on will appear here"),
        "pipeline_requested": ("Pipeline is empty", "Add companies to pipeline to see them here"),
    }
    title, subtitle = messages.get(status, ("No companies", ""))

    st.markdown(f"""
    <div class="empty-state">
        <div class="empty-state-icon">{"📬" if status == "inbox" else "📋"}</div>
        <h3>{title}</h3>
        <p>{subtitle}</p>
    </div>
    """, unsafe_allow_html=True)


def render_api_error(error: str):
    """Render API connection error."""
    st.error(f"""
    **Cannot connect to API server**

    {error}

    Make sure the API server is running:
    ```
    uvicorn api.main:app --reload --port 8000
    ```
    """)


def render_company_list(
    companies: List[Dict[str, Any]],
    status: str,
) -> bool:
    """
    Render list of company cards.

    Returns True if any action was taken (triggers refresh).
    """
    action_taken = False

    for company in companies:
        result = render_company_card(
            company=company,
            show_actions=(status != "passed"),
        )
        if result:
            action_taken = True

    return action_taken


# =============================================================================
# SIDEBAR CONTROLS
# =============================================================================

def render_sidebar_controls() -> Dict[str, Any]:
    """Render sidebar filter controls. Returns filter settings."""
    st.sidebar.markdown("### Filters")

    # Confidence slider
    min_confidence = st.sidebar.slider(
        "Minimum Match Score",
        min_value=0,
        max_value=100,
        value=0,
        step=5,
        format="%d%%",
        help="Filter companies by minimum confidence score"
    ) / 100

    # Page size
    page_size = st.sidebar.selectbox(
        "Companies per page",
        options=[10, 25, 50, 100],
        index=1,
    )

    st.sidebar.markdown("---")

    # Refresh button
    if st.sidebar.button("Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # API status
    api_healthy = check_api_connection()
    if api_healthy:
        st.sidebar.success("API Connected")
    else:
        st.sidebar.error("API Disconnected")

    return {
        "min_confidence": min_confidence,
        "page_size": page_size,
    }


# =============================================================================
# MAIN PAGE
# =============================================================================

def render_inbox_page():
    """Main entry point for inbox page."""
    # Inject CSS
    inject_inbox_css()

    # Render hero
    render_hero()

    # Sidebar controls
    filters = render_sidebar_controls()

    # Check API health first
    if not check_api_connection():
        render_api_error("The API server is not responding.")
        return

    # Create tabs for different statuses
    tabs = st.tabs(["New Deals", "Tracking", "Passed", "Pipeline"])

    tab_statuses = ["inbox", "tracking", "passed", "pipeline_requested"]

    for tab, status in zip(tabs, tab_statuses):
        with tab:
            # Fetch data for this status
            data = fetch_inbox_companies(
                status=status,
                min_confidence=filters["min_confidence"],
                page_size=filters["page_size"],
            )

            # Check for errors
            if data.get("error"):
                render_api_error(data["error"])
                continue

            companies = data.get("companies", [])

            # Show count
            count = len(companies)
            st.caption(f"{count} {'company' if count == 1 else 'companies'}")

            # Render companies or empty state
            if companies:
                action_taken = render_company_list(companies, status)
                if action_taken:
                    # Clear cache and refresh after action
                    st.cache_data.clear()
                    st.rerun()
            else:
                render_empty_state(status)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    st.set_page_config(
        page_title="Deal Inbox | Discovery Engine",
        page_icon="📬",
        layout="wide",
    )
    render_inbox_page()
