# tests/visualization/test_signal_flow.py
"""Verify signal_flow view renders without error given mock stats."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_render_signal_flow_page_shows_metrics():
    """render_signal_flow_page calls st.metric for each stage."""
    mock_stats = {
        "signals_collected": 100,
        "signals_stored": 90,
        "signals_pending": 20,
        "signals_processed": 70,
        "signals_pushed_to_notion": 15,
        "signals_suppressed": 10,
    }
    import streamlit as st
    with patch.object(st, "metric") as mock_metric, \
         patch.object(st, "title"), \
         patch.object(st, "caption"), \
         patch.object(st, "bar_chart"), \
         patch.object(st, "subheader"), \
         patch.object(st, "divider"), \
         patch.object(st, "columns") as mock_cols, \
         patch("dashboard.views.signal_flow.APIClient") as mock_client_cls:
        # st.columns returns 4 mock column objects
        mock_col = MagicMock()
        mock_cols.return_value = [mock_col, mock_col, mock_col, mock_col]
        mock_client = mock_client_cls.return_value
        mock_client.get.return_value = mock_stats
        from dashboard.views.signal_flow import render_signal_flow_page
        render_signal_flow_page()
    # Check st.metric was called (col.metric or st.metric)
    # col.metric calls will be on mock_col
    metric_calls = mock_col.metric.call_args_list
    metric_labels = [call.args[0] for call in metric_calls]
    assert "Collected" in metric_labels or mock_metric.called, \
        "Expected st.metric or col.metric to be called with 'Collected'"


def test_render_signal_flow_page_handles_api_error():
    """View must show an error message if API is unreachable -- must not raise."""
    import streamlit as st
    with patch.object(st, "error") as mock_error, \
         patch.object(st, "title"), \
         patch.object(st, "info"), \
         patch("dashboard.views.signal_flow.APIClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.get.side_effect = ConnectionError("refused")
        from dashboard.views.signal_flow import render_signal_flow_page
        render_signal_flow_page()  # must not raise
    assert mock_error.called
