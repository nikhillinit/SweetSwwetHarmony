"""
Hunter Sandbox View — operator-facing dashboard for managing hunter runs.

Features:
- Run selection in sidebar
- Tabbed main content: Queries, Results, Budget
- Inline feedback buttons (relevant / not_relevant)
- Promote with confirmation dialog
- Cursor-based pagination for results
- Cache buster on feedback/promote actions
"""

from __future__ import annotations

import uuid

import streamlit as st

from dashboard.api_client import APIClient, is_error, error_msg


def render_hunter_page():
    """Render the Hunter Sandbox dashboard view."""
    st.title("Hunter Sandbox")

    client = APIClient()

    # -------------------------------------------------------------------------
    # SIDEBAR: Run Selection + Filters + Actions
    # -------------------------------------------------------------------------
    with st.sidebar:
        st.markdown("### Hunter Controls")

        # Fetch recent runs
        runs_result = _fetch_runs(client, cache_buster=st.session_state.get("hunter_cache_buster", 0))
        runs = runs_result.get("data", []) if not is_error(runs_result) else []

        if runs:
            run_labels = [
                f"{r.get('id', 'unknown')[:8]}… ({r.get('status', '?')})"
                for r in runs
            ]
            selected_idx = st.selectbox(
                "Select Run",
                range(len(run_labels)),
                format_func=lambda i: run_labels[i],
            )
            selected_run = runs[selected_idx] if runs else None
            st.session_state.hunter_selected_run_id = selected_run.get("id") if selected_run else None
        else:
            st.info("No hunter runs found.")
            st.session_state.hunter_selected_run_id = None
            selected_run = None

        st.markdown("---")

        # Filters
        st.markdown("### Filters")
        status_filter = st.selectbox(
            "Result Status",
            ["", "pending", "relevant", "not_relevant", "already_known", "promoted"],
            format_func=lambda x: x.replace("_", " ").title() if x else "All",
        )

        min_score = st.slider(
            "Min Thesis Score",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
        )
        min_score_val = min_score if min_score > 0 else None

    # Initialize state
    if "hunter_cache_buster" not in st.session_state:
        st.session_state.hunter_cache_buster = 0
    if "hunter_result_cursor" not in st.session_state:
        st.session_state.hunter_result_cursor = None
    if "hunter_cursor_stack" not in st.session_state:
        st.session_state.hunter_cursor_stack = []

    # -------------------------------------------------------------------------
    # MAIN CONTENT: Tabs
    # -------------------------------------------------------------------------
    run_id = st.session_state.get("hunter_selected_run_id")
    if not run_id:
        st.info("Select a hunter run from the sidebar to view details.")
        return

    tab_queries, tab_results, tab_budget = st.tabs(["Queries", "Results", "Budget"])

    # -------------------------------------------------------------------------
    # QUERIES TAB
    # -------------------------------------------------------------------------
    with tab_queries:
        _render_queries_tab(client, run_id)

    # -------------------------------------------------------------------------
    # RESULTS TAB
    # -------------------------------------------------------------------------
    with tab_results:
        _render_results_tab(client, run_id, status_filter, min_score_val)

    # -------------------------------------------------------------------------
    # BUDGET TAB
    # -------------------------------------------------------------------------
    with tab_budget:
        _render_budget_tab(client)


# =============================================================================
# TAB RENDERERS
# =============================================================================

def _render_queries_tab(client: APIClient, run_id: str):
    """Render queries table for the selected run."""
    queries_result = client.get(f"/hunter/runs/{run_id}/queries")

    if is_error(queries_result):
        st.warning("Could not load queries.")
        return

    queries = queries_result.get("data", [])
    if not queries:
        st.info("No queries for this run.")
        return

    st.markdown(f"**{len(queries)} queries**")

    hcols = st.columns([2, 1, 1, 1, 1])
    hcols[0].markdown("**Query**")
    hcols[1].markdown("**Collector**")
    hcols[2].markdown("**Status**")
    hcols[3].markdown("**Results**")
    hcols[4].markdown("**Cost**")

    for q in queries:
        cols = st.columns([2, 1, 1, 1, 1])
        with cols[0]:
            st.text(q.get("query_text", "")[:50])
        with cols[1]:
            st.caption(q.get("collector", ""))
        with cols[2]:
            status = q.get("status", "")
            st.caption(status)
        with cols[3]:
            st.caption(f"{q.get('results_count', 0)} results")
        with cols[4]:
            cost = q.get("cost_units_final") or q.get("cost_units_reserved", 0)
            st.caption(f"{cost:.1f} cost")


