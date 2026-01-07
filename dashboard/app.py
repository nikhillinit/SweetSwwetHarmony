"""
Discovery Engine Dashboard - Signal Viewer for Non-Technical Users

A simple Streamlit dashboard for viewing signals and Notion pipeline.

Features:
- View discovery signals with filtering
- View Notion deal pipeline (live from Notion API)
- Pipeline health status
- Direct links to Notion records

Run:
    streamlit run dashboard/app.py

Or from project root:
    python -m streamlit run dashboard/app.py

Environment Variables:
    NOTION_API_KEY - Required for Pipeline tab
    NOTION_DATABASE_ID - Required for Pipeline tab
    DISCOVERY_DB_PATH - Path to signals database (default: signals.db)
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
    "Source",
    "Initial Meeting / Call",
    "Dilligence",
    "Tracking",
    "Committed",
    "Funded",
    "Passed",
    "Lost",
]

# Page config
st.set_page_config(
    page_title="Discovery Engine",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)


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

@st.cache_data(ttl=60)  # Cache for 60 seconds
def load_signals(_store, days_back: int = 7, status_filter: str = "all"):
    """Load signals from database."""
    async def _load():
        if not _store._db:
            await _store.initialize()

        # Get date range
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        # Query signals with processing status
        cursor = await _store._db.execute(
            """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at,
                p.status as processing_status, p.notion_page_id
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.created_at >= ?
            ORDER BY s.created_at DESC
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


@st.cache_data(ttl=60)
def load_stats(_store):
    """Load database stats."""
    return run_async(_store.get_stats())


# =============================================================================
# NOTION DATA LOADING
# =============================================================================

@st.cache_data(ttl=120)  # Cache for 2 minutes (Notion rate limits)
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

        # Build filter
        if status_filter and status_filter != "All":
            filter_obj = {
                "property": "Status",
                "select": {"equals": status_filter}
            }
        else:
            # Get all active deals (exclude Passed/Lost by default)
            filter_obj = {
                "or": [
                    {"property": "Status", "select": {"equals": s}}
                    for s in ["Source", "Initial Meeting / Call", "Dilligence", "Tracking", "Committed", "Funded"]
                ]
            }

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
                    st.error(f"Notion API error: {resp.status_code}")
                    return []

                data = resp.json()
                all_deals.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

                # Rate limit protection
                await asyncio.sleep(0.35)

        # Parse deals
        deals = []
        for page in all_deals:
            props = page.get("properties", {})

            # Extract properties safely
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
                "canonical_key": get_text(props.get("Canonical Key", {})),
                "location": get_text(props.get("Location", {})),
                "created_time": page.get("created_time", ""),
            })

        return deals

    return run_async(_load())


def get_notion_stats(deals: List[Dict]) -> Dict[str, Any]:
    """Calculate stats from Notion deals."""
    by_status = {}
    by_stage = {}
    by_sector = {}

    for deal in deals:
        status = deal.get("status", "Unknown")
        stage = deal.get("stage", "Unknown")
        sector = deal.get("sector", "Unknown") or "Unknown"

        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_sector[sector] = by_sector.get(sector, 0) + 1

    return {
        "total": len(deals),
        "by_status": by_status,
        "by_stage": by_stage,
        "by_sector": by_sector,
    }


# =============================================================================
# UI COMPONENTS
# =============================================================================

def render_sidebar():
    """Render sidebar filters."""
    st.sidebar.title("🔍 Discovery Engine")
    st.sidebar.markdown("---")

    # Date range
    days_back = st.sidebar.selectbox(
        "Time Range",
        options=[1, 7, 14, 30, 90],
        index=1,
        format_func=lambda x: f"Last {x} day{'s' if x > 1 else ''}"
    )

    # Source filter
    sources = ["All", "github", "sec_edgar", "companies_house", "domain_whois",
               "job_postings", "product_hunt", "hacker_news", "arxiv", "uspto"]
    source_filter = st.sidebar.selectbox("Source", sources)

    # Confidence filter
    min_confidence = st.sidebar.slider(
        "Min Confidence",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.1
    )

    # Status filter
    status_filter = st.sidebar.selectbox(
        "Processing Status",
        ["all", "pending", "pushed", "rejected"]
    )

    st.sidebar.markdown("---")

    # Refresh button
    if st.sidebar.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    return {
        "days_back": days_back,
        "source_filter": source_filter if source_filter != "All" else None,
        "min_confidence": min_confidence,
        "status_filter": status_filter,
    }


