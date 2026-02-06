"""
Ops Health Dashboard Page

Monitoring page for the ops layer:
- Overall ops health and component status
- Extraction trend charts
- Active alert panels
- Fact statistics

All data fetched from /health/ops and /health/ops/metrics endpoints.
"""

import streamlit as st
from typing import Any, Dict, List, Optional

from dashboard.api_client import APIClient

STATUS_COLORS = {
    "healthy": "#10B981",
    "degraded": "#F59E0B",
    "unhealthy": "#EF4444",
    "unknown": "#6B7280",
}

SEVERITY_COLORS = {
    "critical": "#EF4444",
    "warning": "#F59E0B",
    "info": "#3B82F6",
}


def render_ops_health_page():
    """Main entry point for the ops health dashboard page."""
    st.title("Ops Monitoring")
    client = APIClient()

    # Fetch ops health
    try:
        ops_health = client.get("/health/ops")
    except Exception as e:
        st.error(f"Could not fetch ops health: {e}")
        st.info("Ensure the API server is running and ops tables are initialized.")
        return

    if not ops_health or "error" in ops_health:
        st.warning("Ops layer not initialized. Run the pipeline first.")
        return

    # Overall status
    _render_overview(ops_health)

    # Component health
    _render_components(ops_health.get("components", {}))

    # Active alerts
    _render_alerts(ops_health.get("active_alerts", []))

    # Metrics + history
    try:
        metrics = client.get("/health/ops/metrics", params={"history_days": 7})
        if metrics:
            _render_extraction_trends(metrics)
            _render_fact_stats(metrics)
            _render_cost_summary(metrics)
            _render_collector_breakdown(metrics)
    except Exception as e:
        st.warning(f"Could not fetch metrics: {e}")


def _render_overview(data: Dict[str, Any]):
    """Render overall status banner."""
    status = data.get("status", "unknown")
    color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Status", status.upper())
    with col2:
        st.metric("Health", f"{data.get('overall_health_pct', 0):.0f}%")
    with col3:
        st.metric("Extractions (24h)", data.get("extractions_24h", 0))
    with col4:
        st.metric("Open Incidents", data.get("open_incidents", 0))

    st.divider()


def _render_components(components: Dict[str, Any]):
    """Render component health cards."""
    if not components:
        st.info("No health data recorded yet.")
        return

    st.subheader("Component Health")

    cols = st.columns(min(len(components), 4))
    for i, (name, metrics) in enumerate(components.items()):
        with cols[i % len(cols)]:
            pct = metrics.get("health_percent", 0)
            if pct >= 90:
                status = "healthy"
            elif pct >= 70:
                status = "degraded"
            else:
                status = "unhealthy"
            color = STATUS_COLORS[status]

            st.markdown(f"""
            <div style="
                border: 1px solid {color}40;
                border-radius: 8px;
                padding: 1rem;
                margin-bottom: 0.5rem;
            ">
                <div style="color: {color}; font-weight: 600;">{name}</div>
                <div style="font-size: 1.5rem; font-weight: 700;">{pct:.0f}%</div>
                <div style="font-size: 0.75rem; color: #6B7280;">
                    {metrics.get('total_checks', 0)} checks |
                    {metrics.get('avg_latency_ms', 0):.0f}ms avg
                </div>
            </div>
            """, unsafe_allow_html=True)


def _render_alerts(alerts: List[Dict[str, Any]]):
    """Render active alerts panel."""
    st.subheader("Active Alerts")

    if not alerts:
        st.success("No active alerts.")
        return

    for alert in alerts:
        sev = alert.get("severity", "info")
        color = SEVERITY_COLORS.get(sev, "#6B7280")
        st.markdown(f"""
        <div style="
            border-left: 4px solid {color};
            padding: 0.5rem 1rem;
            margin-bottom: 0.5rem;
            background: {color}10;
            border-radius: 0 4px 4px 0;
        ">
            <strong style="color: {color};">[{sev.upper()}]</strong>
            {alert.get('rule', 'Unknown')}: {alert.get('message', '')}
        </div>
        """, unsafe_allow_html=True)


def _render_extraction_trends(metrics: Dict[str, Any]):
    """Render extraction trend chart."""
    history = metrics.get("daily_history", [])
    if not history:
        return

    st.subheader("Extraction Trends (7 days)")

    import pandas as pd
    df = pd.DataFrame(history)
    if "date" in df.columns and "runs" in df.columns:
        df = df.sort_values("date")
        st.bar_chart(df.set_index("date")["runs"])


def _render_fact_stats(metrics: Dict[str, Any]):
    """Render fact statistics summary."""
    st.subheader("Fact Statistics")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Facts", metrics.get("total_facts", 0))
    with col2:
        facts = metrics.get("facts_by_status", {})
        st.metric("Active", facts.get("active", 0))
    with col3:
        st.metric("Avg Confidence", f"{metrics.get('avg_fact_confidence', 0):.2f}")
    with col4:
        st.metric("Unused HC", metrics.get("unused_high_confidence_facts", 0))


def _render_cost_summary(metrics: Dict[str, Any]):
    """Render cost summary KPI row: Cost 24h, Avg Duration, All-Time Runs."""
    st.subheader("Cost Summary")

    cost_24h = float(metrics.get("api_cost_24h", 0))
    avg_duration = float(metrics.get("avg_run_duration_sec", 0))
    total_runs = int(metrics.get("total_pipeline_runs", 0))

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Cost (24h)", f"${cost_24h:.2f}")
    with col2:
        if avg_duration >= 60:
            dur_str = f"{avg_duration / 60:.1f}m"
        else:
            dur_str = f"{avg_duration:.0f}s"
        st.metric("Avg Duration", dur_str)
    with col3:
        st.metric("All-Time Runs", total_runs)


def _render_collector_breakdown(metrics: Dict[str, Any]):
    """Render daily cost bar chart from daily_history."""
    history = metrics.get("daily_history", [])
    if not history:
        st.info("No daily cost data available yet.")
        return

    st.subheader("Daily Cost Breakdown")

    import pandas as pd
    df = pd.DataFrame(history)
    if "date" in df.columns and "cost" in df.columns:
        df["cost"] = df["cost"].astype(float)
        df = df.sort_values("date")
        st.bar_chart(df.set_index("date")["cost"])
