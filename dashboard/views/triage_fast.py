"""
Fast Pass Triage View — paginated triage table with quick actions.

Features:
- Sidebar filters (status, confidence, source, search)
- Cursor-based pagination with history stack for Prev
- @st.cache_data with TTL + cache buster for stale-after-action
- Quick-action approve/reject/defer with updated_at provenance
- Per-row selection → deep review
"""

from __future__ import annotations

import uuid

import streamlit as st

from dashboard.api_client import APIClient


def render_triage_fast_page():
    """Render the Fast Pass triage list view."""
    st.title("Triage — Fast Pass")

    client = APIClient()

    # -------------------------------------------------------------------------
    # SIDEBAR FILTERS
    # -------------------------------------------------------------------------
    with st.sidebar:
        st.markdown("### Triage Filters")

        status_filter = st.selectbox(
            "Status",
            ["pending", "approved", "deferred", "rejected", ""],
            index=0,
            format_func=lambda x: x.title() if x else "All",
        )

        confidence_range = st.slider(
            "Min Confidence",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
        )
        min_confidence = confidence_range if confidence_range > 0 else None

        source_filter = st.text_input("Source API", placeholder="e.g. github")

        search_text = st.text_input("Search Company", placeholder="Company name...")

    # Check filter changes → reset cursor
    current_filters = {
        "status": status_filter,
        "min_confidence": min_confidence,
        "source_api": source_filter,
        "search": search_text,
    }
    prev_filters = st.session_state.get("triage_prev_filters", {})
    if current_filters != prev_filters:
        st.session_state.triage_prev_filters = current_filters
        st.session_state.triage_cursor = None
        st.session_state.triage_cursor_stack = []

    # Initialize pagination state
    if "triage_cursor" not in st.session_state:
        st.session_state.triage_cursor = None
    if "triage_cursor_stack" not in st.session_state:
        st.session_state.triage_cursor_stack = []
    if "triage_cache_buster" not in st.session_state:
        st.session_state.triage_cache_buster = 0

    # -------------------------------------------------------------------------
    # FETCH DATA
    # -------------------------------------------------------------------------
    result = _fetch_triage_list(
        client,
        status=status_filter or None,
        min_confidence=min_confidence,
        source_api=source_filter or None,
        search=search_text or None,
        cursor=st.session_state.triage_cursor,
        cache_buster=st.session_state.triage_cache_buster,
    )

    if result and result.get("error"):
        st.error(f"API Error: {result.get('message', result.get('detail', 'Unknown'))}")
        return

    if not result or "data" not in result:
        st.info("No triage items found matching your filters.")
        return

    items = result.get("data", [])
    meta = result.get("meta", {})
    has_more = meta.get("has_more", False)
    next_cursor = meta.get("next_cursor")

    if not items:
        st.info("No triage items found matching your filters.")
        return

    # -------------------------------------------------------------------------
    # TRIAGE TABLE
    # -------------------------------------------------------------------------
    st.markdown(f"**{len(items)} items** on this page")

    for item in items:
        review_id = item.get("review_id")
        company = item.get("company_name", "Unknown")
        confidence = item.get("confidence")
        status = item.get("status", "")
        sources = item.get("sources", "")
        signal_count = item.get("signal_count", 0)
        category = item.get("thesis_category", "")
        updated_at = item.get("updated_at", "")

        conf_pct = f"{confidence:.0%}" if confidence is not None else "—"

        cols = st.columns([3, 1, 1, 1, 1, 2])
        with cols[0]:
            if st.button(f"{company}", key=f"select_{review_id}"):
                st.session_state.triage_selected_id = review_id
                st.rerun()
        with cols[1]:
            st.caption(conf_pct)
        with cols[2]:
            st.caption(f"{signal_count} sig")
        with cols[3]:
            st.caption(sources[:20] if sources else "—")
        with cols[4]:
            st.caption(category or "—")
        with cols[5]:
            _render_quick_actions(client, review_id, status, updated_at)

    # -------------------------------------------------------------------------
    # PAGINATION
    # -------------------------------------------------------------------------
    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.session_state.triage_cursor_stack:
            if st.button("← Previous"):
                st.session_state.triage_cursor = st.session_state.triage_cursor_stack.pop()
                st.rerun()
    with col_next:
        if has_more and next_cursor:
            if st.button("Next →"):
                stack = st.session_state.triage_cursor_stack
                stack.append(st.session_state.triage_cursor)
                st.session_state.triage_cursor_stack = stack
                st.session_state.triage_cursor = next_cursor
                st.rerun()


# =============================================================================
# HELPERS
# =============================================================================

@st.cache_data(ttl=30)
def _fetch_triage_list(
    _client,
    status=None,
    min_confidence=None,
    source_api=None,
    search=None,
    cursor=None,
    cache_buster=0,
):
    """Fetch triage list with caching. cache_buster invalidates after actions."""
    return _client.list_triage(
        status=status,
        min_confidence=min_confidence,
        source_api=source_api,
        search=search,
        cursor=cursor,
    )


def _render_quick_actions(client, review_id, status, updated_at):
    """Render compact quick-action buttons for a triage item."""
    if status != "pending":
        st.caption(status.title())
        return

    action = st.selectbox(
        "Action",
        ["—", "Approve", "Reject", "Defer"],
        key=f"action_{review_id}",
        label_visibility="collapsed",
    )
    if action != "—":
        reason = st.text_input("Reason", key=f"reason_{review_id}", label_visibility="collapsed", placeholder="Reason...")
        if reason and st.button("Go", key=f"go_{review_id}"):
            idempotency_key = str(uuid.uuid4())
            action_map = {
                "Approve": client.approve_triage,
                "Reject": client.reject_triage,
                "Defer": client.defer_triage,
            }
            fn = action_map.get(action)
            if fn:
                result = fn(review_id, reason, updated_at, idempotency_key)
                if result and not result.get("error"):
                    st.success(f"{action}d!")
                    st.session_state.triage_cache_buster += 1
                    st.rerun()
                elif result and result.get("status_code") == 409:
                    st.warning("This item was modified. Please refresh.")
                else:
                    st.error(f"Failed: {result.get('message', 'Unknown error')}")
