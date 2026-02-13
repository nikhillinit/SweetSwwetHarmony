"""Tests for drift monitoring dashboard view (W5.8).

Follows pattern from test_triage_views.py: mock streamlit at module level,
patch APIClient for API mocking, mock context managers for st.tabs/st.columns.
"""

import sys
from unittest.mock import MagicMock, patch

# Mock streamlit before importing views
if 'streamlit' not in sys.modules or not isinstance(sys.modules['streamlit'], MagicMock):
    sys.modules['streamlit'] = MagicMock()
mock_st = sys.modules['streamlit']


class MockSessionState(dict):
    """Dict subclass that supports attribute-style access like Streamlit's session_state."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        try:
            del self[name]
        except KeyError:
            raise AttributeError(name)


def _make_ctx_manager():
    """Create a mock context manager for st.tabs/st.columns entries."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=None)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _reset_st():
    """Reset streamlit mock and session state."""
    mock_st.reset_mock()
    mock_st.session_state = MockSessionState()
    # Clear side_effects
    mock_st.selectbox.side_effect = None
    mock_st.text_input.side_effect = None
    mock_st.button.side_effect = None
    # Setup st.tabs to return 4 context managers
    mock_st.tabs.return_value = [_make_ctx_manager() for _ in range(4)]
    # st.columns returns context managers
    mock_st.columns.side_effect = lambda n, **kw: [_make_ctx_manager() for _ in range(n if isinstance(n, int) else len(n))]
    # st.spinner as context manager
    mock_st.spinner.return_value = _make_ctx_manager()
    mock_st.container.return_value = _make_ctx_manager()


# =============================================================================
# CANARY STATUS TAB
# =============================================================================

class TestCanaryTab:
    """Tests for canary status tab rendering."""

    def setup_method(self):
        _reset_st()

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_canary_tab_renders_metrics(self, MockAPI):
        """Should render verdict, pass rate, total runs, open alerts."""
        from dashboard.views.drift_monitoring import render_drift_monitoring_page

        mock_client = MockAPI.return_value
        mock_client.get.side_effect = lambda url, **kw: {
            "/canary/status": {
                "data": {
                    "latest_verdict": "pass",
                    "latest_pass_rate": 0.95,
                    "total_runs": 10,
                    "open_alerts": 2,
                    "latest_run_at": "2026-02-10T12:00:00Z",
                }
            },
            "/canary/runs": {"data": [], "meta": {"has_more": False}},
            "/canary/drift-alerts/stats": {"data": {"open": 2, "acknowledged": 1, "snoozed": 0, "resolved": 5}},
            "/canary/drift-alerts": {"data": [], "meta": {"has_more": False}},
        }.get(url.split("?")[0], {})
        mock_client.post.return_value = {"data": {"metrics": []}}

        render_drift_monitoring_page()

        # Verify tabs created
        mock_st.tabs.assert_called_once()
        # Verify metrics rendered (st.metric called)
        assert mock_st.metric.call_count >= 4

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_canary_tab_empty_status(self, MockAPI):
        """Should handle empty canary status gracefully."""
        from dashboard.views.drift_monitoring import render_drift_monitoring_page

        mock_client = MockAPI.return_value
        mock_client.get.return_value = {"data": {}, "error": {"message": "No data"}}
        mock_client.post.return_value = {"data": {"metrics": []}}

        render_drift_monitoring_page()

        # Should show info messages, not crash
        assert mock_st.tabs.call_count == 1


# =============================================================================
# ALERT TIMELINE TAB
# =============================================================================

