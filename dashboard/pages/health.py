"""
Health Dashboard Page

Comprehensive system health monitoring:
- Overall system status
- Collector health and last run times
- Database statistics
- Relationship data staleness
- Background jobs management

All data is fetched from the FastAPI backend.
"""

import streamlit as st
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dashboard.api_client import APIClient


# =============================================================================
# CONFIGURATION
# =============================================================================

STATUS_COLORS = {
    "healthy": "#10B981",     # Green
    "degraded": "#F59E0B",    # Amber
    "unhealthy": "#EF4444",   # Red
    "unknown": "#6B7280",     # Gray
}

STATUS_ICONS = {
    "healthy": "checkmark",
    "degraded": "warning",
    "unhealthy": "x-circle",
    "unknown": "question",
}

JOB_STATUS_COLORS = {
    "pending": "#6B7280",
    "running": "#3B82F6",
    "completed": "#10B981",
    "failed": "#EF4444",
    "cancelled": "#F59E0B",
}


# =============================================================================
# HELPERS
# =============================================================================

def format_relative_time(dt_str: Optional[str]) -> str:
    """Format datetime as relative time (e.g., '2h ago')."""
    if not dt_str:
        return "Never"

    try:
        # Handle ISO format with timezone
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt

        if delta.days > 30:
            return dt.strftime("%b %d, %Y")
        elif delta.days > 0:
            return f"{delta.days}d ago"
        elif delta.seconds > 3600:
            hours = delta.seconds // 3600
            return f"{hours}h ago"
        elif delta.seconds > 60:
            minutes = delta.seconds // 60
            return f"{minutes}m ago"
        else:
            return "Just now"
    except (ValueError, TypeError):
        return str(dt_str)[:10] if dt_str else "Unknown"


def status_badge(status: str, text: Optional[str] = None) -> str:
    """Generate HTML for a status badge."""
    color = STATUS_COLORS.get(status.lower(), STATUS_COLORS["unknown"])
    display = text or status.title()
    return f"""
    <span style="
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 12px;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 500;
        background: {color}20;
        color: {color};
    ">
        <span style="width: 6px; height: 6px; border-radius: 50%; background: {color};"></span>
        {display}
    </span>
    """


# =============================================================================
# PAGE COMPONENTS
# =============================================================================

