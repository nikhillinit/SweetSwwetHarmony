"""
Deep Review Detail View — full intelligence for a single triage item.

Features:
- Tabbed display: Signals, Thesis, ACH, Audit History
- Action bar with two-step confirmation and idempotency
- ACH rebuild button
- Back navigation to Fast Pass
"""

from __future__ import annotations

import uuid

import streamlit as st

from dashboard.api_client import APIClient


def render_triage_detail_page():
    """Render the Deep Review detail view for a selected triage item."""
    review_id = st.session_state.get("triage_selected_id")
    if not review_id:
        st.warning("No item selected. Return to Fast Pass to select one.")
        return

    client = APIClient()

    # Back button
    if st.button("← Back to Fast Pass"):
        st.session_state.triage_selected_id = None
        st.rerun()
        return

    # Fetch detail
    result = client.get_triage_detail(review_id)
    if not result or result.get("error"):
        st.error(f"Failed to load detail: {result.get('message', 'Unknown') if result else 'No response'}")
        return

    detail = result.get("data", result)

    # -------------------------------------------------------------------------
    # HEADER
    # -------------------------------------------------------------------------
    company_name = detail.get("company_name", "Unknown")
    canonical_key = detail.get("canonical_key", "")
    confidence = detail.get("confidence")
    status = detail.get("status", "")
    updated_at = detail.get("updated_at", "")

    st.title(company_name)
    cols = st.columns(4)
    with cols[0]:
        st.metric("Confidence", f"{confidence:.0%}" if confidence is not None else "—")
    with cols[1]:
        st.metric("Status", status.title())
    with cols[2]:
        st.metric("Signals", detail.get("total_signal_count", 0))
    with cols[3]:
        st.caption(f"Key: {canonical_key}")

    # -------------------------------------------------------------------------
    # TABS
    # -------------------------------------------------------------------------
    tab_signals, tab_thesis, tab_ach, tab_audit = st.tabs(
        ["Signals", "Thesis", "ACH", "Audit History"]
    )

    with tab_signals:
        _render_signals_tab(detail)

    with tab_thesis:
        _render_thesis_tab(detail)

    with tab_ach:
        _render_ach_tab_v2(client, review_id, detail)

    with tab_audit:
        _render_audit_tab(detail)

    # -------------------------------------------------------------------------
    # ACTION BAR
    # -------------------------------------------------------------------------
    st.divider()
    _render_action_bar(client, review_id, status, updated_at)


# =============================================================================
# TAB RENDERERS
# =============================================================================

def _render_signals_tab(detail):
    """Render signal cards."""
    signals = detail.get("signals", [])
    if not signals:
        st.info("No signals available.")
        return

    total = detail.get("total_signal_count", len(signals))
    if total > len(signals):
        st.caption(f"Showing {len(signals)} of {total} signals")

    for sig in signals:
        with st.container():
            cols = st.columns([1, 1, 1, 3])
            with cols[0]:
                st.caption(sig.get("source_api", ""))
            with cols[1]:
                conf = sig.get("confidence", 0)
                st.caption(f"{conf:.0%}")
            with cols[2]:
                st.caption(sig.get("detected_at", "")[:10] if sig.get("detected_at") else "")
            with cols[3]:
                excerpt = sig.get("excerpt", "")
                if excerpt:
                    st.text(excerpt[:200])


def _render_thesis_tab(detail):
    """Render thesis classification details."""
    category = detail.get("thesis_category")
    rationale = detail.get("thesis_rationale")

    if not category and not rationale:
        st.info("No thesis classification available.")
        return

    if category:
        st.markdown(f"**Category:** {category}")
    if rationale:
        st.markdown(f"**Rationale:** {rationale}")

    # Case law matches
    matches = detail.get("case_law_matches", [])
    if matches:
        st.subheader("Case Law Matches")
        for m in matches:
            st.text(f"  {m.get('label', '')} — {m.get('company_name', '')} "
                    f"(similarity: {m.get('similarity', 0):.0%})")


def _render_ach_tab(client, review_id, detail):
    """Render ACH analysis or rebuild button (legacy fallback)."""
    _render_ach_tab_v2(client, review_id, detail)


