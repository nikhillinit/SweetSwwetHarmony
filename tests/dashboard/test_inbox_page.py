"""
Tests for inbox page: API health, tab rendering, error isolation, action refresh, empty state.

Follows pattern from test_triage_views.py: mock streamlit at module level,
patch at import site, mock context managers for st.tabs/st.columns.
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


def _cache_data(fn=None, **kwargs):
    """Streamlit cache_data mock: works as both @cache_data and @cache_data(ttl=30)."""
    if callable(fn):
        return fn
    return lambda inner: inner


def _reset_st():
    """Reset streamlit mock and session state."""
    mock_st.reset_mock()
    mock_st.session_state = MockSessionState()
    # Clear side_effects
    mock_st.button.side_effect = None
    mock_st.selectbox.side_effect = None
    mock_st.slider.side_effect = None
    # Widget defaults
    mock_st.button.return_value = False
    mock_st.tabs.return_value = [_make_ctx_manager() for _ in range(4)]
    mock_st.columns.side_effect = lambda n, **kw: [
        _make_ctx_manager() for _ in range(n if isinstance(n, int) else len(n))
    ]

    # Sidebar
    mock_st.sidebar = MagicMock()
    mock_st.sidebar.slider.return_value = 0
    mock_st.sidebar.selectbox.return_value = 25
    mock_st.sidebar.button.return_value = False
    mock_st.sidebar.success = MagicMock()
    mock_st.sidebar.error = MagicMock()
    mock_st.sidebar.markdown = MagicMock()

    # cache_data: callable decorator factory + .clear()
    _cache_mock = MagicMock(side_effect=_cache_data)
    _cache_mock.clear = MagicMock()
    mock_st.cache_data = _cache_mock


# =============================================================================
# TESTS
# =============================================================================


class TestInboxPageAPIDisconnected:
    """When API is down, render_api_error is called and st.tabs is not."""

    def setup_method(self):
        _reset_st()

    @patch('dashboard.inbox_page.render_api_error')
    @patch('dashboard.inbox_page.check_api_connection', return_value=False)
    def test_api_down_shows_error_no_tabs(self, mock_check, mock_render_err):
        from dashboard.inbox_page import render_inbox_page

        render_inbox_page()

        mock_render_err.assert_called_once()
        mock_st.tabs.assert_not_called()


class TestInboxPageHealthyRender:
    """When API is healthy, all 4 statuses are fetched in correct order."""

    def setup_method(self):
        _reset_st()

    @patch('dashboard.inbox_page.render_empty_state')
    @patch('dashboard.inbox_page.fetch_inbox_companies')
    @patch('dashboard.inbox_page.APIClient')
    @patch('dashboard.inbox_page.check_api_connection', return_value=True)
    def test_fetch_order_is_inbox_tracking_passed_pipeline(
        self, mock_check, mock_client_cls, mock_fetch, mock_empty
    ):
        from dashboard.inbox_page import render_inbox_page

        mock_fetch.return_value = {"companies": []}

        render_inbox_page()

        assert mock_fetch.call_count == 4
        statuses = [c.kwargs["status"] for c in mock_fetch.call_args_list]
        assert statuses == ["inbox", "tracking", "passed", "pipeline_requested"]


class TestInboxPageOneTabError:
    """One tab returning an error surfaces error while other tabs still fetch."""

    def setup_method(self):
        _reset_st()

    @patch('dashboard.inbox_page.render_api_error')
    @patch('dashboard.inbox_page.render_empty_state')
    @patch('dashboard.inbox_page.fetch_inbox_companies')
    @patch('dashboard.inbox_page.APIClient')
    @patch('dashboard.inbox_page.check_api_connection', return_value=True)
    def test_single_tab_error_isolated(
        self, mock_check, mock_client_cls, mock_fetch, mock_empty, mock_render_err
    ):
        from dashboard.inbox_page import render_inbox_page

        def fetch_side_effect(_client, status="inbox", **kwargs):
            if status == "tracking":
                return {"companies": [], "error": "Tracking endpoint failed"}
            return {"companies": []}

        mock_fetch.side_effect = fetch_side_effect

        render_inbox_page()

        # All 4 fetches still occur
        assert mock_fetch.call_count == 4
        # render_api_error called for the tracking tab error
        mock_render_err.assert_called_once_with("Tracking endpoint failed")


class TestInboxPageActionRefresh:
    """When an action is taken, cache is cleared and page reruns."""

    def setup_method(self):
        _reset_st()

    @patch('dashboard.inbox_page.render_company_list', return_value=True)
    @patch('dashboard.inbox_page.fetch_inbox_companies')
    @patch('dashboard.inbox_page.APIClient')
    @patch('dashboard.inbox_page.check_api_connection', return_value=True)
    def test_action_triggers_cache_clear_and_rerun(
        self, mock_check, mock_client_cls, mock_fetch, mock_render_list
    ):
        from dashboard.inbox_page import render_inbox_page

        mock_fetch.return_value = {"companies": [{"id": 1, "name": "Test Co"}]}

        render_inbox_page()

        mock_st.cache_data.clear.assert_called()
        mock_st.rerun.assert_called()


class TestInboxPageEmptyState:
    """Empty company list triggers render_empty_state."""

    def setup_method(self):
        _reset_st()

    @patch('dashboard.inbox_page.render_empty_state')
    @patch('dashboard.inbox_page.fetch_inbox_companies')
    @patch('dashboard.inbox_page.APIClient')
    @patch('dashboard.inbox_page.check_api_connection', return_value=True)
    def test_empty_companies_renders_empty_state(
        self, mock_check, mock_client_cls, mock_fetch, mock_empty
    ):
        from dashboard.inbox_page import render_inbox_page

        mock_fetch.return_value = {"companies": []}

        render_inbox_page()

        mock_empty.assert_called()
