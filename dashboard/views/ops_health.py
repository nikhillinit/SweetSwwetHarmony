"""
Ops Health Dashboard Page

Monitoring page for the ops layer:
- Overall ops health and component status
- Extraction trend charts
- Active alert panels
- Fact statistics
- Alert rule management (Phase 5 CRUD)
- Metric history timeline (Phase 5 trends)
- Alert evaluation log (Phase 5 audit trail)

All data fetched from /health/ops, /health/ops/metrics, /health/ops/rules,
and /health/ops/history endpoints.
"""

import json
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
        st.info("Check that the API server is running. You may need to start it with `python -m api.app`.")
        return

    if not ops_health or "error" in ops_health:
        st.warning("No ops data found yet. Run the pipeline at least once to populate monitoring metrics.")
        return

    # Tabs layout
    tab_overview, tab_rules, tab_history, tab_evals = st.tabs(
        ["OVERVIEW", "ALERT RULES", "METRIC HISTORY", "EVALUATION LOG"]
    )

    with tab_overview:
        _render_overview_tab(ops_health, client)

    with tab_rules:
        _render_rules_tab(client)

    with tab_history:
        _render_metric_history_tab(client)

    with tab_evals:
        _render_evaluation_log_tab(client)


# =============================================================================
# OVERVIEW TAB (existing content, restructured)
# =============================================================================

def _render_overview_tab(ops_health: Dict[str, Any], client: APIClient):
    """Render the overview tab with existing ops health content."""
    _render_overview(ops_health)
    _render_components(ops_health.get("components", {}))
    _render_alerts(ops_health.get("active_alerts", []))

    try:
        metrics = client.get("/health/ops/metrics", params={"history_days": 7})
        if metrics and not (isinstance(metrics, dict) and metrics.get("error")):
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


# =============================================================================
# ALERT RULES TAB
# =============================================================================

def _render_rules_tab(client: APIClient):
    """Render alert rules management tab: list, toggle, delete, create."""
    st.subheader("Alert Rules")

    rules = client.get("/health/ops/rules")

    if not rules or (isinstance(rules, dict) and rules.get("error")):
        rules = []

    # --- Rules table ---
    if rules:
        import pandas as pd

        table_rows = []
        for rule in rules:
            sev = rule.get("severity", "info")
            enabled = bool(rule.get("enabled"))
            builtin = bool(rule.get("is_builtin"))
            table_rows.append({
                "ID": rule.get("id", ""),
                "Name": rule.get("name", ""),
                "Severity": sev.upper(),
                "Enabled": "Yes" if enabled else "No",
                "Type": "BUILTIN" if builtin else "Custom",
                "Message": rule.get("message_template", ""),
            })

        df = pd.DataFrame(table_rows)
        st.dataframe(df, use_container_width=True)

        # Per-rule actions
        for rule in rules:
            rid = rule.get("id")
            name = rule.get("name", "?")
            builtin = bool(rule.get("is_builtin"))
            enabled = bool(rule.get("enabled"))

            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                sev = rule.get("severity", "info")
                color = SEVERITY_COLORS.get(sev, "#6B7280")
                badge = "BUILTIN" if builtin else "Custom"
                st.markdown(
                    f"**{name}** "
                    f'<span style="color:{color};">[{sev.upper()}]</span> '
                    f'<span style="color:#6B7280;">({badge})</span>',
                    unsafe_allow_html=True,
                )
            with col2:
                new_state = st.checkbox(
                    "Enabled", value=enabled, key=f"rule_toggle_{rid}",
                )
                if new_state != enabled:
                    client.put(f"/health/ops/rules/{rid}", json={"enabled": new_state})
                    st.rerun()
            with col3:
                if not builtin:
                    if st.button("Delete", key=f"rule_del_{rid}"):
                        client.delete(f"/health/ops/rules/{rid}")
                        st.rerun()
    else:
        st.info("No alert rules configured. Create one below.")

    # --- Create rule form ---
    st.divider()
    st.subheader("Create New Rule")

    with st.form("create_rule_form"):
        rule_name = st.text_input("Rule Name", placeholder="e.g. high_cost_alert")
        severity = st.selectbox("Severity", options=["warning", "critical", "info"])
        condition_str = st.text_area(
            "Condition (JSON DSL)",
            placeholder='{"field": "total_cost_24h", "op": ">", "value": 5.0}',
            height=100,
        )
        message_tpl = st.text_input("Message Template", placeholder="Cost exceeded threshold")
        submitted = st.form_submit_button("Create Rule")

        if submitted and rule_name and condition_str:
            try:
                condition = json.loads(condition_str)
            except json.JSONDecodeError:
                st.error("Invalid JSON in condition field.")
                return

            result = client.post("/health/ops/rules", json={
                "name": rule_name,
                "condition": condition,
                "severity": severity,
                "message_template": message_tpl or rule_name,
            })

            if result and not (isinstance(result, dict) and result.get("error")):
                st.success(f"Rule '{rule_name}' created successfully.")
                st.rerun()
            else:
                detail = result.get("detail", result.get("message", "Unknown error")) if result else "No response"
                st.warning(f"Failed to create rule: {detail}")


