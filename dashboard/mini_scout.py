"""
Mini-Scout: Signal search and exploration interface.

Dark theme design inspired by Press On Ventures.
Designed for non-technical users with plain language and helpful guidance.
"""
import asyncio
import logging
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, Any, List
import json

from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# User-friendly names for technical data sources
SOURCE_FRIENDLY_NAMES = {
    "producthunt": "Product Hunt",
    "g2crowd": "G2 Crowd Reviews",
    "linkedin": "LinkedIn",
    "sec_edgar": "SEC Filings",
    "crunchbase": "Crunchbase",
    "github": "GitHub",
    "companies_house": "UK Company Registry",
    "hacker_news": "Hacker News",
    "arxiv": "Research Papers",
    "uspto": "Patent Filings",
    "domain_whois": "Domain Registrations",
}

# User-friendly vertical names
VERTICAL_FRIENDLY_NAMES = {
    "health": "Consumer Health & Wellness",
    "travel": "Travel & Hospitality",
    "saas": "Software & Apps",
    "consumer": "Consumer Products",
    "unknown": "Other / Uncategorized",
}


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

    /* Similar companies */
    .similar-card {
        border: 1px solid #2a2520;
        border-radius: 4px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        background: #0d0d0d;
    }

    .similar-card:hover {
        border-color: #E1D8D1;
    }

    .similar-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
    }

    .similar-name {
        font-size: 1rem;
        font-weight: 500;
        color: #E1D8D1;
    }

    .similar-score {
        font-size: 0.8rem;
        font-weight: 600;
        padding: 0.2rem 0.5rem;
        border-radius: 2px;
        background: rgba(16, 185, 129, 0.2);
        color: #34D399;
    }

    .similar-reasons {
        font-size: 0.8rem;
        color: #9CA3AF;
    }
    </style>
    """, unsafe_allow_html=True)


def find_similar_companies_for_signal(canonical_key: str, db_path: str = "signals.db"):
    """
    Find similar companies for the given canonical key.

    Args:
        canonical_key: The company's canonical key
        db_path: Path to the database

    Returns:
        List of SimilarCompany objects
    """
    try:
        from storage.embedding_store import EmbeddingStore
        from utils.embedding_generator import EmbeddingGenerator
        from utils.similarity_engine import SimilarityEngine

        async def run_search():
            async with EmbeddingStore(db_path=db_path) as store:
                generator = EmbeddingGenerator()
                engine = SimilarityEngine(
                    embedding_store=store,
                    embedding_generator=generator,
                )
                return await engine.find_similar(canonical_key, n=10)

        return run_async(run_search())

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Similar companies error: {e}")
        return []


def render_similar_companies_mini(similar_companies: list) -> None:
    """Render similar companies in a compact format."""
    if not similar_companies:
        st.info("No similar companies found. Try running the embedding batch job first.")
        return

    st.markdown(f"**Found {len(similar_companies)} similar companies:**")

    for company in similar_companies[:5]:  # Show top 5
        score_pct = int(company.similarity_score * 100)
        reasons = ", ".join(company.match_reasons[:2])

        st.markdown(f"""
        <div class="similar-card">
            <div class="similar-header">
                <span class="similar-name">{company.company_name or company.canonical_key}</span>
                <span class="similar-score">{score_pct}%</span>
            </div>
            <div class="similar-reasons">{reasons}</div>
        </div>
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
    """Render the main header with user-friendly text."""
    st.markdown('<h1 class="main-title">Company Search</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle">Search and explore all discovered companies</p>', unsafe_allow_html=True)


