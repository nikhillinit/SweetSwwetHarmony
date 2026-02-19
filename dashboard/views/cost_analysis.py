"""
Cost Analysis Dashboard Page

Cost tracking and forecasting for pipeline operations:
- Overview KPIs (24h cost, period cost, avg per run, total runs)
- Daily cost/run trends (Altair area + bar chart)
- Linear forecasting (7d/30d projections)

All data fetched from /health/ops/metrics and /schedules endpoints.
"""

import streamlit as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dashboard.api_client import APIClient


# =============================================================================
# HELPERS
# =============================================================================

def _format_cost(value: float) -> str:
    """Format a numeric value as a dollar string."""
    if value < 0:
        return f"-${abs(value):.2f}"
    return f"${value:.2f}"


def _compute_linear_forecast(
    history: List[Dict[str, Any]],
    project_days: int = 7,
) -> List[Dict[str, Any]]:
    """
    Simple linear regression forecast — pure Python, no numpy.

    Returns list of {"date": str, "cost": float} dicts for projected days.
    """
    if not history:
        return []

    n = len(history)
    costs = [float(h.get("cost", 0)) for h in history]

    if n == 1:
        # Can't compute slope with one point; flat projection
        last_date = datetime.fromisoformat(history[0]["date"])
        return [
            {
                "date": (last_date + timedelta(days=i + 1)).strftime("%Y-%m-%d"),
                "cost": round(costs[0], 4),
            }
            for i in range(project_days)
        ]

    # Compute slope and intercept via least squares
    x_vals = list(range(n))
    x_mean = sum(x_vals) / n
    y_mean = sum(costs) / n

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, costs))
    denominator = sum((x - x_mean) ** 2 for x in x_vals)

    slope = numerator / denominator if denominator != 0 else 0.0
    intercept = y_mean - slope * x_mean

    # Project forward
    last_date = datetime.fromisoformat(history[-1]["date"])
    forecast = []
    for i in range(1, project_days + 1):
        x = n - 1 + i
        projected_cost = max(0.0, intercept + slope * x)
        forecast.append({
            "date": (last_date + timedelta(days=i)).strftime("%Y-%m-%d"),
            "cost": round(projected_cost, 4),
        })

    return forecast


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def render_cost_analysis_page():
    """Main entry point for the cost analysis dashboard page."""
    st.title("Cost Analysis")
    client = APIClient()

    # Sidebar: period selector
    with st.sidebar:
        period_days = st.selectbox(
            "History Period",
            options=[7, 14, 30, 90],
            format_func=lambda x: f"Last {x} days",
            index=0,
        )

    # Fetch metrics
    metrics = client.get("/health/ops/metrics", params={"history_days": period_days})

    if not metrics or (isinstance(metrics, dict) and metrics.get("error")):
        st.warning("Cost data is not available yet. Run the pipeline at least once to start tracking costs.")
        return

    daily_history = metrics.get("daily_history", [])
    cost_24h = float(metrics.get("api_cost_24h", 0))
    total_runs = int(metrics.get("total_pipeline_runs", 0))

    # Calculate period cost
    period_cost = sum(float(d.get("cost", 0)) for d in daily_history)
    period_runs = sum(int(d.get("runs", 0)) for d in daily_history)
    avg_per_run = period_cost / period_runs if period_runs > 0 else 0.0

    # Tabs
    tab_overview, tab_trends, tab_forecast = st.tabs(
        ["OVERVIEW", "DAILY TRENDS", "FORECASTING"]
    )

    with tab_overview:
        _render_overview(cost_24h, period_cost, avg_per_run, total_runs, period_days, client)

    with tab_trends:
        _render_trends(daily_history)

    with tab_forecast:
        _render_forecast(daily_history)


# =============================================================================
# TAB RENDERERS
# =============================================================================

