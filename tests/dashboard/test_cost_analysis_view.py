"""
Tests for cost analysis dashboard view.
"""

import sys
from unittest.mock import MagicMock, patch

# Use existing streamlit mock or create one
if 'streamlit' not in sys.modules or not isinstance(sys.modules['streamlit'], MagicMock):
    sys.modules['streamlit'] = MagicMock()
mock_st = sys.modules['streamlit']
mock_st.session_state = {}

from dashboard.views.cost_analysis import (
    _compute_linear_forecast,
    _format_cost,
    render_cost_analysis_page,
)


class TestFormatCost:
    """Test cost formatting."""

    def test_zero(self):
        assert _format_cost(0) == "$0.00"

    def test_small_value(self):
        assert _format_cost(0.50) == "$0.50"

    def test_larger_value(self):
        assert _format_cost(12.345) == "$12.35"

    def test_negative(self):
        assert _format_cost(-1.5) == "-$1.50"


class TestComputeLinearForecast:
    """Test linear forecast computation."""

    def test_constant_series(self):
        history = [
            {"date": "2026-01-01", "cost": 10.0},
            {"date": "2026-01-02", "cost": 10.0},
            {"date": "2026-01-03", "cost": 10.0},
        ]
        forecast = _compute_linear_forecast(history, project_days=3)
        # Constant series should forecast near 10.0
        for entry in forecast:
            assert 9.0 <= entry["cost"] <= 11.0

    def test_increasing_series(self):
        history = [
            {"date": "2026-01-01", "cost": 1.0},
            {"date": "2026-01-02", "cost": 2.0},
            {"date": "2026-01-03", "cost": 3.0},
        ]
        forecast = _compute_linear_forecast(history, project_days=2)
        assert len(forecast) == 2
        # Should project upward
        assert forecast[0]["cost"] > 3.0

    def test_empty_history(self):
        forecast = _compute_linear_forecast([], project_days=7)
        assert forecast == []

    def test_single_point(self):
        history = [{"date": "2026-01-01", "cost": 5.0}]
        forecast = _compute_linear_forecast(history, project_days=3)
        # With a single point, all forecasts should be that value
        for entry in forecast:
            assert 4.0 <= entry["cost"] <= 6.0


class TestRenderCostAnalysisPage:
    """Test cost analysis page rendering."""

    def setup_method(self):
        mock_st.reset_mock()
        # st.tabs
        tabs = []
        for _ in range(3):
            t = MagicMock()
            t.__enter__ = MagicMock(return_value=None)
            t.__exit__ = MagicMock(return_value=False)
            tabs.append(t)
        mock_st.tabs.return_value = tabs
        # st.columns
        def mock_columns(n):
            cols = []
            for _ in range(n if isinstance(n, int) else len(n)):
                c = MagicMock()
                c.__enter__ = MagicMock(return_value=None)
                c.__exit__ = MagicMock(return_value=False)
                cols.append(c)
            return cols
        mock_st.columns.side_effect = mock_columns
        # st.sidebar context
        mock_st.sidebar.__enter__ = MagicMock(return_value=None)
        mock_st.sidebar.__exit__ = MagicMock(return_value=False)
        mock_st.sidebar.selectbox.return_value = 7

    @patch('dashboard.views.cost_analysis.APIClient')
    def test_render_with_metrics(self, mock_client_cls):
        """Test page renders with valid metrics."""
        client = MagicMock()
        client.get.side_effect = lambda path, **kw: {
            "/health/ops/metrics": {
                "api_cost_24h": "0.50",
                "total_pipeline_runs": 10,
                "daily_history": [
                    {"date": "2026-01-01", "runs": 3, "cost": 0.15},
                    {"date": "2026-01-02", "runs": 5, "cost": 0.25},
                ],
            },
            "/schedules": [],
        }.get(path.split("?")[0], {})
        mock_client_cls.return_value = client

        render_cost_analysis_page()
        mock_st.title.assert_called_once_with("Cost Analysis")

    @patch('dashboard.views.cost_analysis.APIClient')
    def test_render_handles_missing_metrics(self, mock_client_cls):
        """Test page handles missing metrics."""
        client = MagicMock()
        client.get.return_value = {"error": True, "message": "fail"}
        mock_client_cls.return_value = client

        render_cost_analysis_page()
        mock_st.warning.assert_called()

    @patch('dashboard.views.cost_analysis.APIClient')
    def test_render_with_no_history(self, mock_client_cls):
        """Test page handles empty history."""
        client = MagicMock()
        client.get.side_effect = lambda path, **kw: {
            "/health/ops/metrics": {
                "api_cost_24h": "0.00",
                "total_pipeline_runs": 0,
                "daily_history": [],
            },
            "/schedules": [],
        }.get(path.split("?")[0], {})
        mock_client_cls.return_value = client

        render_cost_analysis_page()
        mock_st.title.assert_called_once_with("Cost Analysis")
