"""
Tests for scheduler dashboard view.
"""

import sys
from unittest.mock import MagicMock, patch

# Use existing streamlit mock or create one
if 'streamlit' not in sys.modules or not isinstance(sys.modules['streamlit'], MagicMock):
    sys.modules['streamlit'] = MagicMock()
mock_st = sys.modules['streamlit']
mock_st.session_state = {}

from dashboard.views.scheduler import (
    _format_cron_human,
    _format_duration,
    render_scheduler_page,
)


class TestFormatCronHuman:
    """Test cron expression to human-readable conversion."""

    def test_every_minute(self):
        assert _format_cron_human("* * * * *") == "Every minute"

    def test_every_hour(self):
        assert _format_cron_human("0 * * * *") == "Every hour"

    def test_every_day(self):
        assert _format_cron_human("0 0 * * *") == "Daily at midnight"

    def test_every_weekday(self):
        assert _format_cron_human("0 9 * * 1-5") == "Weekdays at 09:00"

    def test_custom_cron(self):
        result = _format_cron_human("30 14 * * 3")
        assert "30 14 * * 3" in result


class TestFormatDuration:
    """Test duration formatting from ISO timestamps."""

    def test_short_duration(self):
        result = _format_duration(
            "2026-01-01T10:00:00Z",
            "2026-01-01T10:02:30Z",
        )
        assert result == "2m 30s"

    def test_longer_duration(self):
        result = _format_duration(
            "2026-01-01T10:00:00Z",
            "2026-01-01T11:05:00Z",
        )
        assert result == "1h 5m"

    def test_zero_duration(self):
        result = _format_duration(
            "2026-01-01T10:00:00Z",
            "2026-01-01T10:00:00Z",
        )
        assert result == "0s"

    def test_none_finished(self):
        result = _format_duration("2026-01-01T10:00:00Z", None)
        assert result == "-"


class TestRenderSchedulerPage:
    """Test scheduler page rendering."""

    def setup_method(self):
        mock_st.reset_mock()
        # st.tabs returns context managers
        tab1, tab2 = MagicMock(), MagicMock()
        tab1.__enter__ = MagicMock(return_value=None)
        tab1.__exit__ = MagicMock(return_value=False)
        tab2.__enter__ = MagicMock(return_value=None)
        tab2.__exit__ = MagicMock(return_value=False)
        mock_st.tabs.return_value = [tab1, tab2]
        # st.columns returns context managers
        def mock_columns(n):
            cols = []
            for _ in range(n if isinstance(n, int) else len(n)):
                c = MagicMock()
                c.__enter__ = MagicMock(return_value=None)
                c.__exit__ = MagicMock(return_value=False)
                cols.append(c)
            return cols
        mock_st.columns.side_effect = mock_columns
        # st.expander returns context manager
        exp = MagicMock()
        exp.__enter__ = MagicMock(return_value=None)
        exp.__exit__ = MagicMock(return_value=False)
        mock_st.expander.return_value = exp
        # st.form returns context manager
        form = MagicMock()
        form.__enter__ = MagicMock(return_value=None)
        form.__exit__ = MagicMock(return_value=False)
        mock_st.form.return_value = form

    @patch('dashboard.views.scheduler.APIClient')
    def test_render_with_no_schedules(self, mock_client_cls):
        """Test page renders with empty schedule list."""
        client = MagicMock()
        client.get.return_value = []
        mock_client_cls.return_value = client

        render_scheduler_page()
        mock_st.title.assert_called_once_with("Pipeline Schedules")

    @patch('dashboard.views.scheduler.APIClient')
    def test_render_with_schedules(self, mock_client_cls):
        """Test page renders schedule cards."""
        client = MagicMock()
        client.get.side_effect = lambda path, **kw: {
            "/schedules": [
                {"id": 1, "name": "daily", "cron_expression": "0 0 * * *",
                 "collectors": "github,sec_edgar", "enabled": True,
                 "mode": "full", "dry_run": False},
            ],
        }.get(path, [])
        mock_client_cls.return_value = client

        render_scheduler_page()
        mock_st.title.assert_called_once_with("Pipeline Schedules")

    @patch('dashboard.views.scheduler.APIClient')
    def test_render_handles_api_error(self, mock_client_cls):
        """Test page handles API error gracefully."""
        client = MagicMock()
        client.get.return_value = {"error": True, "message": "fail"}
        mock_client_cls.return_value = client

        render_scheduler_page()
        mock_st.warning.assert_called()

    @patch('dashboard.views.scheduler.APIClient')
    def test_kpi_metrics_shown(self, mock_client_cls):
        """Test KPI metrics are rendered."""
        client = MagicMock()
        client.get.return_value = [
            {"id": 1, "name": "a", "cron_expression": "0 * * * *",
             "collectors": "", "enabled": True, "mode": "full", "dry_run": False},
            {"id": 2, "name": "b", "cron_expression": "0 0 * * *",
             "collectors": "", "enabled": False, "mode": "full", "dry_run": False},
        ]
        mock_client_cls.return_value = client

        render_scheduler_page()
        # st.metric should be called for Total, Active, Paused
        metric_calls = [c for c in mock_st.metric.call_args_list]
        assert len(metric_calls) >= 3

    @patch('dashboard.views.scheduler.APIClient')
    def test_history_tab_fetches_runs(self, mock_client_cls):
        """Test history tab calls history endpoint."""
        client = MagicMock()
        schedules = [
            {"id": 1, "name": "daily", "cron_expression": "0 0 * * *",
             "collectors": "", "enabled": True, "mode": "full", "dry_run": False},
        ]
        client.get.side_effect = lambda path, **kw: {
            "/schedules": schedules,
            "/schedules/1/history": [
                {"id": 1, "schedule_id": 1, "status": "completed",
                 "started_at": "2026-01-01T10:00:00Z",
                 "finished_at": "2026-01-01T10:05:00Z",
                 "signals_found": 5, "error_message": None},
            ],
        }.get(path, [])
        mock_client_cls.return_value = client

        render_scheduler_page()