def render_metrics(signals, health_report):
    """Render top-level metrics."""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Total Signals",
            len(signals),
            help="Signals in selected time range"
        )

    with col2:
        pending = sum(1 for s in signals if s["processing_status"] == "pending")
        st.metric(
            "Pending",
            pending,
            help="Signals awaiting processing"
        )

    with col3:
        high_conf = sum(1 for s in signals if s["confidence"] >= 0.7)
        st.metric(
            "High Confidence",
            high_conf,
            help="Signals with confidence >= 0.7"
        )

    with col4:
        status_color = {
            "HEALTHY": "🟢",
            "DEGRADED": "🟡",
            "CRITICAL": "🔴"
        }
        status = health_report.overall_status if health_report else "UNKNOWN"
        st.metric(
            "Pipeline Health",
            f"{status_color.get(status, '⚪')} {status}",
        )


def render_signal_table(signals, filters):
    """Render signal table with filters applied."""
    # Apply filters
    filtered = signals

    if filters["source_filter"]:
        filtered = [s for s in filtered if s["source_api"] == filters["source_filter"]]

    if filters["min_confidence"] > 0:
        filtered = [s for s in filtered if s["confidence"] >= filters["min_confidence"]]

    if filters["status_filter"] != "all":
        filtered = [s for s in filtered if s["processing_status"] == filters["status_filter"]]

    if not filtered:
        st.info("No signals match the current filters.")
        return

    st.subheader(f"📋 Signals ({len(filtered)})")

    # Create display data
    for signal in filtered[:50]:  # Limit to 50 for performance
        with st.container():
            col1, col2, col3, col4 = st.columns([3, 1, 1, 1])

            with col1:
                company = signal["company_name"]
                canonical = signal["canonical_key"]
                st.markdown(f"**{company}**")
                st.caption(f"`{canonical}`")

            with col2:
                conf = signal["confidence"]
                conf_color = "🟢" if conf >= 0.7 else "🟡" if conf >= 0.4 else "🔴"
                st.markdown(f"{conf_color} **{conf:.2f}**")

            with col3:
                st.markdown(f"📡 {signal['source_api']}")
                st.caption(signal["signal_type"])

            with col4:
                status = signal["processing_status"]
                status_icon = {"pending": "⏳", "pushed": "✅", "rejected": "❌"}.get(status, "❓")
                st.markdown(f"{status_icon} {status}")

                # Notion link if pushed
                if signal["notion_page_id"] and NOTION_DATABASE_ID:
                    notion_url = f"https://notion.so/{signal['notion_page_id'].replace('-', '')}"
                    st.markdown(f"[Open in Notion]({notion_url})")

            st.markdown("---")