def render_stats_bar(store: SignalStore):
    """Render summary statistics with user-friendly labels."""
    stats = run_async(store.get_fts_index_stats())
    all_signals = run_async(store.get_filtered_signals(limit=1000))

    vertical_counts = {}
    for sig in all_signals:
        v = sig.get("vertical", "unknown")
        vertical_counts[v] = vertical_counts.get(v, 0) + 1

    cols = st.columns(6)
    # User-friendly metric labels
    metrics = [
        ("All Companies", stats['total_signals']),
        ("Health & Wellness", vertical_counts.get("health", 0)),
        ("Travel", vertical_counts.get("travel", 0)),
        ("Software", vertical_counts.get("saas", 0)),
        ("Consumer", vertical_counts.get("consumer", 0)),
        ("Other", vertical_counts.get("unknown", 0)),
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
    """Render clean search input with helpful placeholder."""
    with st.form(key="search_form", clear_on_submit=False):
        col1, col2 = st.columns([6, 1])

        with col1:
            query = st.text_input(
                "Search",
                value=st.session_state.mini_scout_filters.get("search_query", ""),
                placeholder="Search by company name, keyword, or industry (e.g., 'wellness', 'travel tech')",
                label_visibility="collapsed",
                help="Enter any text to search across all company names and descriptions"
            )

        with col2:
            search_clicked = st.form_submit_button("Search", type="primary", use_container_width=True)

        if search_clicked:
            st.session_state.mini_scout_filters["search_query"] = query

    return search_clicked


def render_filter_sidebar(store: SignalStore):
    """Render filter sidebar with user-friendly labels."""
    st.sidebar.markdown("### Refine Your Search")

    # Presets section with better explanation
    st.sidebar.markdown('<p class="filter-header">QUICK PRESETS</p>', unsafe_allow_html=True)
    st.sidebar.caption("Load a saved filter combination")
    presets = run_async(store.list_filter_presets())
    preset_names = ["Choose a preset..."] + [p["name"] for p in presets]
    selected_preset = st.sidebar.selectbox("Preset", preset_names, label_visibility="collapsed")

    if selected_preset != "Choose a preset...":
        preset = run_async(store.load_filter_preset(selected_preset))
        if preset:
            st.session_state.mini_scout_filters.update(preset["filters"])
            st.rerun()

    st.sidebar.markdown("---")

    # Industry/Vertical filter with friendly names
    st.sidebar.markdown('<p class="filter-header">INDUSTRY</p>', unsafe_allow_html=True)
    st.sidebar.caption("Filter by company focus area")
    vertical_options = ["health", "travel", "saas", "consumer"]
    vertical_labels = {v: VERTICAL_FRIENDLY_NAMES.get(v, v.title()) for v in vertical_options}
    verticals = st.sidebar.multiselect(
        "Industry",
        options=vertical_options,
        default=st.session_state.mini_scout_filters.get("verticals", []),
        format_func=lambda x: vertical_labels.get(x, x.title()),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["verticals"] = verticals

    # Match score filter with user-friendly label
    st.sidebar.markdown('<p class="filter-header">MINIMUM MATCH SCORE</p>', unsafe_allow_html=True)
    st.sidebar.caption("How well companies fit our criteria")
    min_conf = st.sidebar.slider(
        "Match Score",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.mini_scout_filters.get("min_confidence", 0.0)),
        step=0.1,
        format="%.0f%%",
        label_visibility="collapsed",
        help="Higher = better fit for our investment thesis"
    )
    st.session_state.mini_scout_filters["min_confidence"] = min_conf

    # Visual guide for match scores
    st.sidebar.markdown("""
    <div style="font-size: 0.65rem; color: #7a7267; margin-top: -8px; margin-bottom: 16px;">
        <span style="color: #10B981;">●</span> 70%+ Strong &nbsp;
        <span style="color: #F59E0B;">●</span> 40%+ Worth reviewing
    </div>
    """, unsafe_allow_html=True)

    # Source filter with friendly names
    st.sidebar.markdown('<p class="filter-header">DISCOVERED VIA</p>', unsafe_allow_html=True)
    st.sidebar.caption("Where we found these companies")
    all_sources = ["producthunt", "g2crowd", "linkedin", "sec_edgar", "crunchbase"]
    sources = st.sidebar.multiselect(
        "Source",
        options=all_sources,
        default=st.session_state.mini_scout_filters.get("sources", []),
        format_func=lambda x: SOURCE_FRIENDLY_NAMES.get(x, x.title()),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["sources"] = sources

    # Date range with clearer options
    st.sidebar.markdown('<p class="filter-header">TIME PERIOD</p>', unsafe_allow_html=True)
    st.sidebar.caption("When these companies were discovered")
    date_options = {"all": "All time", "7d": "Past week", "30d": "Past month", "90d": "Past 3 months"}
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
        if st.button("Clear All", use_container_width=True, help="Reset all filters"):
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
        with st.popover("💾 Save"):
            st.markdown("**Save Current Filters**")
            st.caption("Give this filter combination a name to quickly use it again later.")
            preset_name = st.text_input("Preset name", key="save_preset_name", placeholder="e.g., High-value health")
            if st.button("Save Preset") and preset_name:
                try:
                    run_async(store.save_filter_preset(preset_name, st.session_state.mini_scout_filters))
                    st.success("Saved! You can now load this preset from the dropdown above.")
                except ValueError as e:
                    st.error(str(e))

    # Index status (simplified for non-technical users)
    st.sidebar.markdown("---")
    stats = run_async(store.get_fts_index_stats())
    if stats['unindexed'] > 0:
        st.sidebar.warning(f"⚠️ {stats['unindexed']} companies need indexing")
        if st.sidebar.button("Update Search Index", key="rebuild", help="Make all companies searchable"):
            with st.spinner("Updating search index..."):
                run_async(store.rebuild_fts_index())
                st.rerun()
    else:
        st.sidebar.caption(f"✓ {stats['total_signals']} companies searchable")


def execute_search(store: SignalStore) -> List[Dict[str, Any]]:
    """Execute search with current filters."""
    filters = st.session_state.mini_scout_filters
    logger.info(f"execute_search called with query: '{filters.get('search_query')}'")

    start_date = None
    if filters["date_range"] != "all":
        days = int(filters["date_range"].replace("d", ""))
        start_date = datetime.now() - timedelta(days=days)

    if filters["search_query"]:
        results = run_async(store.search_signals_fts(filters["search_query"], limit=500))
        logger.info(f"FTS search returned {len(results)} results")
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
    """Render a signal card with user-friendly labels."""
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

    # User-friendly source and vertical names
    friendly_source = SOURCE_FRIENDLY_NAMES.get(source, source.title() if source else "Unknown")
    friendly_vert = VERTICAL_FRIENDLY_NAMES.get(vert, vert.title()).split(" ")[0]  # Just first word for badge

    # Badge classes with user-friendly labels
    vert_class = f"badge-{vert}"
    if conf >= 0.7:
        conf_class = "badge-confidence-high"
        conf_label = "Strong fit"
    elif conf >= 0.4:
        conf_class = "badge-confidence-medium"
        conf_label = "Worth reviewing"
    else:
        conf_class = "badge-confidence-low"
        conf_label = "Early signal"

    # Build description HTML separately
    desc_html = f'<div class="signal-description">{description}</div>' if description else ''

    # More readable card layout
    card_html = f'''
    <div class="signal-card">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div style="flex:1;">
                <div class="signal-company">{company}</div>
                <div class="signal-meta">Found via {friendly_source} · {created}</div>
                {desc_html}
            </div>
            <div class="signal-badges" style="flex-direction: column; align-items: flex-end; gap: 4px;">
                <span class="badge {vert_class}">{friendly_vert}</span>
                <span class="badge {conf_class}" title="{conf_label}">{conf:.0%} match</span>
            </div>
        </div>
    </div>
    '''

    st.markdown(card_html, unsafe_allow_html=True)

    # Drill-down button with better label
    if st.button(f"See all details for {company}", key=f"view_{signal_id}", help="View complete company profile"):
        st.session_state.selected_company = company
        st.rerun()


def render_results(results: List[Dict[str, Any]]):
    """Render search results with user-friendly messaging."""
    if not results:
        st.markdown("""
        <div class="empty-state">
            <h3>No companies found</h3>
            <p>Try a different search term or adjust your filters to broaden your search.</p>
            <p style="font-size: 0.8rem; color: #7a7267; margin-top: 1rem;">
                💡 <strong>Tip:</strong> Try searching for industry terms like "wellness", "travel", or "food"
                instead of specific company names.
            </p>
        </div>
        """, unsafe_allow_html=True)
        return

    col1, col2 = st.columns([4, 1])
    with col1:
        company_word = "company" if len(results) == 1 else "companies"
        st.markdown(f'<p class="result-count">Found <strong>{len(results)}</strong> {company_word}</p>', unsafe_allow_html=True)
    with col2:
        csv_data = _convert_to_csv(results)
        st.download_button(
            "📥 Export",
            csv_data,
            "discovered_companies.csv",
            "text/csv",
            use_container_width=True,
            help="Download these results as a spreadsheet"
        )

    if len(results) >= 500:
        st.info("💡 Showing first 500 results. Use filters to narrow your search for more specific results.")

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
    """Render company detail view with user-friendly presentation."""
    if st.button("← Back to search results"):
        st.session_state.selected_company = None
        st.rerun()

    st.markdown(f'<h1 class="main-title">{company_name}</h1>', unsafe_allow_html=True)

    signals = run_async(store.get_signals_for_company_by_name(company_name))

    if not signals:
        st.markdown("""
        <div class="empty-state">
            <h3>No information found</h3>
            <p>We don't have any data for this company yet.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    sources = len(set(s.get('source_api', '') for s in signals))
    source_word = "source" if sources == 1 else "sources"
    signal_word = "time" if len(signals) == 1 else "times"
    st.markdown(f'<p class="subtitle">Discovered {len(signals)} {signal_word} from {sources} {source_word}</p>', unsafe_allow_html=True)

    # Summary card
    avg_conf = sum(s.get('confidence', 0) or 0 for s in signals) / len(signals) if signals else 0
    if avg_conf >= 0.7:
        fit_label = "Strong fit for our thesis"
        fit_color = "#10B981"
    elif avg_conf >= 0.4:
        fit_label = "Worth further investigation"
        fit_color = "#F59E0B"
    else:
        fit_label = "Early-stage signal"
        fit_color = "#6B7280"

    st.markdown(f"""
    <div style="background: #111; border: 1px solid #2a2520; border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;">
        <div style="font-size: 0.7rem; color: #7a7267; text-transform: uppercase; letter-spacing: 0.1em;">OVERALL ASSESSMENT</div>
        <div style="color: {fit_color}; font-size: 1.1rem; margin-top: 0.5rem;">{fit_label}</div>
        <div style="font-size: 0.8rem; color: #7a7267; margin-top: 0.25rem;">Average match score: {avg_conf:.0%}</div>
    </div>
    """, unsafe_allow_html=True)

    # Find Similar Companies button
    canonical_key = signals[0].get("canonical_key") if signals else None
    if canonical_key:
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("Find Similar", key=f"similar_{company_name}", help="Find companies similar to this one"):
                with st.spinner("Finding similar companies..."):
                    similar = find_similar_companies_for_signal(canonical_key)
                    st.session_state[f"similar_{company_name}"] = similar

        # Display similar companies if available
        if f"similar_{company_name}" in st.session_state:
            render_similar_companies_mini(st.session_state[f"similar_{company_name}"])

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    st.markdown("### Discovery Timeline")
    st.caption("Each time we found this company through our data sources")

    for signal in signals:
        created = str(signal.get("created_at", ""))[:10]
        sig_type = signal.get('signal_type', 'Unknown')
        source = signal.get('source_api', 'Unknown')
        conf = signal.get('confidence') or 0
        vert = signal.get('vertical') or 'unknown'

        # User-friendly labels
        friendly_source = SOURCE_FRIENDLY_NAMES.get(source, source.title() if source else "Unknown")
        friendly_vert = VERTICAL_FRIENDLY_NAMES.get(vert, vert.title()).split(" ")[0]

        vert_class = f"badge-{vert}"
        conf_class = "badge-confidence-high" if conf >= 0.7 else "badge-confidence-medium" if conf >= 0.4 else "badge-confidence-low"

        card_html = f'''
        <div class="signal-card">
            <div style="display:flex;justify-content:space-between;">
                <div>
                    <div class="signal-company">Found via {friendly_source}</div>
                    <div class="signal-meta">{created}</div>
                </div>
                <div class="signal-badges">
                    <span class="badge {vert_class}">{friendly_vert}</span>
                    <span class="badge {conf_class}">{conf:.0%} match</span>
                </div>
            </div>
        </div>
        '''
        st.markdown(card_html, unsafe_allow_html=True)

        with st.expander("View raw data (technical)"):
            raw_data = signal.get("raw_data")
            if raw_data:
                try:
                    data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                    st.json(data)
                except (json.JSONDecodeError, TypeError):
                    st.text(str(raw_data))
            else:
                st.caption("No additional data available")


def render_analytics(store: SignalStore):
    """Render analytics tab with user-friendly presentation."""
    st.markdown('<h2 style="color: #E1D8D1; font-weight: 400;">Overview</h2>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle">See where your discovered companies come from and what industries they\'re in</p>', unsafe_allow_html=True)

    all_signals = run_async(store.get_filtered_signals(limit=1000))

    if not all_signals:
        st.markdown("""
        <div class="empty-state">
            <h3>No companies discovered yet</h3>
            <p>Once the discovery pipeline runs, you'll see a breakdown of companies by industry and source here.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    # Summary stats at top
    st.markdown(f"""
    <div style="background: #111; border: 1px solid #2a2520; border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; text-align: center;">
        <div style="font-size: 2rem; color: #E1D8D1; font-weight: 600;">{len(all_signals)}</div>
        <div style="font-size: 0.8rem; color: #7a7267;">Total companies discovered</div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### By Industry")
        st.caption("What sectors are these companies in?")
        vertical_counts = {}
        for sig in all_signals:
            v = sig.get("vertical", "unknown")
            vertical_counts[v] = vertical_counts.get(v, 0) + 1

        for vert, count in sorted(vertical_counts.items(), key=lambda x: -x[1]):
            pct = count / len(all_signals) * 100
            friendly_vert = VERTICAL_FRIENDLY_NAMES.get(vert, vert.title())
            st.markdown(f'''
            <div class="signal-card">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span class="signal-company">{friendly_vert}</span>
                    <div style="text-align: right;">
                        <span style="color: #E1D8D1; font-weight: 600;">{count}</span>
                        <span class="signal-meta" style="margin-left: 8px;">({pct:.0f}%)</span>
                    </div>
                </div>
            </div>
            ''', unsafe_allow_html=True)

    with col2:
        st.markdown("### By Discovery Source")
        st.caption("Where did we find these companies?")
        source_counts = {}
        for sig in all_signals:
            s = sig.get("source_api", "unknown")
            source_counts[s] = source_counts.get(s, 0) + 1

        for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
            pct = count / len(all_signals) * 100
            friendly_source = SOURCE_FRIENDLY_NAMES.get(source, source.title() if source else "Unknown")
            st.markdown(f'''
            <div class="signal-card">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span class="signal-company">{friendly_source}</span>
                    <div style="text-align: right;">
                        <span style="color: #E1D8D1; font-weight: 600;">{count}</span>
                        <span class="signal-meta" style="margin-left: 8px;">({pct:.0f}%)</span>
                    </div>
                </div>
            </div>
            ''', unsafe_allow_html=True)


def render_mini_scout_page(store: SignalStore, inject_css: bool = True):
    """Main entry point for Mini-Scout page.

    Args:
        store: SignalStore instance for data access
        inject_css: Whether to inject custom CSS. Set False when rendered inside app.py.
    """
    if inject_css:
        inject_custom_css()
    init_session_state()

    if "selected_company" not in st.session_state:
        st.session_state.selected_company = None

    if st.session_state.selected_company:
        render_company_detail(st.session_state.selected_company, store)
        return

    render_header()
    render_filter_sidebar(store)

    # User-friendly tab labels
    tab1, tab2 = st.tabs(["🔍 SEARCH COMPANIES", "📊 OVERVIEW"])

    with tab1:
        render_stats_bar(store)
        search_clicked = render_search_bar()

        # Get current search query from session state
        query = st.session_state.mini_scout_filters.get("search_query", "")

        # Execute search if we have a query (either from button click or persisted)
        if search_clicked or query:
            with st.spinner("Searching for companies..."):
                results = execute_search(store)
                st.session_state.mini_scout_results = results
            render_results(st.session_state.mini_scout_results)
        elif not st.session_state.mini_scout_results:
            # Show helpful hint when no search has been done
            st.markdown("""
            <div style="text-align: center; padding: 3rem 2rem; color: #7a7267;">
                <div style="font-size: 2.5rem; margin-bottom: 1rem;">🔍</div>
                <h3 style="color: #E1D8D1; font-weight: 400; margin-bottom: 0.5rem;">Search for companies</h3>
                <p style="max-width: 400px; margin: 0 auto;">
                    Enter a company name, keyword, or industry term above to find matching companies.
                    <br><br>
                    Or use the filters on the left to browse by industry, match score, or discovery source.
                </p>
            </div>
            """, unsafe_allow_html=True)

    with tab2:
        render_analytics(store)
