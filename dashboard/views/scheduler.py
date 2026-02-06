"""
Scheduler Dashboard Page

View and manage pipeline schedules:
- KPI metrics (total, active, paused)
- Schedule cards with actions (pause/resume/delete/trigger)
- Create new schedule form
- Run history table with signal counts

All data fetched from /schedules/* API endpoints.
"""

import streamlit as st
from datetime import datetime
from typing import Any, Dict, List, Optional

from dashboard.api_client import APIClient


# =============================================================================
# HELPERS
# =============================================================================

def _format_cron_human(expr: str) -> str:
    """Convert cron expression to human-readable string (pure string matching)."""
    patterns = {
        "* * * * *": "Every minute",
        "0 * * * *": "Every hour",
        "0 0 * * *": "Daily at midnight",
        "0 0 * * 0": "Weekly (Sunday)",
        "0 0 1 * *": "Monthly (1st)",
    }
    if expr in patterns:
        return patterns[expr]

    parts = expr.split()
    if len(parts) == 5:
        minute, hour, dom, mon, dow = parts
        # Weekday patterns
        if dow == "1-5" and dom == "*" and mon == "*":
            return f"Weekdays at {hour.zfill(2)}:{minute.zfill(2)}"
        # Daily at specific time
        if dom == "*" and mon == "*" and dow == "*" and hour != "*" and minute != "*":
            return f"Daily at {hour.zfill(2)}:{minute.zfill(2)}"
        # Hourly at specific minute
        if hour == "*" and dom == "*" and mon == "*" and dow == "*" and minute != "*":
            return f"Every hour at :{minute.zfill(2)}"

    return f"Cron: {expr}"


def _format_duration(started: Optional[str], finished: Optional[str]) -> str:
    """Format duration between two ISO timestamps."""
    if not started or not finished:
        return "-"

    try:
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        delta = end - start
        total_seconds = int(delta.total_seconds())

        if total_seconds <= 0:
            return "0s"

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except (ValueError, TypeError):
        return "-"


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def render_scheduler_page():
    """Main entry point for the scheduler dashboard page."""
    st.title("Pipeline Schedules")
    client = APIClient()

    # Fetch schedules
    schedules = client.get("/schedules")

    if schedules and isinstance(schedules, dict) and schedules.get("error"):
        st.warning("Could not fetch schedules. Ensure the API server is running.")
        return

    if not schedules or not isinstance(schedules, list):
        schedules = []

    # Tabs
    tab_schedules, tab_history = st.tabs(["SCHEDULES", "RUN HISTORY"])

    with tab_schedules:
        _render_schedules_tab(client, schedules)

    with tab_history:
        _render_history_tab(client, schedules)


# =============================================================================
# SCHEDULES TAB
# =============================================================================

def _render_schedules_tab(client: APIClient, schedules: List[Dict[str, Any]]):
    """Render the schedules management tab."""
    total = len(schedules)
    active = sum(1 for s in schedules if s.get("enabled"))
    paused = total - active

    # KPI row
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total", total)
    with col2:
        st.metric("Active", active)
    with col3:
        st.metric("Paused", paused)

    st.divider()

    # Schedule cards
    if not schedules:
        st.info("No schedules configured yet. Create one below.")
    else:
        for sched in schedules:
            _render_schedule_card(client, sched)

    # Create form
    st.divider()
    with st.expander("Create New Schedule"):
        _render_create_form(client)