def _render_overview(
    cost_24h: float,
    period_cost: float,
    avg_per_run: float,
    total_runs: int,
    period_days: int,
    client: APIClient,
):
    """Render overview tab with KPIs and per-schedule breakdown."""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Cost (24h)", _format_cost(cost_24h))
    with col2:
        st.metric(f"Cost ({period_days}d)", _format_cost(period_cost))
    with col3:
        st.metric("Avg / Run", _format_cost(avg_per_run))
    with col4:
        st.metric("Total Runs", total_runs)

    # Per-schedule cost table
    st.divider()
    st.subheader("Per-Schedule Costs")

    schedules = client.get("/schedules")
    if not schedules or not isinstance(schedules, list):
        st.info("No schedules configured.")
        return

    table_data = []
    for sched in schedules:
        sid = sched.get("id")
        name = sched.get("name", "?")
        history = client.get(f"/schedules/{sid}/history")
        if history and isinstance(history, list):
            runs = len(history)
            signals = sum(r.get("signals_found", 0) or 0 for r in history)
        else:
            runs = 0
            signals = 0
        table_data.append({
            "Schedule": name,
            "Runs": runs,
            "Signals": signals,
        })

    if table_data:
        import pandas as pd
        st.dataframe(pd.DataFrame(table_data), use_container_width=True)


def _render_trends(daily_history: List[Dict[str, Any]]):
    """Render daily cost/run trends with Altair chart."""
    if not daily_history:
        st.info("No daily history data available yet.")
        return

    import pandas as pd
    import altair as alt

    df = pd.DataFrame(daily_history)
    df["date"] = pd.to_datetime(df["date"])
    df["cost"] = df["cost"].astype(float)
    df["runs"] = df["runs"].astype(int)

    # Area chart for cost
    cost_chart = (
        alt.Chart(df)
        .mark_area(opacity=0.4, color="#3B82F6")
        .encode(
            x=alt.X("date:T", axis=alt.Axis(title=None, format="%b %d")),
            y=alt.Y("cost:Q", axis=alt.Axis(title="Cost ($)")),
            tooltip=["date:T", "cost:Q"],
        )
    )

    # Bar overlay for runs
    runs_chart = (
        alt.Chart(df)
        .mark_bar(opacity=0.6, color="#10B981", width=12)
        .encode(
            x=alt.X("date:T"),
            y=alt.Y("runs:Q", axis=alt.Axis(title="Runs")),
            tooltip=["date:T", "runs:Q"],
        )
    )

    combined = alt.layer(cost_chart, runs_chart).resolve_scale(
        y="independent"
    ).properties(height=300)

    st.altair_chart(combined, use_container_width=True)


def _render_forecast(daily_history: List[Dict[str, Any]]):
    """Render cost forecasting tab."""
    if not daily_history:
        st.info("Need daily history data to generate forecasts.")
        return

    forecast_7d = _compute_linear_forecast(daily_history, project_days=7)
    forecast_30d = _compute_linear_forecast(daily_history, project_days=30)

    # Projected totals
    projected_7d = sum(f["cost"] for f in forecast_7d)
    projected_30d = sum(f["cost"] for f in forecast_30d)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Projected 7d Cost", _format_cost(projected_7d))
    with col2:
        st.metric("Projected 30d Cost", _format_cost(projected_30d))

    # Trend line chart: historical (solid) + projected (dashed)
    import pandas as pd
    import altair as alt

    hist_df = pd.DataFrame(daily_history)
    hist_df["date"] = pd.to_datetime(hist_df["date"])
    hist_df["cost"] = hist_df["cost"].astype(float)
    hist_df["type"] = "Historical"

    proj_df = pd.DataFrame(forecast_7d)
    proj_df["date"] = pd.to_datetime(proj_df["date"])
    proj_df["type"] = "Projected"

    combined_df = pd.concat([hist_df[["date", "cost", "type"]], proj_df], ignore_index=True)

    chart = (
        alt.Chart(combined_df)
        .mark_line()
        .encode(
            x=alt.X("date:T", axis=alt.Axis(title=None, format="%b %d")),
            y=alt.Y("cost:Q", axis=alt.Axis(title="Cost ($)")),
            strokeDash=alt.StrokeDash(
                "type:N",
                scale=alt.Scale(domain=["Historical", "Projected"], range=[[0], [5, 5]]),
                legend=alt.Legend(title=None),
            ),
            color=alt.Color(
                "type:N",
                scale=alt.Scale(domain=["Historical", "Projected"], range=["#3B82F6", "#F59E0B"]),
                legend=None,
            ),
            tooltip=["date:T", "cost:Q", "type:N"],
        )
        .properties(height=300)
    )

    st.altair_chart(chart, use_container_width=True)
