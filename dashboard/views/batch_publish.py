"""
Batch Publish View — create, preview, commit, abort batch workflows.

Features:
- Create tab: batch creation + preview + commit/abort
- Active batches tab: list with status
- Idempotency keys for commit and abort
- TOCTOU guard via items_hash
"""

from __future__ import annotations

import uuid

import streamlit as st

from dashboard.api_client import APIClient


def render_batch_publish_page():
    """Render the Batch Publish view."""
    st.title("Batch Publish")

    client = APIClient()

    tab_create, tab_active = st.tabs(["Create New", "Active Batches"])

    with tab_create:
        _render_create_tab(client)

    with tab_active:
        _render_active_tab(client)


# =============================================================================
# CREATE TAB
# =============================================================================

def _render_create_tab(client):
    """Batch creation workflow: create → preview → commit/abort."""
    # Check for active draft in session
    draft_batch_id = st.session_state.get("batch_draft_id")

    if draft_batch_id:
        _render_draft_preview(client, draft_batch_id)
    else:
        _render_create_form(client)


def _render_create_form(client):
    """Form to create a new batch."""
    st.markdown("Create a batch of approved reviews for Notion publishing.")

    limit = st.number_input(
        "Batch size",
        min_value=1,
        max_value=100,
        value=50,
        help="Maximum number of reviews to include",
    )

    if st.button("Create Batch"):
        with st.spinner("Creating batch..."):
            result = client.create_batch(limit=limit)
            if result and not result.get("error"):
                data = result.get("data", result)
                batch_id = data.get("batch_id")
                st.session_state.batch_draft_id = batch_id
                st.session_state.batch_items_hash = data.get("items_hash")
                st.success(f"Batch {batch_id} created with {data.get('item_count', 0)} items")
                st.rerun()
            else:
                st.error(f"Failed: {result.get('message', result.get('detail', 'Unknown')) if result else 'No response'}")


def _render_draft_preview(client, batch_id):
    """Preview and commit/abort a draft batch."""
    st.markdown(f"### Batch: `{batch_id}`")

    # Fetch preview
    preview = client.get_batch_preview(batch_id)
    if not preview or preview.get("error"):
        st.error(f"Could not load batch: {preview.get('message', 'Unknown') if preview else 'No response'}")
        if st.button("Clear Draft"):
            _clear_draft()
            st.rerun()
        return

    data = preview.get("data", preview)
    items = data.get("items", [])
    items_hash = data.get("items_hash", "")
    item_count = data.get("item_count", len(items))
    status = data.get("status", "")

    if status != "draft":
        st.info(f"Batch status: {status} — no longer editable.")
        if st.button("Clear"):
            _clear_draft()
            st.rerun()
        return

    st.metric("Items", item_count)

    # Items table
    if items:
        for item in items:
            cols = st.columns([3, 1, 1, 1])
            with cols[0]:
                st.text(item.get("company_name", item.get("company_id", "—")))
            with cols[1]:
                conf = item.get("confidence")
                st.caption(f"{conf:.0%}" if conf is not None else "—")
            with cols[2]:
                st.caption(item.get("canonical_key", "")[:20])
            with cols[3]:
                st.caption(item.get("status", ""))

    # Commit / Abort actions
    st.divider()
    batch_action_key = st.session_state.get("batch_action_key")

    col_commit, col_abort, col_clear = st.columns(3)

    with col_commit:
        dry_run = st.checkbox("Dry run", value=False, key="batch_dry_run")
        if st.button("Commit Batch", type="primary"):
            if not batch_action_key:
                batch_action_key = str(uuid.uuid4())
                st.session_state.batch_action_key = batch_action_key

            with st.spinner("Committing..."):
                result = client.commit_batch(
                    batch_id, items_hash, dry_run=dry_run,
                    idempotency_key=batch_action_key,
                )
                if result and not result.get("error"):
                    st.success("Batch committed!")
                    _clear_draft()
                    st.rerun()
                elif result and result.get("status_code") == 409:
                    st.error("Batch contents changed since preview. Please re-preview.")
                    _clear_draft()
                    st.rerun()
                else:
                    st.error(f"Commit failed: {result.get('message', 'Unknown') if result else 'No response'}")
                    st.session_state.batch_action_key = None

    with col_abort:
        reason = st.text_input("Abort reason", key="abort_reason", placeholder="Optional reason...")
        if st.button("Abort Batch"):
            abort_key = str(uuid.uuid4())
            with st.spinner("Aborting..."):
                result = client.abort_batch(batch_id, reason=reason, idempotency_key=abort_key)
                if result and not result.get("error"):
                    st.success("Batch aborted. Reviews reverted to approved.")
                    _clear_draft()
                    st.rerun()
                else:
                    st.error(f"Abort failed: {result.get('message', 'Unknown') if result else 'No response'}")

    with col_clear:
        if st.button("Discard"):
            _clear_draft()
            st.rerun()


# =============================================================================
# ACTIVE BATCHES TAB
# =============================================================================

def _render_active_tab(client):
    """List active and recent batches."""
    result = client.list_batches(limit=20)
    if not result or result.get("error"):
        st.info("No batches found or API unavailable.")
        return

    batches = result.get("data", [])
    if not batches:
        st.info("No batches found.")
        return

    for batch in batches:
        batch_id = batch.get("batch_id", "")
        status = batch.get("status", "")
        count = batch.get("item_count", 0)
        pushed = batch.get("pushed_count")
        created = batch.get("created_at", "")[:19]
        actor = batch.get("actor", "")

        cols = st.columns([2, 1, 1, 1, 2])
        with cols[0]:
            st.text(f"{batch_id[:12]}...")
        with cols[1]:
            st.caption(status)
        with cols[2]:
            st.caption(f"{count} items")
        with cols[3]:
            pushed_text = f"{pushed} pushed" if pushed is not None else "—"
            st.caption(pushed_text)
        with cols[4]:
            st.caption(f"{created} by {actor}")


# =============================================================================
# HELPERS
# =============================================================================

def _clear_draft():
    """Clear draft batch session state."""
    for key in ("batch_draft_id", "batch_items_hash", "batch_action_key"):
        if key in st.session_state:
            del st.session_state[key]