class TestAlertsTab:
    """Tests for alert timeline tab rendering."""

    def setup_method(self):
        _reset_st()

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_alerts_tab_renders_stats(self, MockAPI):
        """Should render alert stats: open, acknowledged, snoozed, resolved."""
        from dashboard.views.drift_monitoring import _render_alerts_tab

        mock_client = MockAPI.return_value
        mock_client.get.side_effect = lambda url, **kw: {
            "/canary/drift-alerts/stats": {
                "data": {
                    "open": 5,
                    "acknowledged": 2,
                    "snoozed": 1,
                    "resolved": 10,
                    "mtta_p50_seconds": 120.5,
                }
            },
            "/canary/drift-alerts": {
                "data": [
                    {
                        "id": 1,
                        "alert_type": "pass_rate_drop",
                        "severity": "critical",
                        "status": "open",
                        "message": "Pass rate dropped",
                        "metric_name": "pass_rate",
                        "occurrence_count": 3,
                        "created_at": "2026-02-10T10:00:00Z",
                    },
                ],
                "meta": {"has_more": False},
            },
        }.get(url.split("?")[0], {})

        _render_alerts_tab(mock_client)

        # Should render stats metrics
        metric_calls = [str(c) for c in mock_st.metric.call_args_list]
        assert any("Open" in str(c) for c in mock_st.metric.call_args_list)

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_alerts_tab_empty(self, MockAPI):
        """Should handle no alerts gracefully."""
        from dashboard.views.drift_monitoring import _render_alerts_tab

        mock_client = MockAPI.return_value
        mock_client.get.side_effect = lambda url, **kw: {
            "/canary/drift-alerts/stats": {"data": {"open": 0, "acknowledged": 0, "snoozed": 0, "resolved": 0}},
            "/canary/drift-alerts": {"data": [], "meta": {"has_more": False}},
        }.get(url.split("?")[0], {})

        _render_alerts_tab(mock_client)

        # Should show info about no alerts
        assert mock_st.info.called


# =============================================================================
# RECOMMENDATIONS TAB
# =============================================================================

class TestRecommendationsTab:
    """Tests for recommendations tab rendering."""

    def setup_method(self):
        _reset_st()

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_recommendations_with_archetype_alerts(self, MockAPI):
        """Should recommend golden set expansion for >=3 archetype regressions."""
        from dashboard.views.drift_monitoring import _render_recommendations_tab

        mock_client = MockAPI.return_value
        mock_client.get.return_value = {
            "data": [
                {"id": i, "alert_type": "archetype_regression", "severity": "warning",
                 "status": "open", "message": f"Regression {i}", "metric_name": "pass_rate:cpg"}
                for i in range(4)
            ],
            "meta": {"has_more": False},
        }

        _render_recommendations_tab(mock_client)

        # Should render at least one recommendation card
        md_calls = [str(c) for c in mock_st.markdown.call_args_list]
        assert any("Expand Golden Set" in str(c) for c in md_calls)

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_recommendations_no_open_alerts(self, MockAPI):
        """Should show info message when no open alerts."""
        from dashboard.views.drift_monitoring import _render_recommendations_tab

        mock_client = MockAPI.return_value
        mock_client.get.return_value = {"data": [], "meta": {"has_more": False}}

        _render_recommendations_tab(mock_client)

        assert mock_st.info.called


# =============================================================================
# SPC CHARTS TAB
# =============================================================================

class TestSPCTab:
    """Tests for SPC charts tab rendering."""

    def setup_method(self):
        _reset_st()

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_spc_tab_renders_metrics(self, MockAPI):
        """Should render SPC metrics from check endpoint."""
        from dashboard.views.drift_monitoring import _render_spc_tab

        mock_client = MockAPI.return_value
        mock_client.post.return_value = {
            "data": {
                "metrics": [
                    {
                        "metric": "overall_fp_rate",
                        "verdict": "in_control",
                        "limits": {"mean": 0.25, "ucl": 0.45, "lcl": 0.05},
                        "alerts": [],
                    },
                ],
            },
        }

        _render_spc_tab(mock_client)

        # Should render metric header
        md_calls = [str(c) for c in mock_st.markdown.call_args_list]
        assert any("Overall Fp Rate" in str(c) for c in md_calls)
        # Should render mean/ucl/lcl
        assert mock_st.metric.call_count >= 3

    @patch("dashboard.views.drift_monitoring.APIClient")
    def test_spc_tab_unavailable(self, MockAPI):
        """Should handle SPC unavailable gracefully."""
        from dashboard.views.drift_monitoring import _render_spc_tab

        mock_client = MockAPI.return_value
        mock_client.post.return_value = {"error": {"message": "Feature disabled"}}

        _render_spc_tab(mock_client)

        assert mock_st.info.called


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
