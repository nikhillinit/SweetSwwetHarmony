"""
Drift Monitoring Dashboard View (Wave 5).

4 tabs:
1. SPC Charts — Faceted line charts with UCL/LCL bands
2. Canary Status — Latest run, pass rate, verdict
3. Alert Timeline — Open/ack/snoozed/resolved with action buttons
4. Recommendations — Priority-sorted cards with evidence citations

Data fetched from /api/v1/canary/* endpoints via APIClient.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import streamlit as st

from dashboard.api_client import APIClient


# Colors for alert statuses
ALERT_STATUS_COLORS = {
    "open": "#EF4444",       # Red
    "acknowledged": "#F59E0B",  # Amber
    "snoozed": "#6B7280",    # Gray
    "resolved": "#10B981",   # Green
}

SEVERITY_COLORS = {
    "critical": "#EF4444",
    "warning": "#F59E0B",
    "info": "#3B82F6",
}

PRIORITY_COLORS = {
    "high": "#EF4444",
    "medium": "#F59E0B",
    "low": "#3B82F6",
}

VERDICT_COLORS = {
    "pass": "#10B981",
    "fail": "#EF4444",
    "error": "#F59E0B",
}


def render_drift_monitoring_page():
    """Main entry point for the drift monitoring dashboard page."""
    st.title("Drift Monitoring")

    client = APIClient()

    tab_spc, tab_canary, tab_alerts, tab_recs = st.tabs(
        ["SPC CHARTS", "CANARY STATUS", "ALERT TIMELINE", "RECOMMENDATIONS"]
    )

    with tab_spc:
        _render_spc_tab(client)

    with tab_canary:
        _render_canary_tab(client)

    with tab_alerts:
        _render_alerts_tab(client)

    with tab_recs:
        _render_recommendations_tab(client)


# =============================================================================
# SPC CHARTS TAB
# =============================================================================

def _render_spc_tab(client: APIClient):
    """Render SPC control charts with UCL/LCL bands."""
    st.subheader("Statistical Process Control")

    try:
        result = client.post("/canary/spc/check", json={})
    except Exception as e:
        st.error(f"Could not fetch SPC data: {e}")
        return

    if not result or result.get("error"):
        msg = result.get("message", "Unknown error") if result else "No response"
        st.info(f"SPC check unavailable: {msg}")
        return

    data = result.get("data", {})
    metrics = data.get("metrics", [])

    if not metrics:
        st.info("No SPC metrics available yet. Run the daily aggregator first.")
        return

    for metric_result in metrics:
        metric_name = metric_result.get("metric", "unknown")
        verdict = metric_result.get("verdict", "unknown")
        friendly = metric_name.replace("_", " ").title()

        verdict_color = "#10B981" if verdict == "in_control" else (
            "#EF4444" if verdict == "out_of_control" else "#6B7280"
        )

        st.markdown(
            f"**{friendly}** — "
            f"<span style='color:{verdict_color}'>{verdict.replace('_', ' ').upper()}</span>",
            unsafe_allow_html=True,
        )

        limits = metric_result.get("limits", {})
        if limits:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Mean", f"{limits.get('mean', 0):.4f}")
            with col2:
                st.metric("UCL", f"{limits.get('ucl', 0):.4f}")
            with col3:
                st.metric("LCL", f"{limits.get('lcl', 0):.4f}")

        alerts = metric_result.get("alerts", [])
        if alerts:
            for alert in alerts:
                st.warning(alert.get("message", "Alert"))

        st.markdown("---")


# =============================================================================
# CANARY STATUS TAB
# =============================================================================

def _render_canary_tab(client: APIClient):
    """Render canary status overview."""
    st.subheader("Canary Status")

    try:
        status = client.get("/canary/status")
    except Exception as e:
        st.error(f"Could not fetch canary status: {e}")
        return

    if not status or status.get("error"):
        st.info("No canary data available.")
        return

    data = status.get("data", {})

    # Top-level metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        verdict = data.get("latest_verdict")
        color = VERDICT_COLORS.get(verdict, "#6B7280") if verdict else "#6B7280"
        st.metric("Latest Verdict", (verdict or "N/A").upper())
    with col2:
        pass_rate = data.get("latest_pass_rate")
        st.metric("Pass Rate", f"{pass_rate:.1%}" if pass_rate is not None else "N/A")
    with col3:
        st.metric("Total Runs", data.get("total_runs", 0))
    with col4:
        st.metric("Open Alerts", data.get("open_alerts", 0))

    last_run = data.get("latest_run_at")
    if last_run:
        st.caption(f"Last run: {last_run}")

    # Recent runs
    st.markdown("---")
    st.markdown("### Recent Canary Runs")

    try:
        runs_resp = client.get("/canary/runs", params={"limit": 10})
    except Exception as e:
        st.warning(f"Could not fetch runs: {e}")
        return

    if not runs_resp or runs_resp.get("error"):
        st.info("No canary runs found.")
        return

    runs = runs_resp.get("data", [])
    if not runs:
        st.info("No canary runs found.")
        return

    for run in runs:
        verdict = run.get("verdict", "unknown")
        color = VERDICT_COLORS.get(verdict, "#6B7280")
        pass_rate = run.get("pass_rate")
        pr_str = f"{pass_rate:.1%}" if pass_rate is not None else "N/A"

        st.markdown(
            f"- **Run {run.get('run_id', '?')}** — "
            f"<span style='color:{color}'>{verdict.upper()}</span> "
            f"(pass rate: {pr_str}, scored: {run.get('total_scored', 0)}, "
            f"created: {run.get('created_at', 'N/A')})",
            unsafe_allow_html=True,
        )


# =============================================================================
# ALERT TIMELINE TAB
# =============================================================================

def _render_alerts_tab(client: APIClient):
    """Render alert timeline with action buttons."""
    st.subheader("Drift Alerts")

    # Alert stats
    try:
        stats_resp = client.get("/canary/drift-alerts/stats")
    except Exception as e:
        st.warning(f"Could not fetch alert stats: {e}")
        stats_resp = None

    if stats_resp and not stats_resp.get("error"):
        stats = stats_resp.get("data", {})
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Open", stats.get("open", 0))
        with col2:
            st.metric("Acknowledged", stats.get("acknowledged", 0))
        with col3:
            st.metric("Snoozed", stats.get("snoozed", 0))
        with col4:
            st.metric("Resolved", stats.get("resolved", 0))

        mtta = stats.get("mtta_p50_seconds")
        if mtta is not None:
            st.caption(f"MTTA (p50): {mtta:.0f}s")

    # Filters
    st.markdown("---")
    status_filter = st.selectbox(
        "Filter by status",
        ["all", "open", "acknowledged", "snoozed", "resolved"],
        index=0,
    )

    # Fetch alerts
    params = {"limit": 50}
    if status_filter != "all":
        params["status"] = status_filter

    try:
        alerts_resp = client.get("/canary/drift-alerts", params=params)
    except Exception as e:
        st.error(f"Could not fetch alerts: {e}")
        return

    if not alerts_resp or alerts_resp.get("error"):
        st.info("No drift alerts found.")
        return

    alerts = alerts_resp.get("data", [])
    if not alerts:
        st.info("No drift alerts match the current filter.")
        return

    for alert in alerts:
        _render_alert_card(client, alert)


def _render_alert_card(client: APIClient, alert: Dict[str, Any]):
    """Render a single alert card with action buttons."""
    alert_id = alert.get("id")
    status = alert.get("status", "open")
    severity = alert.get("severity", "info")
    color = ALERT_STATUS_COLORS.get(status, "#6B7280")
    sev_color = SEVERITY_COLORS.get(severity, "#6B7280")

    st.markdown(
        f"**#{alert_id}** "
        f"<span style='color:{sev_color}'>[{severity.upper()}]</span> "
        f"<span style='color:{color}'>{status.upper()}</span> — "
        f"{alert.get('message', 'No message')}",
        unsafe_allow_html=True,
    )

    meta_parts = []
    if alert.get("alert_type"):
        meta_parts.append(f"Type: {alert['alert_type']}")
    if alert.get("metric_name"):
        meta_parts.append(f"Metric: {alert['metric_name']}")
    if alert.get("occurrence_count", 1) > 1:
        meta_parts.append(f"Occurrences: {alert['occurrence_count']}")
    if alert.get("created_at"):
        meta_parts.append(f"Created: {alert['created_at']}")

    if meta_parts:
        st.caption(" | ".join(meta_parts))

    # Action buttons (only for non-resolved alerts)
    if status != "resolved":
        cols = st.columns(3)
        if status == "open":
            with cols[0]:
                if st.button("Acknowledge", key=f"ack_{alert_id}_{uuid.uuid4().hex[:6]}"):
                    _ack_alert(client, alert_id)
            with cols[1]:
                if st.button("Snooze 24h", key=f"snz_{alert_id}_{uuid.uuid4().hex[:6]}"):
                    _snooze_alert(client, alert_id, hours=24)
        if status in ("open", "acknowledged", "snoozed"):
            with cols[2]:
                if st.button("Resolve", key=f"rsv_{alert_id}_{uuid.uuid4().hex[:6]}"):
                    _resolve_alert(client, alert_id)

    st.markdown("---")


def _ack_alert(client: APIClient, alert_id: int):
    """Acknowledge an alert."""
    try:
        resp = client.post(
            f"/canary/drift-alerts/{alert_id}/acknowledge",
            json={"reason": "Acknowledged via dashboard"},
        )
        if resp and not resp.get("error"):
            st.success(f"Alert #{alert_id} acknowledged.")
        else:
            st.error(f"Failed to acknowledge: {resp}")
    except Exception as e:
        st.error(f"Error: {e}")


def _snooze_alert(client: APIClient, alert_id: int, hours: int = 24):
    """Snooze an alert."""
    try:
        resp = client.post(
            f"/canary/drift-alerts/{alert_id}/snooze",
            json={"hours": hours, "reason": "Snoozed via dashboard"},
        )
        if resp and not resp.get("error"):
            st.success(f"Alert #{alert_id} snoozed for {hours}h.")
        else:
            st.error(f"Failed to snooze: {resp}")
    except Exception as e:
        st.error(f"Error: {e}")


def _resolve_alert(client: APIClient, alert_id: int):
    """Resolve an alert."""
    try:
        resp = client.post(
            f"/canary/drift-alerts/{alert_id}/resolve",
            json={"resolution": "Resolved via dashboard"},
        )
        if resp and not resp.get("error"):
            st.success(f"Alert #{alert_id} resolved.")
        else:
            st.error(f"Failed to resolve: {resp}")
    except Exception as e:
        st.error(f"Error: {e}")


# =============================================================================
# RECOMMENDATIONS TAB
# =============================================================================

def _render_recommendations_tab(client: APIClient):
    """Render priority-sorted recommendation cards."""
    st.subheader("Drift Recommendations")

    try:
        resp = client.get("/canary/drift-alerts", params={"status": "open", "limit": 50})
    except Exception as e:
        st.warning(f"Could not fetch alerts for recommendations: {e}")
        return

    if not resp or resp.get("error"):
        st.info("No recommendations available.")
        return

    alerts = resp.get("data", [])
    if not alerts:
        st.info("No open alerts — no recommendations needed.")
        return

    # Group alerts by type for recommendations
    by_type: Dict[str, list] = {}
    for a in alerts:
        atype = a.get("alert_type", "unknown")
        by_type.setdefault(atype, []).append(a)

    recs_shown = 0

    # Archetype regressions (>=3 → expand golden set)
    arch_alerts = by_type.get("archetype_regression", [])
    if len(arch_alerts) >= 3:
        _render_rec_card(
            priority="high",
            title="Expand Golden Set",
            message=f"{len(arch_alerts)} archetype regressions detected. "
                    "Consider expanding golden set coverage for affected archetypes.",
            evidence_count=len(arch_alerts),
        )
        recs_shown += 1

    # Pass rate drops
    pr_alerts = by_type.get("pass_rate_drop", [])
    if pr_alerts:
        _render_rec_card(
            priority="high",
            title="Investigate Collector Quality",
            message=f"{len(pr_alerts)} pass rate drop(s) detected. "
                    "Review collector quality and recent signal patterns.",
            evidence_count=len(pr_alerts),
        )
        recs_shown += 1

    # Trend alerts
    trend_alerts = by_type.get("trend_alert", [])
    if trend_alerts:
        _render_rec_card(
            priority="medium",
            title="Adjust Confidence Threshold",
            message="FP rate shows sustained upward trend. "
                    "Consider adjusting MIN_CONFIDENCE threshold.",
            evidence_count=len(trend_alerts),
        )
        recs_shown += 1

    # Individual drift
    drift_alerts = by_type.get("individual_drift", [])
    if drift_alerts:
        _render_rec_card(
            priority="medium",
            title="Review Scoring Model",
            message=f"{len(drift_alerts)} individual drift alert(s). "
                    "Consider recalibrating scoring model.",
            evidence_count=len(drift_alerts),
        )
        recs_shown += 1

    if recs_shown == 0:
        st.info("Open alerts exist but no specific recommendations triggered.")


def _render_rec_card(
    priority: str,
    title: str,
    message: str,
    evidence_count: int = 0,
):
    """Render a single recommendation card."""
    color = PRIORITY_COLORS.get(priority, "#6B7280")

    st.markdown(
        f"<span style='color:{color}; font-weight:bold'>[{priority.upper()}]</span> "
        f"**{title}**",
        unsafe_allow_html=True,
    )
    st.markdown(message)
    if evidence_count:
        st.caption(f"Based on {evidence_count} alert(s)")
    st.markdown("---")