def render_system_overview(health: Dict[str, Any]):
    """Render the system overview section."""
    st.markdown("## System Status")

    status = health.get("status", "unknown")
    color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(f"""
        <div style="
            background: {color}10;
            border: 1px solid {color}40;
            border-radius: 12px;
            padding: 1.5rem;
            text-align: center;
        ">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">
                {"checkmark" if status == "healthy" else "warning" if status == "degraded" else "x"}
            </div>
            <div style="font-family: 'Inter', sans-serif; font-weight: 700; font-size: 1.25rem; color: {color};">
                {status.upper()}
            </div>
            <div style="font-size: 0.75rem; color: #6B7280; margin-top: 0.25rem;">
                Overall Status
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        version = health.get("version", "1.0.0")
        env = health.get("environment", "development")
        st.metric("Version", version)
        st.caption(f"Environment: {env}")

    with col3:
        components = health.get("components", [])
        healthy_count = sum(1 for c in components if c.get("status") == "healthy")
        total = len(components)
        st.metric("Components", f"{healthy_count}/{total}")
        st.caption("Healthy / Total")

    with col4:
        alerts = health.get("alerts", [])
        alert_count = len(alerts)
        if alert_count > 0:
            st.metric("Active Alerts", alert_count, delta=-alert_count, delta_color="inverse")
        else:
            st.metric("Active Alerts", 0)
            st.caption("All clear")


def render_components_table(components: List[Dict[str, Any]]):
    """Render the components health table."""
    st.markdown("### Components")

    if not components:
        st.info("No component data available")
        return

    # Build table rows
    for comp in components:
        name = comp.get("name", "Unknown")
        status = comp.get("status", "unknown")
        message = comp.get("message", "")
        color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])

        col1, col2, col3 = st.columns([2, 1, 3])

        with col1:
            # Format name nicely
            display_name = name.replace("_", " ").replace("api ", "").title()
            st.markdown(f"**{display_name}**")

        with col2:
            st.markdown(status_badge(status), unsafe_allow_html=True)

        with col3:
            st.caption(message)

        st.divider()


def render_collectors_section(client: APIClient):
    """Render collector health section with Run Now buttons."""
    st.markdown("### Collectors")

    collectors = client.get_collectors()

    if not collectors:
        st.warning("Could not fetch collector status")
        return

    # Group by status
    healthy = [c for c in collectors if c.get("status") == "healthy"]
    degraded = [c for c in collectors if c.get("status") == "degraded"]
    unknown = [c for c in collectors if c.get("status") == "unknown"]

    # Summary metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Healthy", len(healthy))
    with col2:
        st.metric("Degraded", len(degraded))
    with col3:
        st.metric("Never Run", len(unknown))

    st.markdown("---")

    # Collector table with actions
    for collector in collectors:
        name = collector.get("name", "unknown")
        status = collector.get("status", "unknown")
        last_run = collector.get("last_run")
        signals = collector.get("signals_last_run", 0)
        message = collector.get("message", "")
        color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])

        col1, col2, col3, col4 = st.columns([2, 1.5, 2, 1.5])

        with col1:
            display_name = name.replace("_", " ").title()
            st.markdown(f"**{display_name}**")
            st.caption(f"{signals} signals" if signals else "No data")

        with col2:
            st.markdown(status_badge(status), unsafe_allow_html=True)

        with col3:
            if last_run:
                st.caption(f"Last run: {format_relative_time(last_run)}")
            else:
                st.caption("Never run")

        with col4:
            if st.button("Run Now", key=f"run_{name}", use_container_width=True):
                with st.spinner(f"Starting {name}..."):
                    result = client.start_collect_job(collector=name)
                    if result and not result.get("error"):
                        st.success(f"Started job: {result.get('id', 'unknown')[:8]}")
                        st.rerun()
                    else:
                        st.error(result.get("message", "Failed to start job"))

        st.divider()


def render_database_section(client: APIClient):
    """Render database health section."""
    st.markdown("### Database")

    db_health = client.get_database_health()

    if not db_health or db_health.get("error"):
        st.warning("Could not fetch database status")
        return

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Signals", db_health.get("total_signals", 0))

    with col2:
        st.metric("Companies", db_health.get("total_companies", 0))

    with col3:
        st.metric("Pending", db_health.get("pending_signals", 0))

    with col4:
        size_mb = db_health.get("database_size_mb", 0)
        st.metric("DB Size", f"{size_mb:.1f} MB")

    # Additional details
    schema = db_health.get("schema_version", 0)
    wal_mb = db_health.get("wal_size_mb", 0)
    st.caption(f"Schema version: {schema} | WAL: {wal_mb:.1f} MB")


def render_relationships_section(client: APIClient):
    """Render relationship health section."""
    st.markdown("### Relationships")

    rel_health = client.get_relationship_health()

    if not rel_health or rel_health.get("status") == "unavailable":
        st.info("Relationship monitoring not configured")
        return

    if rel_health.get("status") == "error":
        st.warning(f"Error: {rel_health.get('message', 'Unknown error')}")
        return

    col1, col2 = st.columns(2)

    with col1:
        email_status = rel_health.get("email_scan_status", "unknown")
        email_staleness = rel_health.get("email_staleness_days", 0)
        email_last = rel_health.get("email_last_scan")

        st.markdown("**Email Scan**")
        st.markdown(status_badge(
            "healthy" if email_status == "fresh" else "degraded",
            f"{email_staleness}d old" if email_staleness else "Fresh"
        ), unsafe_allow_html=True)
        if email_last:
            st.caption(f"Last scan: {format_relative_time(email_last)}")

    with col2:
        lp_status = rel_health.get("lp_sync_status", "unknown")
        lp_staleness = rel_health.get("lp_staleness_days", 0)
        lp_last = rel_health.get("lp_last_sync")

        st.markdown("**LP Sync**")
        st.markdown(status_badge(
            "healthy" if lp_status == "fresh" else "degraded",
            f"{lp_staleness}d old" if lp_staleness else "Fresh"
        ), unsafe_allow_html=True)
        if lp_last:
            st.caption(f"Last sync: {format_relative_time(lp_last)}")

    # Summary
    total = rel_health.get("total_relationships", 0)
    paths = rel_health.get("warm_intro_paths", 0)
    st.caption(f"Total relationships: {total} | Warm intro paths: {paths}")


def render_jobs_section(client: APIClient):
    """Render jobs list with management actions."""
    st.markdown("### Background Jobs")

    # Quick actions
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("Process Signals", use_container_width=True):
            with st.spinner("Starting..."):
                result = client.start_process_job()
                if result and not result.get("error"):
                    st.success(f"Started: {result.get('id', '')[:8]}")
                    st.rerun()
                else:
                    st.error(result.get("message", "Failed"))

    with col2:
        if st.button("Sync Notion", use_container_width=True):
            with st.spinner("Starting..."):
                result = client.start_sync_job()
                if result and not result.get("error"):
                    st.success(f"Started: {result.get('id', '')[:8]}")
                    st.rerun()
                else:
                    st.error(result.get("message", "Failed"))

    with col3:
        if st.button("Refresh", key="refresh_jobs", use_container_width=True):
            st.rerun()

    st.markdown("---")

    # Jobs list
    jobs_data = client.list_jobs(limit=20)

    if not jobs_data or jobs_data.get("error"):
        st.warning("Could not fetch jobs")
        return

    jobs = jobs_data.get("jobs", [])

    if not jobs:
        st.info("No recent jobs")
        return

    # Render jobs table
    for job in jobs:
        job_id = job.get("id", "")
        job_type = job.get("job_type", "unknown")
        status = job.get("status", "unknown")
        progress = job.get("progress_pct", 0)
        progress_msg = job.get("progress_message", "")
        error_msg = job.get("error_message", "")
        started = job.get("started_at")
        completed = job.get("completed_at")
        created_by = job.get("created_by", "system")

        status_color = JOB_STATUS_COLORS.get(status, "#6B7280")

        col1, col2, col3, col4 = st.columns([2, 1.5, 2.5, 1])

        with col1:
            # Job type and ID
            st.markdown(f"**{job_type.title()}**")
            st.caption(f"ID: {job_id[:8]}... | By: {created_by}")

        with col2:
            # Status badge
            st.markdown(f"""
            <span style="
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 4px 12px;
                border-radius: 9999px;
                font-size: 0.75rem;
                font-weight: 500;
                background: {status_color}20;
                color: {status_color};
            ">
                {status.upper()}
            </span>
            """, unsafe_allow_html=True)

        with col3:
            # Progress/Message
            if status == "running":
                st.progress(progress / 100)
                st.caption(progress_msg or f"{progress}%")
            elif status == "failed":
                st.caption(f"Error: {error_msg[:50]}..." if len(error_msg or "") > 50 else error_msg)
            elif status == "completed":
                st.caption(f"Completed: {format_relative_time(completed)}")
            else:
                st.caption(f"Created: {format_relative_time(started)}")

        with col4:
            # Actions
            if status == "running":
                if st.button("Cancel", key=f"cancel_{job_id}", use_container_width=True):
                    result = client.cancel_job(job_id)
                    if result and result.get("success"):
                        st.success("Cancelled")
                        st.rerun()
            elif status == "failed":
                if st.button("Retry", key=f"retry_{job_id}", use_container_width=True):
                    # Retry by starting a new job of the same type
                    if job_type == "collect":
                        # Need to get params to know which collector
                        job_detail = client.get_job(job_id)
                        if job_detail:
                            params = job_detail.get("params", {})
                            collector = params.get("collector", "github")
                            client.start_collect_job(collector=collector)
                    elif job_type == "process":
                        client.start_process_job()
                    elif job_type == "sync":
                        client.start_sync_job()
                    st.rerun()

        # Expandable logs
        with st.expander("View Logs", expanded=False):
            logs = client.get_job_logs(job_id, limit=20)
            if logs:
                for log in logs:
                    level = log.get("level", "INFO")
                    message = log.get("message", "")
                    logged_at = log.get("logged_at", "")

                    level_color = {
                        "ERROR": "#EF4444",
                        "WARNING": "#F59E0B",
                        "INFO": "#3B82F6",
                        "DEBUG": "#6B7280",
                    }.get(level, "#6B7280")

                    st.markdown(f"""
                    <div style="font-family: monospace; font-size: 0.8rem; margin-bottom: 4px;">
                        <span style="color: {level_color};">[{level}]</span>
                        <span style="color: #9CA3AF;">{format_relative_time(logged_at)}</span>
                        <span style="color: #374151;">{message}</span>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.caption("No logs available")

        st.divider()


def render_alerts_section(alerts: List[Dict[str, Any]]):
    """Render active alerts section."""
    if not alerts:
        return

    st.markdown("### Active Alerts")

    for alert in alerts:
        alert_type = alert.get("type", "unknown")
        severity = alert.get("severity", "medium")
        message = alert.get("message", "")

        severity_colors = {
            "high": "#EF4444",
            "medium": "#F59E0B",
            "low": "#3B82F6",
        }
        color = severity_colors.get(severity, "#6B7280")

        st.markdown(f"""
        <div style="
            background: {color}10;
            border-left: 3px solid {color};
            padding: 12px 16px;
            border-radius: 0 8px 8px 0;
            margin-bottom: 8px;
        ">
            <div style="font-weight: 600; color: {color}; font-size: 0.85rem; text-transform: uppercase;">
                {alert_type.replace('_', ' ')}
            </div>
            <div style="color: #374151; margin-top: 4px;">
                {message}
            </div>
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# MAIN PAGE
# =============================================================================

def render_health_page():
    """Main entry point for health dashboard page."""
    st.markdown("# System Health")
    st.caption("Monitor system components, collectors, and background jobs")

    client = APIClient()

    # Fetch detailed health
    health = client.get_health_detailed()

    if not health:
        st.error("Cannot connect to API server. Make sure it's running.")
        return

    if health.get("error"):
        st.error(f"Error fetching health: {health.get('message', 'Unknown error')}")
        return

    # Render sections
    render_system_overview(health)

    # Alerts (if any)
    alerts = health.get("alerts", [])
    render_alerts_section(alerts)

    st.markdown("---")

    # Tabs for different sections
    tab1, tab2, tab3, tab4 = st.tabs(["Collectors", "Database", "Relationships", "Jobs"])

    with tab1:
        render_collectors_section(client)

    with tab2:
        render_database_section(client)
        st.markdown("---")
        render_components_table(health.get("components", []))

    with tab3:
        render_relationships_section(client)

    with tab4:
        render_jobs_section(client)