def _render_results_tab(
    client: APIClient,
    run_id: str,
    status_filter: str,
    min_score: float | None,
):
    """Render results table with inline feedback and promote actions."""
    params = {"run_id": run_id, "limit": 20}
    if status_filter:
        params["status"] = status_filter
    if min_score is not None:
        params["min_score"] = min_score

    cursor = st.session_state.get("hunter_result_cursor")
    if cursor:
        params["cursor"] = cursor

    results_data = client.get("/hunter/runs/{}/results".format(run_id), params=params)

    if is_error(results_data):
        st.warning("Could not load results.")
        return

    items = results_data.get("data", [])
    meta = results_data.get("meta", {})
    has_more = meta.get("has_more", False)
    next_cursor = meta.get("next_cursor")

    if not items:
        st.info("No results matching filters.")
        return

    st.markdown(f"**{len(items)} results** on this page")

    hcols = st.columns([3, 1, 1, 1, 2])
    hcols[0].markdown("**Company**")
    hcols[1].markdown("**Conf.**")
    hcols[2].markdown("**Thesis**")
    hcols[3].markdown("**Status**")
    hcols[4].markdown("**Actions**")

    for item in items:
        result_id = item.get("id")
        company = item.get("company_name", "Unknown")
        canonical = item.get("canonical_key", "—")
        confidence = item.get("confidence_score")
        thesis_score = item.get("thesis_fit_score")
        status = item.get("status", "")
        already_known = item.get("already_known", False)

        conf_str = f"{confidence:.0%}" if confidence is not None else "—"
        thesis_str = f"{thesis_score:.0%}" if thesis_score is not None else "—"

        cols = st.columns([3, 1, 1, 1, 2])
        with cols[0]:
            label = company
            if already_known:
                label += " (known)"
            st.text(label)
        with cols[1]:
            st.caption(f"Conf: {conf_str}")
        with cols[2]:
            st.caption(f"Thesis: {thesis_str}")
        with cols[3]:
            st.caption(status.replace("_", " ").title())
        with cols[4]:
            _render_result_actions(client, result_id, status, item.get("updated_at", ""))

    # Pagination
    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.session_state.hunter_cursor_stack:
            if st.button("Previous", key="hunter_prev"):
                st.session_state.hunter_result_cursor = st.session_state.hunter_cursor_stack.pop()
                st.rerun()
    with col_next:
        if has_more and next_cursor:
            if st.button("Next", key="hunter_next"):
                stack = st.session_state.hunter_cursor_stack
                stack.append(st.session_state.hunter_result_cursor)
                st.session_state.hunter_cursor_stack = stack
                st.session_state.hunter_result_cursor = next_cursor
                st.rerun()


def _render_result_actions(client: APIClient, result_id: int, status: str, updated_at: str):
    """Render inline feedback + promote buttons for a result row."""
    if status in ("promoted", "already_known", "not_relevant"):
        st.caption(status.replace("_", " ").title())
        return

    if status == "pending":
        col_rel, col_rej = st.columns(2)
        with col_rel:
            if st.button("Relevant", key=f"rel_{result_id}"):
                idempotency_key = str(uuid.uuid4())
                resp = client.post(
                    f"/hunter/results/{result_id}/feedback",
                    json={"status": "relevant", "idempotency_key": idempotency_key},
                )
                if is_error(resp):
                    st.error("Failed")
                else:
                    st.session_state.hunter_cache_buster += 1
                    st.rerun()
        with col_rej:
            if st.button("Not Rel", key=f"rej_{result_id}"):
                idempotency_key = str(uuid.uuid4())
                resp = client.post(
                    f"/hunter/results/{result_id}/feedback",
                    json={"status": "not_relevant", "idempotency_key": idempotency_key},
                )
                if is_error(resp):
                    st.error("Failed")
                else:
                    st.session_state.hunter_cache_buster += 1
                    st.rerun()

    elif status == "relevant":
        if st.button("Promote", key=f"promote_{result_id}"):
            st.session_state[f"confirm_promote_{result_id}"] = True

        if st.session_state.get(f"confirm_promote_{result_id}"):
            st.warning("Promote to signals pipeline?")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Yes", key=f"confirm_yes_{result_id}"):
                    idempotency_key = str(uuid.uuid4())
                    resp = client.post(
                        f"/hunter/results/{result_id}/promote",
                        json={"idempotency_key": idempotency_key},
                    )
                    if is_error(resp):
                        st.error("Promotion failed")
                    else:
                        st.success("Promoted!")
                        st.session_state.hunter_cache_buster += 1
                        st.session_state.pop(f"confirm_promote_{result_id}", None)
                        st.rerun()
            with col_no:
                if st.button("No", key=f"confirm_no_{result_id}"):
                    st.session_state.pop(f"confirm_promote_{result_id}", None)
                    st.rerun()


def _render_budget_tab(client: APIClient):
    """Render budget usage overview."""
    budget_result = client.get("/hunter/budget")

    if is_error(budget_result):
        st.warning("Could not load budget data.")
        return

    data = budget_result.get("data", budget_result)
    global_info = data.get("global", {})
    collectors = data.get("collectors", {})

    # Global summary
    cost_used = global_info.get("cost_units", 0)
    cost_cap = global_info.get("cost_cap", 100)
    if cost_cap and cost_cap > 0:
        st.progress(min(cost_used / cost_cap, 1.0))
        st.caption(f"Global cost: {cost_used:.1f} / {cost_cap:.1f}")
    else:
        st.caption("No global budget data")

    if global_info.get("circuit_breaker_tripped"):
        st.error("Circuit breaker TRIPPED — queries paused")

    # Per-collector breakdown
    if collectors:
        st.markdown("#### Per-Collector Usage")
        for coll_name, coll_data in collectors.items():
            queries_used = coll_data.get("queries_executed", 0)
            queries_cap = coll_data.get("queries_cap", 50)
            if queries_cap and queries_cap > 0:
                st.progress(min(queries_used / queries_cap, 1.0))
                st.caption(f"{coll_name}: {queries_used}/{queries_cap} queries")
            else:
                st.caption(f"{coll_name}: {queries_used} queries (no cap)")
    else:
        st.info("No collector budget data for today.")


# =============================================================================
# CACHED FETCHERS
# =============================================================================

@st.cache_data(ttl=30)
def _fetch_runs(_client, cache_buster=0):
    """Fetch recent hunter runs with caching."""
    return _client.get("/hunter/runs", params={"limit": 20})
