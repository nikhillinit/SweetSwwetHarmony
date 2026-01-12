"""
Mini-Scout: Signal search and exploration interface.

Dark theme design inspired by Press On Ventures.
"""
import asyncio
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, Any, List
import json

from storage.signal_store import SignalStore


def run_async(coro):
    """Helper to run async code in Streamlit."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def inject_custom_css():
    """Inject custom CSS for dark, minimal design."""
    st.markdown("""
    <style>
    /* Dark theme - Press On Ventures style */
    .stApp {
        background-color: #0a0a0a;
        color: #E1D8D1;
    }

    /* Main title */
    .main-title {
        font-size: 2.5rem;
        font-weight: 300;
        color: #E1D8D1;
        margin-bottom: 0.25rem;
        letter-spacing: -0.02em;
    }

    .subtitle {
        color: #7a7267;
        font-size: 1rem;
        font-weight: 400;
        margin-bottom: 2rem;
    }

    /* Signal card styling - dark with cream border */
    .signal-card {
        border: 1px solid #2a2520;
        border-left: 3px solid #E1D8D1;
        border-radius: 4px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 0.75rem;
        background: #111111;
        transition: all 0.2s ease;
    }

    .signal-card:hover {
        background: #1a1a1a;
        border-left-color: #fff;
    }

    .signal-company {
        font-size: 1.1rem;
        font-weight: 500;
        color: #E1D8D1;
        margin-bottom: 0.25rem;
    }

    .signal-meta {
        font-size: 0.8rem;
        color: #7a7267;
    }

    .signal-badges {
        display: flex;
        gap: 0.5rem;
    }

    .badge {
        font-size: 0.7rem;
        padding: 0.2rem 0.6rem;
        border-radius: 2px;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    .badge-health {
        background: rgba(16, 185, 129, 0.2);
        color: #10B981;
        border: 1px solid rgba(16, 185, 129, 0.3);
    }

    .badge-travel {
        background: rgba(139, 92, 246, 0.2);
        color: #A78BFA;
        border: 1px solid rgba(139, 92, 246, 0.3);
    }

    .badge-saas {
        background: rgba(236, 72, 153, 0.2);
        color: #F472B6;
        border: 1px solid rgba(236, 72, 153, 0.3);
    }

    .badge-consumer {
        background: rgba(249, 115, 22, 0.2);
        color: #FB923C;
        border: 1px solid rgba(249, 115, 22, 0.3);
    }

    .badge-unknown {
        background: rgba(107, 114, 128, 0.2);
        color: #9CA3AF;
        border: 1px solid rgba(107, 114, 128, 0.3);
    }

    .badge-confidence-high {
        background: rgba(16, 185, 129, 0.15);
        color: #34D399;
        border: 1px solid rgba(16, 185, 129, 0.3);
    }

    .badge-confidence-medium {
        background: rgba(245, 158, 11, 0.15);
        color: #FBBF24;
        border: 1px solid rgba(245, 158, 11, 0.3);
    }

    .badge-confidence-low {
        background: rgba(239, 68, 68, 0.15);
        color: #F87171;
        border: 1px solid rgba(239, 68, 68, 0.3);
    }

    /* Stats bar */
    .stat-card {
        background: #111111;
        border: 1px solid #2a2520;
        border-radius: 4px;
        padding: 1rem;
        text-align: center;
    }

    .stat-value {
        font-size: 1.75rem;
        font-weight: 600;
        color: #E1D8D1;
    }

    .stat-label {
        font-size: 0.7rem;
        color: #7a7267;
        text-transform: uppercase;
        letter-spacing: 0.1em;
    }

    /* Clean divider */
    .divider {
        height: 1px;
        background: #2a2520;
        margin: 1.5rem 0;
    }

    /* Filter section headers */
    .filter-header {
        font-size: 0.7rem;
        font-weight: 600;
        color: #7a7267;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-bottom: 0.5rem;
    }

    /* Hide default Streamlit elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* Dark sidebar */
    [data-testid="stSidebar"] {
        background-color: #0a0a0a;
        border-right: 1px solid #2a2520;
    }

    [data-testid="stSidebar"] .stMarkdown {
        color: #E1D8D1;
    }

    /* Result count */
    .result-count {
        font-size: 0.875rem;
        color: #7a7267;
        margin-bottom: 1rem;
    }

    .result-count strong {
        color: #E1D8D1;
        font-weight: 600;
    }

    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
        background-color: transparent;
        border-bottom: 1px solid #2a2520;
    }

    .stTabs [data-baseweb="tab"] {
        color: #7a7267;
        background-color: transparent;
        border: none;
        padding: 0.75rem 0;
    }

    .stTabs [aria-selected="true"] {
        color: #E1D8D1;
        border-bottom: 2px solid #E1D8D1;
    }

    /* Input styling */
    .stTextInput input {
        background-color: #111111;
        border: 1px solid #2a2520;
        color: #E1D8D1;
    }

    .stTextInput input:focus {
        border-color: #E1D8D1;
    }

    /* Button styling */
    .stButton button {
        background-color: #E1D8D1;
        color: #0a0a0a;
        border: none;
        font-weight: 500;
    }

    .stButton button:hover {
        background-color: #fff;
        color: #0a0a0a;
    }

    /* Multiselect styling */
    .stMultiSelect {
        color: #E1D8D1;
    }

    /* Slider */
    .stSlider > div > div {
        background-color: #2a2520;
    }

    /* Radio buttons */
    .stRadio label {
        color: #E1D8D1 !important;
    }

    /* Metrics */
    [data-testid="stMetricValue"] {
        color: #E1D8D1;
    }

    [data-testid="stMetricLabel"] {
        color: #7a7267;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: #111111;
        color: #E1D8D1;
    }

    /* Description text */
    .signal-description {
        font-size: 0.85rem;
        color: #9a9189;
        margin-top: 0.5rem;
        line-height: 1.5;
    }

    /* Empty state */
    .empty-state {
        text-align: center;
        padding: 4rem 2rem;
        color: #7a7267;
    }

    .empty-state h3 {
        color: #E1D8D1;
        font-weight: 400;
        margin-bottom: 0.5rem;
    }
    </style>
    """, unsafe_allow_html=True)


def init_session_state():
    """Initialize session state for filters."""
    if "mini_scout_filters" not in st.session_state:
        st.session_state.mini_scout_filters = {
            "search_query": "",
            "verticals": [],
            "sources": [],
            "signal_types": [],
            "min_confidence": 0.0,
            "date_range": "all",
        }
    if "mini_scout_results" not in st.session_state:
        st.session_state.mini_scout_results = []


def render_header():
    """Render the main header."""
    st.markdown('<h1 class="main-title">Signal Scout</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle">Discover and explore investment signals</p>', unsafe_allow_html=True)


def render_stats_bar(store: SignalStore):
    """Render summary statistics."""
    stats = run_async(store.get_fts_index_stats())
    all_signals = run_async(store.get_filtered_signals(limit=1000))

    vertical_counts = {}
    for sig in all_signals:
        v = sig.get("vertical", "unknown")
        vertical_counts[v] = vertical_counts.get(v, 0) + 1

    cols = st.columns(6)
    metrics = [
        ("Total", stats['total_signals']),
        ("Health", vertical_counts.get("health", 0)),
        ("Travel", vertical_counts.get("travel", 0)),
        ("SaaS", vertical_counts.get("saas", 0)),
        ("Consumer", vertical_counts.get("consumer", 0)),
        ("Unknown", vertical_counts.get("unknown", 0)),
    ]

    for col, (label, value) in zip(cols, metrics):
        with col:
            st.markdown(f"""
            <div class="stat-card">
                <div class="stat-value">{value}</div>
                <div class="stat-label">{label}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)


