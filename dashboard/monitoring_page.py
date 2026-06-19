"""
Monitoring Page - Website change tracking dashboard

Shows:
- Active watches with status
- Recent diffs with severity scoring
- Unacknowledged alerts with acknowledge buttons
- Monitoring run history
"""

import asyncio
import streamlit as st
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import logging

from utils.db_path_helper import resolve_db_path_env

logger = logging.getLogger(__name__)


def run_async(coro):
    """Helper to run async code in Streamlit."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def inject_monitoring_css():
    """Inject custom CSS for monitoring page (light theme, consistent with app)."""
    st.markdown("""
    <style>
    /* Card styling — light theme consistent with main app */
    .monitor-card {
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.75rem;
        background: #FFFFFF;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
    }

    /* Alert card with severity color */
    .alert-card {
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.75rem;
        background: #FFFFFF;
    }

    .alert-high {
        border-left: 3px solid #EF4444;
    }

    .alert-medium {
        border-left: 3px solid #F59E0B;
    }

    .alert-low {
        border-left: 3px solid #10B981;
    }

    /* Watch status */
    .watch-active {
        color: #10B981;
    }

    .watch-inactive {
        color: #6B7280;
    }

    .watch-failing {
        color: #EF4444;
    }

    /* Severity badge */
    .severity-badge {
        display: inline-block;
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    .severity-high {
        background: #FEE2E2;
        color: #991B1B;
    }

    .severity-medium {
        background: #FEF3C7;
        color: #92400E;
    }

    .severity-low {
        background: #D1FAE5;
        color: #065F46;
    }

    /* Label styling */
    .label {
        font-size: 0.7rem;
        font-weight: 600;
        color: #6B7280;
        text-transform: uppercase;
        letter-spacing: 0.1em;
    }

    /* Metric value */
    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        color: #1F2937;
    }

    .metric-label {
        font-size: 0.8rem;
        color: #6B7280;
    }
    </style>
    """, unsafe_allow_html=True)


def severity_class(score: float) -> str:
    """Get CSS class for severity score."""
    if score >= 0.8:
        return "severity-high"
    elif score >= 0.4:
        return "severity-medium"
    return "severity-low"


def format_time_ago(dt: Optional[datetime]) -> str:
    """Format datetime as relative time."""
    if not dt:
        return "never"

    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f"{mins}m ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    else:
        days = int(seconds / 86400)
        return f"{days}d ago"


def render_monitoring_page(db_path: Optional[str] = None):
    """Render the monitoring dashboard page."""
    db_path = resolve_db_path_env(db_path)
    inject_monitoring_css()

    st.title("Website Monitoring")

    # Initialize stores
    from storage.signal_store import SignalStore
    from monitoring.monitor_store import MonitorStore

    store = run_async(_init_store(db_path))
    if not store:
        st.error("Failed to initialize database")
        return

    monitor_store = MonitorStore(store)

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs([
        "Overview",
        "Active Watches",
        "Recent Changes",
        "Alerts"
    ])

    with tab1:
        render_overview(monitor_store)

    with tab2:
        render_watches(monitor_store)

    with tab3:
        render_diffs(monitor_store)

    with tab4:
        render_alerts(monitor_store)


async def _init_store(db_path: str):
    """Initialize signal store."""
    try:
        from storage.signal_store import SignalStore
        store = SignalStore(db_path)
        await store.initialize()
        return store
    except Exception as e:
        logger.error(f"Failed to init store: {e}")
        return None


def render_overview(monitor_store):
    """Render overview metrics."""
    st.subheader("Monitoring Overview")

    # Get metrics
    try:
        due_watches = run_async(monitor_store.get_due_watches(limit=1000))
        unacked_alerts = run_async(monitor_store.get_unacked_alerts(limit=100))
        recent_runs = run_async(monitor_store.get_recent_runs(limit=5))
        config = run_async(monitor_store.get_config())
    except Exception as e:
        st.error(f"Error loading metrics: {e}")
        return

    # Metric cards
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(f"""
        <div class="monitor-card">
            <div class="metric-value">{len(due_watches)}</div>
            <div class="metric-label">Watches Due</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="monitor-card">
            <div class="metric-value">{len(unacked_alerts)}</div>
            <div class="metric-label">Pending Alerts</div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        last_run = recent_runs[0] if recent_runs else None
        last_run_time = format_time_ago(
            datetime.fromisoformat(last_run['started_at']) if last_run else None
        )
        st.markdown(f"""
        <div class="monitor-card">
            <div class="metric-value" style="font-size: 1.5rem;">{last_run_time}</div>
            <div class="metric-label">Last Run</div>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        high_sev = sum(1 for r in recent_runs if r.get('high_severity_events', 0) > 0)
        st.markdown(f"""
        <div class="monitor-card">
            <div class="metric-value">{high_sev}</div>
            <div class="metric-label">High Severity (5 runs)</div>
        </div>
        """, unsafe_allow_html=True)

    # Recent runs table
    if recent_runs:
        st.subheader("Recent Monitoring Runs")

        for run in recent_runs[:5]:
            duration = run.get('duration_seconds', 0) or 0
            high_sev = run.get('high_severity_events', 0)

            status_color = "#EF4444" if high_sev > 0 else "#10B981"

            st.markdown(f"""
            <div class="monitor-card" style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <span style="color: #6B7280;">{run['started_at'][:16]}</span>
                </div>
                <div>
                    <span style="color: #4B5563;">
                        {run['watches_checked']} checked,
                        {run['snapshots_taken']} snapshots
                    </span>
                </div>
                <div>
                    <span style="color: {status_color};">
                        {high_sev} high severity
                    </span>
                </div>
                <div>
                    <span style="color: #6B7280;">{duration:.1f}s</span>
                </div>
            </div>
            """, unsafe_allow_html=True)


def render_watches(monitor_store):
    """Render active watches list."""
    st.subheader("Active Watches")

    try:
        watches = run_async(monitor_store.get_due_watches(limit=100))
    except Exception as e:
        st.error(f"Error loading watches: {e}")
        return

    if not watches:
        st.info("No active watches. Use `python run_pipeline.py monitor add <url>` to create one.")
        return

    for watch in watches:
        last_check = format_time_ago(watch.last_checked_at)
        failures = watch.consecutive_failures

        if failures > 0:
            status_class = "watch-failing"
            status_text = f"{failures} failures"
        elif watch.last_checked_at:
            status_class = "watch-active"
            status_text = f"checked {last_check}"
        else:
            status_class = "watch-inactive"
            status_text = "never checked"

        st.markdown(f"""
        <div class="monitor-card">
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <div style="font-weight: 600; color: #1F2937;">{watch.canonical_key}</div>
                    <div style="font-size: 0.85rem; color: #6B7280;">{watch.url}</div>
                </div>
                <div style="text-align: right;">
                    <div class="{status_class}">{status_text}</div>
                    <div style="font-size: 0.75rem; color: #6B7280;">
                        {watch.watch_type} | {watch.interval_seconds // 3600}h interval
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)


def render_diffs(monitor_store):
    """Render recent diffs."""
    st.subheader("Recent Changes")

    min_severity = st.slider("Minimum Severity", 0.0, 1.0, 0.2, 0.1)

    try:
        diffs = run_async(monitor_store.get_recent_diffs(limit=50, min_severity=min_severity))
    except Exception as e:
        st.error(f"Error loading diffs: {e}")
        return

    if not diffs:
        st.info("No recent changes detected.")
        return

    for diff in diffs:
        sev_class = severity_class(diff.severity_score)
        time_ago = format_time_ago(diff.created_at)

        # Build change summary
        changes = []
        if diff.has_redirect:
            changes.append("redirect")
        if diff.has_state_change:
            changes.append("state change")
        if diff.has_text_change:
            changes.append("text change")

        changes_text = ", ".join(changes) if changes else "unknown"

        st.markdown(f"""
        <div class="alert-card alert-{'high' if diff.severity_score >= 0.8 else 'medium' if diff.severity_score >= 0.4 else 'low'}">
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <div style="font-weight: 600; color: #1F2937;">Watch #{diff.watch_id}</div>
                    <div style="font-size: 0.85rem; color: #4B5563;">{changes_text}</div>
                </div>
                <div style="text-align: right;">
                    <span class="severity-badge {sev_class}">{diff.severity_score:.2f}</span>
                    <div style="font-size: 0.75rem; color: #6B7280; margin-top: 0.25rem;">{time_ago}</div>
                </div>
            </div>
            {_render_diff_summary(diff)}
        </div>
        """, unsafe_allow_html=True)


def _render_diff_summary(diff) -> str:
    """Render diff summary as HTML."""
    if not diff.diff_summary:
        return ""

    summary = diff.diff_summary
    parts = []

    if summary.get('length_change'):
        change = summary['length_change']
        if change > 0:
            parts.append(f"+{change} chars")
        else:
            parts.append(f"{change} chars")

    if summary.get('semantic_drift') is not None:
        parts.append(f"drift: {summary['semantic_drift']:.2f}")

    if summary.get('old_state') and summary.get('new_state'):
        if summary['old_state'] != summary['new_state']:
            parts.append(f"{summary['old_state']} → {summary['new_state']}")

    if not parts:
        return ""

    return f"""
    <div style="margin-top: 0.5rem; font-size: 0.8rem; color: #6B7280;">
        {' | '.join(parts)}
    </div>
    """


def render_alerts(monitor_store):
    """Render unacknowledged alerts."""
    st.subheader("Pending Alerts")

    try:
        alerts = run_async(monitor_store.get_unacked_alerts(limit=50))
    except Exception as e:
        st.error(f"Error loading alerts: {e}")
        return

    if not alerts:
        st.success("No pending alerts.")
        return

    for alert in alerts:
        sev_class = severity_class(alert.severity_score)
        time_ago = format_time_ago(alert.created_at)

        col1, col2 = st.columns([4, 1])

        with col1:
            st.markdown(f"""
            <div class="alert-card alert-{'high' if alert.severity_score >= 0.8 else 'medium' if alert.severity_score >= 0.4 else 'low'}">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div>
                        <div style="font-weight: 600; color: #1F2937;">
                            {alert.alert_reason.replace('_', ' ').title()}
                        </div>
                        <div style="font-size: 0.85rem; color: #4B5563;">
                            Watch #{alert.watch_id}
                            {f' | Diff #{alert.diff_id}' if alert.diff_id else ''}
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <span class="severity-badge {sev_class}">{alert.severity_score:.2f}</span>
                        <div style="font-size: 0.75rem; color: #6B7280; margin-top: 0.25rem;">{time_ago}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            if st.button("Acknowledge", key=f"ack_{alert.id}"):
                try:
                    run_async(monitor_store.acknowledge_alert(alert.id, "dashboard_user"))
                    st.success(f"Alert #{alert.id} acknowledged")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to acknowledge: {e}")


# Entry point for standalone testing
if __name__ == "__main__":
    render_monitoring_page()