def _render_schedule_card(client: APIClient, sched: Dict[str, Any]):
    """Render a single schedule card with actions."""
    sid = sched.get("id")
    name = sched.get("name", "Unnamed")
    cron = sched.get("cron_expression", "")
    collectors = sched.get("collectors", "") or ""
    enabled = sched.get("enabled", True)
    mode = sched.get("mode", "full")
    dry_run = sched.get("dry_run", False)

    status_text = "Active" if enabled else "Paused"
    status_color = "#10B981" if enabled else "#F59E0B"

    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown(f"**{name}**")
        st.caption(
            f"{_format_cron_human(cron)}  |  "
            f"Mode: {mode}  |  "
            f"Collectors: {collectors if collectors else 'all'}  |  "
            f"Dry run: {'yes' if dry_run else 'no'}"
        )

    with col2:
        st.markdown(
            f'<span style="color: {status_color}; font-weight: 600;">'
            f'{status_text}</span>',
            unsafe_allow_html=True,
        )
        action_col1, action_col2, action_col3 = st.columns(3)

        with action_col1:
            if enabled:
                if st.button("Pause", key=f"pause_{sid}"):
                    client.put(f"/schedules/{sid}/pause")
                    st.rerun()
            else:
                if st.button("Resume", key=f"resume_{sid}"):
                    client.put(f"/schedules/{sid}/resume")
                    st.rerun()

        with action_col2:
            if st.button("Run", key=f"trigger_{sid}"):
                result = client.post(f"/schedules/{sid}/trigger")
                if result and not result.get("error"):
                    st.success(f"Run enqueued (ID: {result.get('run_id', '?')})")
                else:
                    st.error("Failed to trigger run")

        with action_col3:
            if st.button("Delete", key=f"delete_{sid}"):
                client.delete(f"/schedules/{sid}")
                st.rerun()

    st.divider()


def _render_create_form(client: APIClient):
    """Render the create-schedule form."""
    with st.form("create_schedule"):
        name = st.text_input("Schedule Name", placeholder="e.g. Daily GitHub Scan")
        cron = st.text_input("Cron Expression", value="0 0 * * *",
                             help="Standard 5-field cron (minute hour day month weekday)")
        collectors = st.text_input(
            "Collectors (comma-separated)",
            placeholder="github,sec_edgar (leave blank for all)",
        )
        col1, col2 = st.columns(2)
        with col1:
            mode = st.selectbox("Mode", ["full", "collect", "process"])
        with col2:
            dry_run = st.checkbox("Dry Run")

        submitted = st.form_submit_button("Create Schedule")

        if submitted and name and cron:
            payload = {
                "name": name,
                "cron_expression": cron,
                "collectors": [c.strip() for c in collectors.split(",") if c.strip()],
                "mode": mode,
                "dry_run": dry_run,
            }
            result = client.post("/schedules", json=payload)
            if result and not result.get("error"):
                st.success(f"Schedule '{name}' created!")
                st.rerun()
            else:
                detail = result.get("detail", "Unknown error") if result else "No response"
                st.error(f"Failed to create schedule: {detail}")


# =============================================================================
# HISTORY TAB
# =============================================================================

def _render_history_tab(client: APIClient, schedules: List[Dict[str, Any]]):
    """Render the run history tab."""
    if not schedules:
        st.info("No schedules to show history for.")
        return

    # Schedule selector
    schedule_names = {s["id"]: s["name"] for s in schedules}
    selected_id = st.selectbox(
        "Schedule",
        options=list(schedule_names.keys()),
        format_func=lambda x: schedule_names.get(x, f"#{x}"),
    )

    if selected_id is None:
        return

    # Fetch history
    history = client.get(f"/schedules/{selected_id}/history")

    if not history or not isinstance(history, list):
        st.info("No runs recorded yet for this schedule.")
        return

    # Signal bar chart
    chart_data = []
    for run in history:
        chart_data.append({
            "Run": f"#{run.get('id', '?')}",
            "Signals": run.get("signals_found", 0) or 0,
        })

    if chart_data:
        import pandas as pd
        df = pd.DataFrame(chart_data)
        st.bar_chart(df.set_index("Run")["Signals"])

    # History table
    table_data = []
    for run in history:
        table_data.append({
            "ID": run.get("id"),
            "Status": run.get("status", "unknown"),
            "Signals": run.get("signals_found", 0) or 0,
            "Duration": _format_duration(
                run.get("started_at"),
                run.get("finished_at"),
            ),
            "Error": run.get("error_message") or "",
        })

    if table_data:
        import pandas as pd
        st.dataframe(pd.DataFrame(table_data), use_container_width=True)