def render_search_bar() -> bool:
    """Render clean search input."""
    col1, col2 = st.columns([6, 1])

    with col1:
        query = st.text_input(
            "Search",
            value=st.session_state.mini_scout_filters["search_query"],
            placeholder="Search companies, keywords...",
            label_visibility="collapsed"
        )
        st.session_state.mini_scout_filters["search_query"] = query

    with col2:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    return search_clicked


def render_filter_sidebar(store: SignalStore):
    """Render filter sidebar."""
    st.sidebar.markdown("### Filters")

    # Presets
    st.sidebar.markdown('<p class="filter-header">SAVED PRESETS</p>', unsafe_allow_html=True)
    presets = run_async(store.list_filter_presets())
    preset_names = ["None"] + [p["name"] for p in presets]
    selected_preset = st.sidebar.selectbox("Preset", preset_names, label_visibility="collapsed")

    if selected_preset != "None":
        preset = run_async(store.load_filter_preset(selected_preset))
        if preset:
            st.session_state.mini_scout_filters.update(preset["filters"])
            st.rerun()

    st.sidebar.markdown("---")

    # Vertical filter
    st.sidebar.markdown('<p class="filter-header">VERTICAL</p>', unsafe_allow_html=True)
    verticals = st.sidebar.multiselect(
        "Vertical",
        options=["health", "travel", "saas", "consumer"],
        default=st.session_state.mini_scout_filters.get("verticals", []),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["verticals"] = verticals

    # Confidence filter
    st.sidebar.markdown('<p class="filter-header">MIN CONFIDENCE</p>', unsafe_allow_html=True)
    min_conf = st.sidebar.slider(
        "Confidence",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.mini_scout_filters.get("min_confidence", 0.0)),
        step=0.1,
        format="%.0f%%",
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["min_confidence"] = min_conf

    # Source filter
    st.sidebar.markdown('<p class="filter-header">SOURCE</p>', unsafe_allow_html=True)
    all_sources = ["producthunt", "g2crowd", "linkedin", "sec_edgar", "crunchbase"]
    sources = st.sidebar.multiselect(
        "Source",
        options=all_sources,
        default=st.session_state.mini_scout_filters.get("sources", []),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["sources"] = sources

    # Date range
    st.sidebar.markdown('<p class="filter-header">DATE RANGE</p>', unsafe_allow_html=True)
    date_options = {"all": "All time", "7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days"}
    current_date_range = st.session_state.mini_scout_filters.get("date_range", "all")
    date_range = st.sidebar.radio(
        "Date",
        options=list(date_options.keys()),
        format_func=lambda x: date_options[x],
        index=list(date_options.keys()).index(current_date_range) if current_date_range in date_options else 0,
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["date_range"] = date_range

    st.sidebar.markdown("---")

    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("Clear", use_container_width=True):
            st.session_state.mini_scout_filters = {
                "search_query": "",
                "verticals": [],
                "sources": [],
                "signal_types": [],
                "min_confidence": 0.0,
                "date_range": "all",
            }
            st.rerun()

    with col2:
        with st.popover("Save"):
            preset_name = st.text_input("Name", key="save_preset_name")
            if st.button("Save Preset") and preset_name:
                try:
                    run_async(store.save_filter_preset(preset_name, st.session_state.mini_scout_filters))
                    st.success("Saved!")
                except ValueError as e:
                    st.error(str(e))

    # Index status
    st.sidebar.markdown("---")
    stats = run_async(store.get_fts_index_stats())
    st.sidebar.caption(f"Index: {stats['indexed_signals']}/{stats['total_signals']}")
    if stats['unindexed'] > 0:
        if st.sidebar.button("Rebuild Index", key="rebuild"):
            with st.spinner("Rebuilding..."):
                run_async(store.rebuild_fts_index())
                st.rerun()


def execute_search(store: SignalStore) -> List[Dict[str, Any]]:
    """Execute search with current filters."""
    filters = st.session_state.mini_scout_filters

    start_date = None
    if filters["date_range"] != "all":
        days = int(filters["date_range"].replace("d", ""))
        start_date = datetime.now() - timedelta(days=days)

    if filters["search_query"]:
        results = run_async(store.search_signals_fts(filters["search_query"], limit=500))
        if filters["verticals"]:
            results = [r for r in results if r.get("vertical") in filters["verticals"]]
        if filters["min_confidence"] > 0:
            results = [r for r in results if (r.get("confidence") or 0) >= filters["min_confidence"]]
        if filters["sources"]:
            results = [r for r in results if r.get("source_api") in filters["sources"]]
        return results
    else:
        return run_async(store.get_filtered_signals(
            verticals=filters["verticals"] or None,
            sources=filters["sources"] or None,
            min_confidence=filters["min_confidence"] if filters["min_confidence"] > 0 else None,
            start_date=start_date,
            limit=500
        ))


def render_signal_card(signal: Dict[str, Any]):
    """Render a signal card."""
    company = signal.get('company_name', 'Unknown')
    source = signal.get('source_api', '')
    sig_type = signal.get('signal_type', '')
    created = str(signal.get('created_at', ''))[:10]
    conf = signal.get('confidence') or 0
    vert = signal.get('vertical') or 'unknown'
    signal_id = signal.get('signal_id', signal.get('id', ''))

    # Get description from raw_data if available
    description = ""
    raw = signal.get('raw_data')
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            description = data.get('description', data.get('tagline', ''))[:150]
            if len(description) == 150:
                description += "..."
        except:
            pass

    # Badge classes
    vert_class = f"badge-{vert}"
    if conf >= 0.8:
        conf_class = "badge-confidence-high"
    elif conf >= 0.5:
        conf_class = "badge-confidence-medium"
    else:
        conf_class = "badge-confidence-low"

    # Build description HTML separately
    desc_html = f'<div class="signal-description">{description}</div>' if description else ''

    card_html = f'<div class="signal-card"><div style="display:flex;justify-content:space-between;align-items:flex-start;"><div style="flex:1;"><div class="signal-company">{company}</div><div class="signal-meta">{source} · {sig_type} · {created}</div>{desc_html}</div><div class="signal-badges"><span class="badge {vert_class}">{vert}</span><span class="badge {conf_class}">{conf:.0%}</span></div></div></div>'

    st.markdown(card_html, unsafe_allow_html=True)

    # Drill-down button
    if st.button(f"View {company}", key=f"view_{signal_id}", help="See all signals"):
        st.session_state.selected_company = company
        st.rerun()


def render_results(results: List[Dict[str, Any]]):
    """Render search results."""
    if not results:
        st.markdown("""
        <div class="empty-state">
            <h3>No signals found</h3>
            <p>Try adjusting your search or filters</p>
        </div>
        """, unsafe_allow_html=True)
        return

    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown(f'<p class="result-count"><strong>{len(results)}</strong> signals</p>', unsafe_allow_html=True)
    with col2:
        csv_data = _convert_to_csv(results)
        st.download_button("Export", csv_data, "signals.csv", "text/csv", use_container_width=True)

    if len(results) >= 500:
        st.warning("Showing first 500 results")

    for signal in results:
        render_signal_card(signal)


def _convert_to_csv(results: List[Dict[str, Any]]) -> str:
    """Convert results to CSV."""
    import csv
    import io

    if not results:
        return ""

    output = io.StringIO()
    fieldnames = ['signal_id', 'company_name', 'vertical', 'source_api', 'signal_type', 'confidence', 'created_at']
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(results)
    return output.getvalue()


def render_company_detail(company_name: str, store: SignalStore):
    """Render company detail view."""
    if st.button("← Back"):
        st.session_state.selected_company = None
        st.rerun()

    st.markdown(f'<h1 class="main-title">{company_name}</h1>', unsafe_allow_html=True)

    signals = run_async(store.get_signals_for_company_by_name(company_name))

    if not signals:
        st.info("No signals found for this company.")
        return

    sources = len(set(s.get('source_api', '') for s in signals))
    st.markdown(f'<p class="subtitle">{len(signals)} signals from {sources} sources</p>', unsafe_allow_html=True)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    for signal in signals:
        created = str(signal.get("created_at", ""))[:10]
        sig_type = signal.get('signal_type', 'Unknown')
        source = signal.get('source_api', 'Unknown')
        conf = signal.get('confidence') or 0
        vert = signal.get('vertical') or 'unknown'

        vert_class = f"badge-{vert}"
        conf_class = "badge-confidence-high" if conf >= 0.8 else "badge-confidence-medium" if conf >= 0.5 else "badge-confidence-low"

        card_html = f'<div class="signal-card"><div style="display:flex;justify-content:space-between;"><div><div class="signal-company">{sig_type}</div><div class="signal-meta">{source} · {created}</div></div><div class="signal-badges"><span class="badge {vert_class}">{vert}</span><span class="badge {conf_class}">{conf:.0%}</span></div></div></div>'
        st.markdown(card_html, unsafe_allow_html=True)

        with st.expander("Details"):
            raw_data = signal.get("raw_data")
            if raw_data:
                try:
                    data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                    st.json(data)
                except (json.JSONDecodeError, TypeError):
                    st.text(str(raw_data))


def render_analytics(store: SignalStore):
    """Render analytics tab."""
    st.markdown('<h2 style="color: #E1D8D1; font-weight: 400;">Analytics</h2>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle">Signal distribution</p>', unsafe_allow_html=True)

    all_signals = run_async(store.get_filtered_signals(limit=1000))

    if not all_signals:
        st.markdown("""
        <div class="empty-state">
            <h3>No data yet</h3>
            <p>Run the pipeline to collect signals</p>
        </div>
        """, unsafe_allow_html=True)
        return

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**By Vertical**")
        vertical_counts = {}
        for sig in all_signals:
            v = sig.get("vertical", "unknown")
            vertical_counts[v] = vertical_counts.get(v, 0) + 1

        for vert, count in sorted(vertical_counts.items(), key=lambda x: -x[1]):
            pct = count / len(all_signals) * 100
            st.markdown(f'<div class="signal-card"><div style="display:flex;justify-content:space-between;"><span class="signal-company">{vert.title()}</span><span class="signal-meta">{count} ({pct:.0f}%)</span></div></div>', unsafe_allow_html=True)

    with col2:
        st.markdown("**By Source**")
        source_counts = {}
        for sig in all_signals:
            s = sig.get("source_api", "unknown")
            source_counts[s] = source_counts.get(s, 0) + 1

        for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
            pct = count / len(all_signals) * 100
            st.markdown(f'<div class="signal-card"><div style="display:flex;justify-content:space-between;"><span class="signal-company">{source}</span><span class="signal-meta">{count} ({pct:.0f}%)</span></div></div>', unsafe_allow_html=True)


def render_mini_scout_page(store: SignalStore):
    """Main entry point for Mini-Scout page."""
    inject_custom_css()
    init_session_state()

    if "selected_company" not in st.session_state:
        st.session_state.selected_company = None

    if st.session_state.selected_company:
        render_company_detail(st.session_state.selected_company, store)
        return

    render_header()
    render_filter_sidebar(store)

    tab1, tab2 = st.tabs(["SIGNALS", "ANALYTICS"])

    with tab1:
        render_stats_bar(store)
        search_clicked = render_search_bar()

        if search_clicked or st.session_state.mini_scout_results:
            if search_clicked:
                with st.spinner("Searching..."):
                    results = execute_search(store)
                    st.session_state.mini_scout_results = results

            render_results(st.session_state.mini_scout_results)

    with tab2:
        render_analytics(store)
