"""
Tests for Hunter Sandbox dashboard view.

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
    mock_st.tabs.return_value = [_make_ctx_manager() for _ in range(3)]
    mock_st.columns.side_effect = lambda n, **kw: [
        _make_ctx_manager() for _ in range(n if isinstance(n, int) else len(n))
    ]
    mock_st.cache_data = lambda **kwargs: (lambda fn: fn)
    mock_st.spinner.return_value = _make_ctx_manager()
    mock_st.container.return_value = _make_ctx_manager()


def _mock_runs():
    return {
        "data": [
            {
                "id": "run_001abc",
                "status": "completed",
                "query_count": 3,
                "result_count": 10,
                "created_at": "2026-02-09T00:00:00Z",
            }
        ],
        "meta": {"has_more": False},
    }


def _mock_queries():
    return {
        "data": [
            {
                "id": 1,
                "collector": "github",
                "query_text": "health food startup stars:>10",
                "status": "completed",
                "results_count": 5,
                "cost_units_reserved": 1.0,
                "cost_units_final": 1.0,
            },
            {
                "id": 2,
                "collector": "hacker_news",
                "query_text": "Show HN wellness app",
                "status": "completed",
                "results_count": 3,
                "cost_units_reserved": 1.0,
                "cost_units_final": 1.0,
            },
        ],
        "meta": {"has_more": False},
    }


def _mock_results(status="pending"):
    return {
        "data": [
            {
                "id": 101,
                "company_name": "HealthSnacks Inc",
                "canonical_key": "domain:healthsnacks.ai",
                "confidence_score": 0.85,
                "thesis_fit_score": 0.9,
                "status": status,
                "already_known": False,
                "updated_at": "2026-02-09T00:00:00Z",
            },
            {
                "id": 102,
                "company_name": "FitApp Co",
                "canonical_key": "domain:fitapp.io",
                "confidence_score": 0.7,
                "thesis_fit_score": 0.6,
                "status": status,
                "already_known": False,
                "updated_at": "2026-02-09T00:00:00Z",
            },
        ],
        "meta": {"has_more": False, "next_cursor": None},
    }


def _mock_budget():
    return {
        "data": {
            "budget_date": "2026-02-09",
            "global": {
                "cost_units": 15.0,
                "cost_cap": 100.0,
                "circuit_breaker_tripped": False,
            },
            "collectors": {
                "github": {
                    "queries_executed": 5,
                    "queries_cap": 50,
                },
                "hacker_news": {
                    "queries_executed": 2,
                    "queries_cap": 50,
                },
            },
        },
    }


# =============================================================================
# TESTS
# =============================================================================


class TestHunterPageRender:
    def setup_method(self):
        _reset_st()
        mock_st.sidebar = _make_ctx_manager()
        mock_st.sidebar.__enter__ = MagicMock(return_value=mock_st.sidebar)
        mock_st.selectbox.return_value = 0  # first run selected
        mock_st.slider.return_value = 0.0
        mock_st.button.return_value = False

    @patch('dashboard.views.hunter.APIClient')
    def test_renders_title(self, mock_client_cls):
        from dashboard.views.hunter import render_hunter_page

        client = MagicMock()
        client.get.side_effect = lambda path, **kw: {
            "/hunter/runs": _mock_runs(),
            "/hunter/runs/run_001abc/queries": _mock_queries(),
            "/hunter/runs/run_001abc/results": _mock_results(),
            "/hunter/budget": _mock_budget(),
        }.get(path.split("?")[0], {"data": []})
        mock_client_cls.return_value = client

        render_hunter_page()

        mock_st.title.assert_called_once_with("Hunter Sandbox")

    @patch('dashboard.views.hunter.APIClient')
    def test_no_runs_shows_info(self, mock_client_cls):
        from dashboard.views.hunter import render_hunter_page

        client = MagicMock()
        client.get.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_hunter_page()

        mock_st.info.assert_called()

    @patch('dashboard.views.hunter.APIClient')
    def test_api_error_shows_info(self, mock_client_cls):
        from dashboard.views.hunter import render_hunter_page

        client = MagicMock()
        client.get.return_value = {"error": True, "message": "Connection refused"}
        mock_client_cls.return_value = client

        render_hunter_page()

        mock_st.info.assert_called()

    @patch('dashboard.views.hunter.APIClient')
    def test_tabs_rendered(self, mock_client_cls):
        from dashboard.views.hunter import render_hunter_page

        client = MagicMock()
        client.get.side_effect = lambda path, **kw: {
            "/hunter/runs": _mock_runs(),
            "/hunter/budget": _mock_budget(),
        }.get(path.split("?")[0], {"data": [], "meta": {"has_more": False}})
        mock_client_cls.return_value = client

        # First call is selectbox for run (returns index 0)
        # Second call is selectbox for status filter
        mock_st.selectbox.side_effect = [0, ""]

        render_hunter_page()

        mock_st.tabs.assert_called_with(["Queries", "Results", "Budget"])


class TestQueriesTab:
    def setup_method(self):
        _reset_st()
        mock_st.button.return_value = False

    @patch('dashboard.views.hunter.APIClient')
    def test_queries_rendered(self, mock_client_cls):
        from dashboard.views.hunter import _render_queries_tab

        client = MagicMock()
        client.get.return_value = _mock_queries()
        mock_client_cls.return_value = client

        _render_queries_tab(client, "run_001")

        # Should show "2 queries"
        mock_st.markdown.assert_any_call("**2 queries**")

    @patch('dashboard.views.hunter.APIClient')
    def test_empty_queries(self, mock_client_cls):
        from dashboard.views.hunter import _render_queries_tab

        client = MagicMock()
        client.get.return_value = {"data": []}
        mock_client_cls.return_value = client

        _render_queries_tab(client, "run_001")

        mock_st.info.assert_called_with("No queries for this run.")


class TestResultsTab:
    def setup_method(self):
        _reset_st()
        mock_st.button.return_value = False
        mock_st.session_state["hunter_result_cursor"] = None
        mock_st.session_state["hunter_cursor_stack"] = []

    @patch('dashboard.views.hunter.APIClient')
    def test_results_rendered(self, mock_client_cls):
        from dashboard.views.hunter import _render_results_tab

        client = MagicMock()
        client.get.return_value = _mock_results()
        mock_client_cls.return_value = client

        _render_results_tab(client, "run_001", "", None)

        mock_st.markdown.assert_any_call("**2 results** on this page")

    @patch('dashboard.views.hunter.APIClient')
    def test_empty_results(self, mock_client_cls):
        from dashboard.views.hunter import _render_results_tab

        client = MagicMock()
        client.get.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        _render_results_tab(client, "run_001", "", None)

        mock_st.info.assert_called_with("No results matching filters.")


class TestBudgetTab:
    def setup_method(self):
        _reset_st()

    @patch('dashboard.views.hunter.APIClient')
    def test_budget_rendered(self, mock_client_cls):
        from dashboard.views.hunter import _render_budget_tab

        client = MagicMock()
        client.get.return_value = _mock_budget()
        mock_client_cls.return_value = client

        _render_budget_tab(client)

        # Should call st.progress for global + 2 collectors = 3 times
        assert mock_st.progress.call_count >= 1

    @patch('dashboard.views.hunter.APIClient')
    def test_budget_error(self, mock_client_cls):
        from dashboard.views.hunter import _render_budget_tab

        client = MagicMock()
        client.get.return_value = {"error": True, "message": "fail"}
        mock_client_cls.return_value = client

        _render_budget_tab(client)

        mock_st.warning.assert_called_with("Could not load budget data.")

    @patch('dashboard.views.hunter.APIClient')
    def test_circuit_breaker_shown(self, mock_client_cls):
        from dashboard.views.hunter import _render_budget_tab

        budget = _mock_budget()
        budget["data"]["global"]["circuit_breaker_tripped"] = True
        client = MagicMock()
        client.get.return_value = budget
        mock_client_cls.return_value = client

        _render_budget_tab(client)

        mock_st.error.assert_called_with("Circuit breaker TRIPPED — queries paused")


class TestFeedbackActions:
    def setup_method(self):
        _reset_st()

    @patch('dashboard.views.hunter.APIClient')
    def test_pending_shows_feedback_buttons(self, mock_client_cls):
        from dashboard.views.hunter import _render_result_actions

        client = MagicMock()
        # No button clicks
        mock_st.button.return_value = False

        _render_result_actions(client, 101, "pending", "2026-02-09T00:00:00Z")

        # Two buttons rendered: "Relevant" and "Not Rel"
        assert mock_st.button.call_count == 2

    @patch('dashboard.views.hunter.APIClient')
    def test_promoted_status_shows_label(self, mock_client_cls):
        from dashboard.views.hunter import _render_result_actions

        client = MagicMock()
        mock_st.button.return_value = False

        _render_result_actions(client, 101, "promoted", "2026-02-09T00:00:00Z")

        mock_st.caption.assert_called_with("Promoted")
        client.post.assert_not_called()

    @patch('dashboard.views.hunter.APIClient')
    def test_not_relevant_shows_label(self, mock_client_cls):
        from dashboard.views.hunter import _render_result_actions

        client = MagicMock()
        mock_st.button.return_value = False

        _render_result_actions(client, 101, "not_relevant", "2026-02-09T00:00:00Z")

        mock_st.caption.assert_called_with("Not Relevant")

    @patch('dashboard.views.hunter.APIClient')
    def test_relevant_shows_promote_button(self, mock_client_cls):
        from dashboard.views.hunter import _render_result_actions

        client = MagicMock()
        mock_st.button.return_value = False

        _render_result_actions(client, 101, "relevant", "2026-02-09T00:00:00Z")

        # Should render one "Promote" button
        assert mock_st.button.call_count >= 1