def _render_ach_tab_v2(client, review_id, detail):
    """Render enhanced ACH matrix view with grid, narratives, and rebuild."""
    from dashboard.views.ach_matrix import render_ach_view

    ach = detail.get("ach_summary")

    # Try fetching full ACH data from API if not in detail
    if not ach:
        ach_result = client.get_triage_ach(review_id)
        if ach_result and not ach_result.get("error"):
            ach = ach_result.get("data", ach_result)

    if ach:
        # Extract tribunal data if present in the ACH response
        tribunal_data = None
        if ach.get("bull_summary") or ach.get("bear_summary"):
            tribunal_data = {
                "bull_summary": ach.get("bull_summary", ""),
                "bear_summary": ach.get("bear_summary", ""),
                "differentiators": ach.get("differentiators", []),
                "differentiator_count": ach.get("differentiator_count", 0),
            }
        render_ach_view(ach, tribunal_data)
    else:
        st.info("No ACH analysis available. Click Rebuild to generate one.")

    # Rebuild + Export buttons
    if st.button("Rebuild ACH", key="rebuild_ach"):
        with st.spinner("Building ACH analysis..."):
            rebuild_result = client.rebuild_triage_ach(review_id)
            if rebuild_result and not rebuild_result.get("error"):
                st.success("ACH analysis rebuilt!")
                st.rerun()
            else:
                st.error(f"Rebuild failed: {rebuild_result.get('message', 'Unknown') if rebuild_result else 'No response'}")


def _render_audit_tab(detail):
    """Render audit history timeline."""
    history = detail.get("audit_history", [])
    if not history:
        st.info("No audit history available.")
        return

    for entry in history:
        action = entry.get("action_type", "")
        actor = entry.get("actor", "system")
        reason = entry.get("reason", "")
        ts = entry.get("created_at", "")
        st.text(f"[{ts[:19]}] {action} by {actor}" + (f" — {reason}" if reason else ""))


# =============================================================================
# ACTION BAR
# =============================================================================

def _render_action_bar(client, review_id, status, updated_at):
    """Two-step action confirmation with idempotency key persistence."""
    if status not in ("pending", "deferred"):
        st.caption(f"Status: {status.title()} — no actions available")
        return

    st.markdown("### Actions")

    pending_key = st.session_state.get("pending_action_key")
    pending_action = st.session_state.get("pending_action_type")

    if pending_key:
        # Step 2: confirm the pending action
        st.warning(f"Confirm **{pending_action}** for this item?")
        reason = st.text_input(
            "Reason",
            value=st.session_state.get("pending_reason", ""),
            key="confirm_reason",
        )
        st.session_state.pending_reason = reason

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if reason and st.button(f"Confirm {pending_action}"):
                action_map = {
                    "Approve": client.approve_triage,
                    "Reject": client.reject_triage,
                    "Defer": client.defer_triage,
                }
                fn = action_map.get(pending_action)
                if fn:
                    result = fn(review_id, reason, updated_at, pending_key)
                    if result and not result.get("error"):
                        st.success(f"{pending_action}d successfully!")
                        _clear_pending_action()
                        st.session_state.triage_cache_buster = st.session_state.get("triage_cache_buster", 0) + 1
                        st.rerun()
                    elif result and result.get("status_code") == 409:
                        st.error("Modified by another user. Please refresh.")
                    else:
                        st.error(f"Failed: {result.get('message', 'Unknown') if result else 'No response'}")
        with col_cancel:
            if st.button("Cancel"):
                _clear_pending_action()
                st.rerun()
    else:
        # Step 1: choose action
        cols = st.columns(3)
        with cols[0]:
            if st.button("Approve", type="primary"):
                _set_pending_action("Approve")
                st.rerun()
        with cols[1]:
            if st.button("Reject"):
                _set_pending_action("Reject")
                st.rerun()
        with cols[2]:
            if st.button("Defer"):
                _set_pending_action("Defer")
                st.rerun()


def _set_pending_action(action: str):
    """Set a pending action with a fresh idempotency key."""
    st.session_state.pending_action_key = str(uuid.uuid4())
    st.session_state.pending_action_type = action
    st.session_state.pending_reason = ""


def _clear_pending_action():
    """Clear pending action state."""
    for key in ("pending_action_key", "pending_action_type", "pending_reason"):
        if key in st.session_state:
            del st.session_state[key]
