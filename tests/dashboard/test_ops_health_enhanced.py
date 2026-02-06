"""
Tests for enhanced ops health dashboard (cost summary + collector breakdown).
"""

import sys
from unittest.mock import MagicMock, patch

# Use existing streamlit mock or create one
if 'streamlit' not in sys.modules or not isinstance(sys.modules['streamlit'], MagicMock):
    sys.modules['streamlit'] = MagicMock()
mock_st = sys.modules['streamlit']
mock_st.session_state = {}

from dashboard.views.ops_health import (
    _render_cost_summary,
    _render_collector_breakdown,
)


class TestRenderCostSummary:
    """Test cost summary KPI rendering."""

    def setup_method(self):
        mock_st.reset_mock()
        def mock_columns(n):
            cols = []
            for _ in range(n if isinstance(n, int) else len(n)):
                c = MagicMock()
                c.__enter__ = MagicMock(return_value=None)
                c.__exit__ = MagicMock(return_value=False)
                cols.append(c)
            return cols
        mock_st.columns.side_effect = mock_columns

    def test_renders_cost_metrics(self):
        """Test cost summary renders 3 KPI metrics."""
        metrics = {
            "api_cost_24h": "1.23",
            "avg_run_duration_sec": 120.5,
            "total_pipeline_runs": 42,
        }
        _render_cost_summary(metrics)
        # Should call st.metric 3 times (Cost 24h, Avg Duration, All-Time Runs)
        assert mock_st.metric.call_count == 3

    def test_handles_missing_fields(self):
        """Test cost summary handles missing metric fields gracefully."""
        metrics = {}
        _render_cost_summary(metrics)
        assert mock_st.metric.call_count == 3


class TestRenderCollectorBreakdown:
    """Test collector cost breakdown chart."""

    def setup_method(self):
        mock_st.reset_mock()

    def test_renders_chart_with_history(self):
        """Test collector breakdown renders a bar chart."""
        metrics = {
            "daily_history": [
                {"date": "2026-01-01", "cost": 0.10, "runs": 2},
                {"date": "2026-01-02", "cost": 0.20, "runs": 3},
            ],
        }
        _render_collector_breakdown(metrics)
        mock_st.subheader.assert_called()
        mock_st.bar_chart.assert_called_once()

    def test_handles_empty_history(self):
        """Test collector breakdown with no daily history."""
        metrics = {"daily_history": []}
        _render_collector_breakdown(metrics)
        mock_st.info.assert_called()
