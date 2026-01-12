"""
Mini-Scout: Signal search and exploration interface.

Provides fuzzy search, thesis filtering, and saved presets for
non-technical team members to explore signals.
"""
import asyncio
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
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


def render_search_bar() -> bool:
    """Render the search input and button. Returns True if search clicked."""
    col1, col2 = st.columns([5, 1])

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

    st.caption('Tip: Type company names or keywords to search')

    return search_clicked


def render_filter_sidebar(store: SignalStore):
    """Render the filter controls in sidebar."""
    st.sidebar.markdown("### Filters")

    # Preset controls
    st.sidebar.markdown("**Saved Presets**")
    presets = run_async(store.list_filter_presets())
    preset_names = ["(Select preset)"] + [p["name"] for p in presets]

    selected_preset = st.sidebar.selectbox("Load preset", preset_names, label_visibility="collapsed")

    if selected_preset != "(Select preset)":
        preset = run_async(store.load_filter_preset(selected_preset))
        if preset:
            st.session_state.mini_scout_filters.update(preset["filters"])
            st.rerun()

    # Save preset button
    with st.sidebar.expander("Save current as preset"):
        preset_name = st.text_input("Preset name", key="new_preset_name")
        if st.button("Save Preset") and preset_name:
            try:
                run_async(store.save_filter_preset(
                    preset_name,
                    st.session_state.mini_scout_filters
                ))
                st.success(f"Saved '{preset_name}'")
            except ValueError as e:
                st.error(str(e))

    st.sidebar.markdown("---")

    # Vertical filter
    st.sidebar.markdown("**Vertical**")
    verticals = st.sidebar.multiselect(
        "Vertical",
        options=["health", "travel", "saas", "consumer", "unknown"],
        default=st.session_state.mini_scout_filters.get("verticals", []),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["verticals"] = verticals

    # Confidence filter
    st.sidebar.markdown("**Minimum Confidence**")
    min_conf = st.sidebar.slider(
        "Confidence",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.mini_scout_filters.get("min_confidence", 0.0)),
        step=0.1,
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["min_confidence"] = min_conf

    # Source filter
    st.sidebar.markdown("**Source**")
    all_sources = ["producthunt", "g2crowd", "capterra", "sec_edgar", "companies_house", "github"]
    sources = st.sidebar.multiselect(
        "Source",
        options=all_sources,
        default=st.session_state.mini_scout_filters.get("sources", []),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["sources"] = sources

    # Date range filter
    st.sidebar.markdown("**Date Range**")
    date_options = {
        "all": "All time",
        "7d": "Last 7 days",
        "30d": "Last 30 days",
        "90d": "Last 90 days",
    }
    current_date_range = st.session_state.mini_scout_filters.get("date_range", "all")
    date_range = st.sidebar.radio(
        "Date",
        options=list(date_options.keys()),
        format_func=lambda x: date_options[x],
        index=list(date_options.keys()).index(current_date_range) if current_date_range in date_options else 0,
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["date_range"] = date_range

    # Clear filters button
    st.sidebar.markdown("---")
    if st.sidebar.button("Clear all filters"):
        st.session_state.mini_scout_filters = {
            "search_query": "",
            "verticals": [],
            "sources": [],
            "signal_types": [],
            "min_confidence": 0.0,
            "date_range": "all",
        }
        st.rerun()


def execute_search(store: SignalStore) -> List[Dict[str, Any]]:
    """Execute search with current filters."""
    filters = st.session_state.mini_scout_filters

    # Calculate date range
    start_date = None
    if filters["date_range"] != "all":
        days = int(filters["date_range"].replace("d", ""))
        start_date = datetime.now() - timedelta(days=days)

    # If search query, use FTS
    if filters["search_query"]:
        results = run_async(store.search_signals_fts(
            filters["search_query"],
            limit=500
        ))
        # Apply additional filters to FTS results
        if filters["verticals"]:
            results = [r for r in results if r.get("vertical") in filters["verticals"]]
        if filters["min_confidence"] > 0:
            results = [r for r in results if (r.get("confidence") or 0) >= filters["min_confidence"]]
        if filters["sources"]:
            results = [r for r in results if r.get("source_api") in filters["sources"]]
        return results
    else:
        # No search query, use filtered query
        results = run_async(store.get_filtered_signals(
            verticals=filters["verticals"] or None,
            sources=filters["sources"] or None,
            min_confidence=filters["min_confidence"] if filters["min_confidence"] > 0 else None,
            start_date=start_date,
            limit=500
        ))
        return results


def render_signal_card(signal: Dict[str, Any]):
    """Render a single signal card with clickable company name."""
    conf = signal.get("confidence") or 0
    if conf >= 0.8:
        conf_color = "#10B981"
    elif conf >= 0.5:
        conf_color = "#F59E0B"
    else:
        conf_color = "#EF4444"

    vertical_colors = {
        "health": "#3B82F6",
        "travel": "#8B5CF6",
        "saas": "#EC4899",
        "consumer": "#F97316",
        "unknown": "#6B7280",
    }
    vert = signal.get("vertical") or "unknown"
    vert_color = vertical_colors.get(vert, "#6B7280")

    company_name = signal.get('company_name', 'Unknown')
    source = signal.get('source_api', '')
    sig_type = signal.get('signal_type', '')
    created = str(signal.get('created_at', ''))[:10]
    signal_id = signal.get('signal_id', '')

    with st.container():
        col1, col2, col3 = st.columns([4, 1, 1])

        with col1:
            # Clickable company name
            if st.button(company_name, key=f"company_{signal_id}", help="Click to see all signals"):
                st.session_state.selected_company = company_name
                st.rerun()
            st.caption(f"{source} · {sig_type} · {created}")

        with col2:
            st.markdown(f'<span style="background: {vert_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em;">{vert.upper()}</span>', unsafe_allow_html=True)

        with col3:
            st.markdown(f'<span style="background: {conf_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em;">{conf:.0%}</span>', unsafe_allow_html=True)


def render_results(results: List[Dict[str, Any]]):
    """Render search results."""
    if not results:
        st.info("No signals match your search. Try broader terms or adjust filters.")
        return

    # Result count and export
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**{len(results)}** signals found")
    with col2:
        # Export CSV
        if results:
            csv_data = _convert_to_csv(results)
            st.download_button(
                "Export CSV",
                csv_data,
                "signals.csv",
                "text/csv",
                key="download_csv"
            )

    # Warning if truncated
    if len(results) >= 500:
        st.warning("Showing first 500 results. Narrow your search for more specific results.")

    st.markdown("---")

    # Render cards
    for signal in results:
        render_signal_card(signal)


def _convert_to_csv(results: List[Dict[str, Any]]) -> str:
    """Convert results to CSV string."""
    import csv
    import io

    if not results:
        return ""

    output = io.StringIO()
    # Use consistent column order
    fieldnames = ['signal_id', 'company_name', 'vertical', 'source_api', 'signal_type', 'confidence', 'created_at']
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(results)
    return output.getvalue()


def render_company_detail(company_name: str, store: SignalStore):
    """Render detailed view of all signals for a company."""
    st.subheader(f"All signals for: {company_name}")

    signals = run_async(store.get_signals_for_company_by_name(company_name))

    if not signals:
        st.info("No signals found for this company.")
        return

    st.markdown(f"**{len(signals)} signals** from {len(set(s.get('source_api', '') for s in signals))} sources")
    st.markdown("---")

    # Timeline view
    for signal in signals:
        with st.container():
            col1, col2 = st.columns([1, 4])
            with col1:
                created = str(signal.get("created_at", ""))[:10]
                st.caption(created)
            with col2:
                sig_type = signal.get('signal_type', 'Unknown')
                source = signal.get('source_api', 'Unknown')
                conf = signal.get('confidence') or 0
                vert = signal.get('vertical') or 'unknown'

                st.markdown(f"**{sig_type}** from {source}")
                st.caption(f"Confidence: {conf:.0%} | Vertical: {vert}")

                with st.expander("View raw data"):
                    raw_data = signal.get("raw_data")
                    if raw_data:
                        try:
                            if isinstance(raw_data, str):
                                data = json.loads(raw_data)
                            else:
                                data = raw_data
                            st.json(data)
                        except:
                            st.text(str(raw_data))
                    else:
                        st.caption("No raw data available")

            st.markdown("---")


def render_mini_scout_page(store: SignalStore):
    """Main entry point for Mini-Scout page."""
    init_session_state()

    # Initialize selected_company if not exists
    if "selected_company" not in st.session_state:
        st.session_state.selected_company = None

    # Check if viewing company detail
    if st.session_state.selected_company:
        if st.button("← Back to search"):
            st.session_state.selected_company = None
            st.rerun()
        render_company_detail(st.session_state.selected_company, store)
        return

    # Normal search view
    st.title("Mini-Scout")
    st.caption("Search and explore signals")

    render_filter_sidebar(store)

    search_clicked = render_search_bar()

    if search_clicked or st.session_state.mini_scout_results:
        if search_clicked:
            with st.spinner("Searching..."):
                results = execute_search(store)
                st.session_state.mini_scout_results = results

        render_results(st.session_state.mini_scout_results)