def render_health_details(health_report):
    """Render health report details."""
    if not health_report:
        st.warning("Health report not available")
        return

    st.subheader("🏥 Pipeline Health Details")

    # Source health table
    if health_report.source_health:
        cols = st.columns(min(len(health_report.source_health), 4))

        for idx, (source_name, health) in enumerate(health_report.source_health.items()):
            with cols[idx % 4]:
                status_icon = {"HEALTHY": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(health.status, "⚪")
                st.markdown(f"**{status_icon} {source_name}**")
                st.caption(f"{health.signal_count} signals")
                st.caption(f"Avg confidence: {health.avg_confidence:.2f}")

                if health.warnings:
                    for w in health.warnings[:2]:
                        st.warning(w, icon="⚠️")

    # Anomalies
    if health_report.anomalies:
        st.subheader("⚠️ Anomalies Detected")
        for anomaly in health_report.anomalies[:5]:
            severity_icon = "🔴" if anomaly.severity == "CRITICAL" else "🟡"
            st.warning(f"{severity_icon} **{anomaly.anomaly_type}**: {anomaly.description}")


def render_stats(stats):
    """Render database statistics."""
    st.subheader("📊 Statistics")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Signals by Type**")
        if stats.get("signals_by_type"):
            for sig_type, count in sorted(stats["signals_by_type"].items(), key=lambda x: -x[1]):
                st.text(f"  {sig_type}: {count}")
        else:
            st.text("  No signals yet")

    with col2:
        st.markdown("**Processing Status**")
        if stats.get("processing_status"):
            for status, count in stats["processing_status"].items():
                icon = {"pending": "⏳", "pushed": "✅", "rejected": "❌"}.get(status, "❓")
                st.text(f"  {icon} {status}: {count}")
        else:
            st.text("  No processing data")


def render_pipeline_deals(deals: List[Dict], status_filter: str):
    """Render Notion pipeline deals."""
    if not deals:
        if not NOTION_API_KEY:
            st.warning("Set NOTION_API_KEY environment variable to view pipeline")
        elif not NOTION_DATABASE_ID:
            st.warning("Set NOTION_DATABASE_ID environment variable to view pipeline")
        else:
            st.info("No deals found matching the filter.")
        return

    # Group by status for better visualization
    by_status = {}
    for deal in deals:
        status = deal.get("status", "Unknown")
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(deal)

    # Status order
    status_order = ["Source", "Initial Meeting / Call", "Dilligence", "Tracking", "Committed", "Funded", "Passed", "Lost"]

    for status in status_order:
        if status not in by_status:
            continue

        status_deals = by_status[status]
        status_icons = {
            "Source": "🆕",
            "Initial Meeting / Call": "📞",
            "Dilligence": "🔍",
            "Tracking": "👀",
            "Committed": "🤝",
            "Funded": "💰",
            "Passed": "❌",
            "Lost": "💔",
        }

        with st.expander(f"{status_icons.get(status, '📋')} {status} ({len(status_deals)})", expanded=(status == "Source")):
            for deal in status_deals:
                with st.container():
                    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])

                    with col1:
                        company = deal["company_name"] or "Unnamed"
                        website = deal.get("website", "")
                        st.markdown(f"**{company}**")
                        if website:
                            st.caption(f"[{website}]({website})")
                        if deal.get("why_now"):
                            st.caption(f"💡 {deal['why_now'][:100]}...")

                    with col2:
                        stage = deal.get("stage", "")
                        if stage:
                            st.markdown(f"📊 {stage}")
                        sector = deal.get("sector", "")
                        if sector:
                            st.caption(sector)

                    with col3:
                        conf = deal.get("confidence", 0)
                        if conf:
                            conf_color = "🟢" if conf >= 0.7 else "🟡" if conf >= 0.4 else "🔴"
                            st.markdown(f"{conf_color} **{conf:.2f}**")
                        signal_types = deal.get("signal_types", [])
                        if signal_types:
                            st.caption(", ".join(signal_types[:3]))

                    with col4:
                        page_id = deal["page_id"].replace("-", "")
                        notion_url = f"https://notion.so/{page_id}"
                        st.markdown(f"[Open in Notion]({notion_url})")
                        if deal.get("location"):
                            st.caption(f"📍 {deal['location']}")

                    st.markdown("---")