# =============================================================================
# METRIC HISTORY TAB
# =============================================================================

def _render_metric_history_tab(client: APIClient, hours: int = 24):
    """Render metric history tab with time-series charts."""
    st.subheader("Metric Snapshots")

    snapshots = client.get("/health/ops/history", params={"hours": hours})

    if not snapshots or (isinstance(snapshots, dict) and snapshots.get("error")):
        st.info("No metric snapshots recorded yet. Snapshots are saved during alert evaluation.")
        return

    if not isinstance(snapshots, list) or len(snapshots) == 0:
        st.info("No metric snapshots recorded yet. Snapshots are saved during alert evaluation.")
        return

    # Parse snapshot JSON and build time series
    import pandas as pd

    rows = []
    for snap in snapshots:
        ts = snap.get("timestamp", "")
        raw = snap.get("snapshot_json", "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        rows.append({
            "timestamp": ts,
            "Health %": float(data.get("overall_health_pct", 0)),
            "Extractions (24h)": int(data.get("extractions_24h", 0)),
            "Cost (24h)": float(data.get("total_cost_24h", 0)),
            "Open Incidents": int(data.get("open_incidents", 0)),
        })

    if not rows:
        st.info("No valid snapshot data to display.")
        return

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # KPI: latest snapshot values
    latest = rows[-1]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Health %", f"{latest['Health %']:.0f}%")
    with col2:
        st.metric("Extractions", latest["Extractions (24h)"])
    with col3:
        st.metric("Cost (24h)", f"${latest['Cost (24h)']:.2f}")
    with col4:
        st.metric("Incidents", latest["Open Incidents"])

    # Altair line charts
    import altair as alt

    metrics_to_chart = ["Health %", "Extractions (24h)", "Cost (24h)", "Open Incidents"]

    # Melt into long-form for Altair
    df_long = df.melt(
        id_vars=["timestamp"],
        value_vars=metrics_to_chart,
        var_name="metric",
        value_name="value",
    )

    chart = (
        alt.Chart(df_long)
        .mark_line(point=True)
        .encode(
            x=alt.X("timestamp:T", axis=alt.Axis(title=None, format="%H:%M")),
            y=alt.Y("value:Q", axis=alt.Axis(title=None)),
            color=alt.Color("metric:N", legend=alt.Legend(title=None)),
            tooltip=["timestamp:T", "metric:N", "value:Q"],
        )
        .properties(height=300)
        .facet(
            facet=alt.Facet("metric:N", title=None),
            columns=2,
        )
        .resolve_scale(y="independent")
    )

    st.altair_chart(chart, use_container_width=True)


# =============================================================================
# EVALUATION LOG TAB
# =============================================================================

def _render_evaluation_log_tab(client: APIClient):
    """Render alert evaluation log: when rules fired and resolved."""
    st.subheader("Alert Evaluation History")

    # Fetch recent evaluations from all rules
    evaluations = client.get("/health/ops/history", params={"hours": 168})

    # Try to get evaluations via rules detail endpoint
    rules = client.get("/health/ops/rules")
    all_evals = []

    if rules and isinstance(rules, list):
        for rule in rules:
            rid = rule.get("id")
            if rid is None:
                continue
            detail = client.get(f"/health/ops/rules/{rid}")
            if detail and isinstance(detail, dict):
                rule_evals = detail.get("evaluations", [])
                for ev in rule_evals:
                    ev["rule_name"] = ev.get("rule_name", rule.get("name", "?"))
                    all_evals.append(ev)

    if not all_evals:
        st.info("No alert evaluations recorded yet. Evaluations are logged when rules fire.")
        return

    import pandas as pd

    rows = []
    for ev in all_evals:
        sev = ev.get("severity", "info")
        resolved = ev.get("resolved_at")
        status = "Resolved" if resolved else "Open"
        rows.append({
            "Rule": ev.get("rule_name", "?"),
            "Severity": sev.upper(),
            "Message": ev.get("message", ""),
            "Fired At": ev.get("fired_at", ""),
            "Resolved At": resolved or "-",
            "Status": status,
        })

    df = pd.DataFrame(rows)

    # Render with severity color coding via markdown
    for _, row in df.iterrows():
        sev_lower = row["Severity"].lower()
        color = SEVERITY_COLORS.get(sev_lower, "#6B7280")
        status_color = "#10B981" if row["Status"] == "Resolved" else "#EF4444"
        st.markdown(
            f'<div style="border-left: 3px solid {color}; padding: 0.3rem 0.8rem; margin-bottom: 0.3rem;">'
            f'<strong style="color:{color};">[{row["Severity"]}]</strong> '
            f'{row["Rule"]}: {row["Message"]} '
            f'<span style="color:#6B7280; font-size:0.8rem;">({row["Fired At"]})</span> '
            f'<span style="color:{status_color}; font-weight:600;">{row["Status"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.divider()
    st.dataframe(df, use_container_width=True)
