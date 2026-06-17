# dashboard/views/signal_flow.py
"""
Signal Flow Dashboard

Shows signal counts at each pipeline stage:
  Collected -> Pending -> Processed -> Pushed to Notion

All data is read-only from the API. Never queries signals.db directly.
"""
from __future__ import annotations

import streamlit as st

from dashboard.api_client import APIClient


def render_signal_flow_page() -> None:
    st.title("Signal Flow")
    st.caption("Read-only view -- counts at each pipeline stage derived from /health/stats")

    client = APIClient()

    try:
        stats = client.get("/pipeline/stats")
    except Exception as e:
        st.error(f"Could not fetch pipeline stats: {e}")
        st.info("Start the API server: `python -m api.app`")
        return

    if not stats:
        st.warning("No pipeline stats returned.")
        return

    # Stage funnel
    collected = stats.get("signals_collected", 0)
    stored = stats.get("signals_stored", 0)
    pending = stats.get("signals_pending", 0)
    processed = stats.get("signals_processed", 0)
    pushed = stats.get("signals_pushed_to_notion", 0)
    suppressed = stats.get("signals_suppressed", 0)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Collected", collected)
    col2.metric("Pending", pending)
    col3.metric("Processed", processed)
    col4.metric("Pushed to Notion", pushed)

    st.divider()
    st.subheader("Funnel breakdown")

    import pandas as pd
    funnel_data = pd.DataFrame({
        "Stage": ["Collected", "Stored", "Pending", "Processed", "Pushed"],
        "Count": [collected, stored, pending, processed, pushed],
    })
    st.bar_chart(funnel_data.set_index("Stage"))

    st.subheader("Suppression")
    st.metric("Suppressed (dedup + cache)", suppressed)

    st.divider()
    st.caption(
        "Source: `/pipeline/stats` API endpoint. "
        "Refresh the page for latest counts. "
        "This view never writes to the database."
    )