def render_pipeline_stats(deals: List[Dict]):
    """Render pipeline statistics."""
    if not deals:
        st.info("No pipeline data available")
        return

    stats = get_notion_stats(deals)

    st.subheader("📊 Pipeline Overview")

    # Top metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Deals", stats["total"])

    with col2:
        active = sum(v for k, v in stats["by_status"].items() if k not in ["Passed", "Lost", "Funded"])
        st.metric("Active Pipeline", active)

    with col3:
        funded = stats["by_status"].get("Funded", 0)
        st.metric("Funded", funded)

    with col4:
        source = stats["by_status"].get("Source", 0)
        st.metric("New (Source)", source)

    st.markdown("---")

    # Breakdown charts
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**By Status**")
        for status, count in sorted(stats["by_status"].items(), key=lambda x: -x[1]):
            st.text(f"  {status}: {count}")

    with col2:
        st.markdown("**By Stage**")
        for stage, count in sorted(stats["by_stage"].items(), key=lambda x: -x[1]):
            if stage:
                st.text(f"  {stage}: {count}")

    with col3:
        st.markdown("**By Sector**")
        for sector, count in sorted(stats["by_sector"].items(), key=lambda x: -x[1])[:8]:
            if sector and sector != "Unknown":
                st.text(f"  {sector}: {count}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main dashboard entry point."""
    # Main content
    st.title("🔍 Discovery Engine Dashboard")

    # Check Notion connection
    has_notion = bool(NOTION_API_KEY and NOTION_DATABASE_ID)
    has_db = Path(DB_PATH).exists()

    if not has_notion and not has_db:
        st.error("No data sources configured!")
        st.info("Set NOTION_API_KEY + NOTION_DATABASE_ID for pipeline view")
        st.info("Or run the pipeline to create signals database")
        return

    # Sidebar
    st.sidebar.title("🔍 Discovery Engine")
    st.sidebar.markdown("---")

    # Data source selection
    if has_notion and has_db:
        data_source = st.sidebar.radio("Data Source", ["Pipeline (Notion)", "Signals (Local)"])
    elif has_notion:
        data_source = "Pipeline (Notion)"
        st.sidebar.info("Viewing Notion Pipeline")
    else:
        data_source = "Signals (Local)"
        st.sidebar.info("Viewing Local Signals")

    st.sidebar.markdown("---")

    # Refresh button
    if st.sidebar.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    # =========================================================================
    # NOTION PIPELINE VIEW
    # =========================================================================
    if data_source == "Pipeline (Notion)":
        st.markdown("Live view of the Notion deal pipeline.")

        # Pipeline-specific filters
        status_filter = st.sidebar.selectbox(
            "Filter by Status",
            ["All Active", "All"] + NOTION_STATUSES,
            index=0
        )

        # Load Notion data
        try:
            if status_filter == "All Active":
                deals = load_notion_deals(None)  # Active deals only
            elif status_filter == "All":
                # Load all including Passed/Lost
                deals = load_notion_deals("All")
            else:
                deals = load_notion_deals(status_filter)
        except Exception as e:
            st.error(f"Failed to load from Notion: {e}")
            deals = []

        # Tabs for pipeline
        tab1, tab2 = st.tabs(["📋 Deals", "📊 Stats"])

        with tab1:
            render_pipeline_deals(deals, status_filter)

        with tab2:
            render_pipeline_stats(deals)

        # Footer
        st.markdown("---")
        notion_url = f"https://notion.so/{NOTION_DATABASE_ID.replace('-', '')}"
        st.caption(f"[Open in Notion]({notion_url}) | Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # =========================================================================
    # LOCAL SIGNALS VIEW
    # =========================================================================
    else:
        st.markdown("View of discovery signals from the local database.")

        if not has_db:
            st.error(f"Database not found at `{DB_PATH}`")
            st.info("Run: `python run_pipeline.py collect --collectors github`")
            return

        # Get store
        try:
            store = get_store()
        except Exception as e:
            st.error(f"Failed to connect to database: {e}")
            return

        # Render signal-specific sidebar filters
        # Date range
        days_back = st.sidebar.selectbox(
            "Time Range",
            options=[1, 7, 14, 30, 90],
            index=1,
            format_func=lambda x: f"Last {x} day{'s' if x > 1 else ''}"
        )

        # Source filter
        sources = ["All", "github", "sec_edgar", "companies_house", "domain_whois",
                   "job_postings", "product_hunt", "hacker_news", "arxiv", "uspto"]
        source_filter = st.sidebar.selectbox("Source", sources)

        # Confidence filter
        min_confidence = st.sidebar.slider(
            "Min Confidence",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.1
        )

        # Status filter
        status_filter = st.sidebar.selectbox(
            "Processing Status",
            ["all", "pending", "pushed", "rejected"]
        )

        filters = {
            "days_back": days_back,
            "source_filter": source_filter if source_filter != "All" else None,
            "min_confidence": min_confidence,
            "status_filter": status_filter,
        }

        # Load data
        try:
            signals = load_signals(store, days_back=filters["days_back"])
            health_report = load_health_report(store)
            stats = load_stats(store)
        except Exception as e:
            st.error(f"Failed to load data: {e}")
            return

        # Metrics row
        render_metrics(signals, health_report)

        st.markdown("---")

        # Tabs for different views
        tab1, tab2, tab3 = st.tabs(["📋 Signals", "🏥 Health", "📊 Stats"])

        with tab1:
            render_signal_table(signals, filters)

        with tab2:
            render_health_details(health_report)

        with tab3:
            render_stats(stats)

        # Footer
        st.markdown("---")
        st.caption(f"Database: `{DB_PATH}` | Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
