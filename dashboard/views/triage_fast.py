"""
Fast Pass Triage View — paginated triage table with quick actions.

Features:
- Sidebar filters (status, confidence, source, search, date range, sort)
- Source API multi-select with predefined collector options
- Sortable columns (confidence, detected_at, company_name)
- Row count display with "has more" indicator
- Cursor-based pagination with history stack for Prev
- @st.cache_data with TTL + cache buster for stale-after-action
- Quick-action approve/reject/defer with updated_at provenance
- Per-row selection → deep review
"""

from __future__ import annotations

import uuid
from datetime import date

import streamlit as st

from dashboard.api_client import APIClient, is_error, error_msg

# Predefined source API options matching collector inventory
SOURCE_API_OPTIONS = [
    "github",
    "sec_edgar",
    "news_api",
    "job_postings",
    "domain_whois",
    "hacker_news",
    "companies_house",
    "rss_feeds",
]


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

        source_selections = st.multiselect(
            "Source APIs",
            SOURCE_API_OPTIONS,
            default=[],
        )
        source_filter = ",".join(source_selections) if source_selections else ""

        search_text = st.text_input("Search Company", placeholder="Company name...")

        st.markdown("### Date Range")
        date_range = st.date_input(
            "Detected At",
            value=(),
            help="Filter by signal detection date range",
        )
        start_date = None
        end_date = None
        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start_date = date_range[0].isoformat()
            end_date = date_range[1].isoformat()

        st.markdown("### Sort")
        sort_by = st.selectbox(
            "Sort By",
            ["confidence", "detected_at", "company_name"],
        )
        sort_order_label = st.radio(
            "Order",
            ["Descending", "Ascending"],
            horizontal=True,
        )
        sort_order = "desc" if sort_order_label == "Descending" else "asc"

    # Check filter changes → reset cursor
    current_filters = {
        "status": status_filter,
        "min_confidence": min_confidence,
        "source_api": source_filter,
        "search": search_text,
        "start_date": start_date,
        "end_date": end_date,
        "sort_by": sort_by,
        "sort_order": sort_order,
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
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    if is_error(result):
        st.error(f"Could not load triage data: {error_msg(result)}")
        st.caption("Check that the API server is running, or adjust API_BASE_URL if needed.")
        return

    if not result or "data" not in result:
        st.warning("Unexpected API response. Try refreshing the page.")
        return

    items = result.get("data", [])
    meta = result.get("meta", {})
    has_more = meta.get("has_more", False)
    next_cursor = meta.get("next_cursor")

    if not items:
        st.info(
            "No items match your current filters. "
            "Try widening the date range, lowering the confidence threshold, "
            "or selecting additional source APIs in the sidebar."
        )
        return

    # -------------------------------------------------------------------------
    # TRIAGE TABLE
    # -------------------------------------------------------------------------
    if has_more:
        st.markdown(f"**Showing {len(items)} of more**")
    else:
        st.markdown(f"**Showing {len(items)} items**")

    # Initialize bulk selection state
    if "triage_selected_ids" not in st.session_state:
        st.session_state.triage_selected_ids = set()

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

        cols = st.columns([0.5, 3, 1, 1, 1, 1, 2])
        with cols[0]:
            checked = st.checkbox(
                "Select",
                key=f"bulk_{review_id}",
                label_visibility="collapsed",
            )
            if checked:
                st.session_state.triage_selected_ids.add(review_id)
            else:
                st.session_state.triage_selected_ids.discard(review_id)
        with cols[1]:
            if st.button(f"{company}", key=f"select_{review_id}"):
                st.session_state.triage_selected_id = review_id
                st.rerun()
        with cols[2]:
            st.caption(conf_pct)
        with cols[3]:
            st.caption(f"{signal_count} sig")
        with cols[4]:
            st.caption(sources[:20] if sources else "—")
        with cols[5]:
            st.caption(category or "—")
        with cols[6]:
            _render_quick_actions(client, review_id, status, updated_at)

    # -------------------------------------------------------------------------
    # BULK ACTION BAR
    # -------------------------------------------------------------------------
    selected_ids = [
        rid for rid in st.session_state.triage_selected_ids
        if any(item.get("review_id") == rid for item in items)
    ]
    if selected_ids:
        st.markdown("---")
        bcols = st.columns([2, 2, 1])
        with bcols[0]:
            bulk_action = st.selectbox(
                "Bulk Action",
                ["Approve", "Reject", "Defer"],
                key="bulk_action_select",
            )
        with bcols[1]:
            bulk_reason = st.text_input(
                "Reason",
                key="bulk_reason_input",
                placeholder="Reason for bulk action...",
            )
        with bcols[2]:
            st.markdown("")  # spacer for alignment
            if st.button(
                f"Apply to {len(selected_ids)} items",
                key="bulk_apply",
                type="primary",
            ):
                if bulk_reason:
                    bulk_items = [
                        {"review_id": rid, "reason": bulk_reason}
                        for rid in selected_ids
                    ]
                    idempotency_key = str(uuid.uuid4())
                    result = client.bulk_triage(
                        action=bulk_action.lower(),
                        items=bulk_items,
                        idempotency_key=idempotency_key,
                    )
                    if is_error(result):
                        status_code = result.get("status_code") if result else None
                        if status_code == 423:
                            st.info(
                                "Bulk actions are not yet enabled. "
                                "An admin can activate this with BULK_TRIAGE_ENABLED."
                            )
                        else:
                            st.error(f"Bulk action failed: {error_msg(result)}")
                    else:
                        st.success(f"{bulk_action}d {len(selected_ids)} items!")
                        st.session_state.triage_selected_ids = set()
                        st.session_state.triage_cache_buster += 1
                        st.rerun()
                else:
                    st.warning("Please enter a reason for the bulk action.")

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
    start_date=None,
    end_date=None,
    sort_by=None,
    sort_order=None,
):
    """Fetch triage list with caching. cache_buster invalidates after actions."""
    return _client.list_triage(
        status=status,
        min_confidence=min_confidence,
        source_api=source_api,
        search=search,
        cursor=cursor,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_order=sort_order,
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
