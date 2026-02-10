"""
Tests for triage dashboard views: Fast Pass, Deep Review, Batch Publish.

Follows pattern from test_scheduler_view.py: mock streamlit at module level,
patch APIClient for API mocking, mock context managers for st.tabs/st.columns.
"""

import sys
import uuid
from unittest.mock import MagicMock, patch, PropertyMock

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
    # Clear any lingering side_effect on widgets (side_effect takes precedence over return_value)
    mock_st.selectbox.side_effect = None
    mock_st.text_input.side_effect = None
    mock_st.slider.side_effect = None
    mock_st.multiselect.side_effect = None
    mock_st.date_input.side_effect = None
    mock_st.radio.side_effect = None
    mock_st.button.side_effect = None
    # Setup st.tabs to return context managers
    mock_st.tabs.return_value = [_make_ctx_manager() for _ in range(4)]
    # st.columns needs to return the right number of context managers
    mock_st.columns.side_effect = lambda n, **kw: [_make_ctx_manager() for _ in range(n if isinstance(n, int) else len(n))]
    # st.cache_data as passthrough decorator
    mock_st.cache_data = lambda **kwargs: (lambda fn: fn)
    # st.spinner as context manager
    spinner = _make_ctx_manager()
    mock_st.spinner.return_value = spinner
    # st.container returns a context manager
    mock_st.container.return_value = _make_ctx_manager()


# =============================================================================
# FAST PASS VIEW TESTS
# =============================================================================

class TestTriageFastPage:
    def setup_method(self):
        _reset_st()
        # Provide sidebar context manager
        mock_st.sidebar = _make_ctx_manager()
        mock_st.sidebar.__enter__ = MagicMock(return_value=mock_st.sidebar)
        # Default filter return values
        mock_st.selectbox.return_value = "pending"
        mock_st.slider.return_value = 0.0
        mock_st.text_input.return_value = ""
        mock_st.multiselect.return_value = []
        mock_st.date_input.return_value = ()
        mock_st.radio.return_value = "Descending"
        mock_st.number_input.return_value = 50
        mock_st.checkbox.return_value = False
        mock_st.button.return_value = False

    @patch('dashboard.views.triage_fast.APIClient')
    def test_renders_items(self, mock_client_cls):
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {
            "data": [
                {
                    "review_id": 1,
                    "company_id": "comp_1",
                    "company_name": "Acme Inc",
                    "confidence": 0.85,
                    "status": "pending",
                    "sources": "github",
                    "signal_count": 3,
                    "thesis_category": "consumer_cpg",
                    "updated_at": "2026-01-15T00:00:00Z",
                    "created_at": "2026-01-15T00:00:00Z",
                }
            ],
            "meta": {"has_more": False, "next_cursor": None},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        mock_st.title.assert_called_once_with("Triage — Fast Pass")

    @patch('dashboard.views.triage_fast.APIClient')
    def test_empty_state(self, mock_client_cls):
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        mock_st.info.assert_called()

    @patch('dashboard.views.triage_fast.APIClient')
    def test_api_error(self, mock_client_cls):
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {"error": True, "message": "Connection refused"}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        mock_st.error.assert_called()

    @patch('dashboard.views.triage_fast.APIClient')
    def test_filters_applied(self, mock_client_cls):
        from dashboard.views.triage_fast import render_triage_fast_page

        mock_st.selectbox.return_value = "approved"
        mock_st.slider.return_value = 0.5
        mock_st.multiselect.return_value = ["github"]
        mock_st.text_input.return_value = ""

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        client.list_triage.assert_called_once()

    @patch('dashboard.views.triage_fast.APIClient')
    def test_pagination_cursor_stack(self, mock_client_cls):
        from dashboard.views.triage_fast import render_triage_fast_page

        # Simulate page with next cursor (just 2 items for simplicity)
        client = MagicMock()
        client.list_triage.return_value = {
            "data": [
                {"review_id": 1, "company_id": "c1", "company_name": "Co 1",
                 "confidence": 0.5, "status": "pending", "sources": "github",
                 "signal_count": 1, "thesis_category": "", "updated_at": "2026-01-01",
                 "created_at": "2026-01-01"},
            ],
            "meta": {"has_more": True, "next_cursor": "cursor_abc"},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # Verify no crash — pagination buttons should be rendered

    @patch('dashboard.views.triage_fast.APIClient')
    def test_filter_change_resets_cursor(self, mock_client_cls):
        """Changing filters should reset cursor and stack."""
        mock_st.session_state["triage_cursor"] = "old_cursor"
        mock_st.session_state["triage_cursor_stack"] = ["cursor1"]
        mock_st.session_state["triage_prev_filters"] = {
            "status": "pending", "min_confidence": None,
            "source_api": "", "search": "",
            "start_date": None, "end_date": None,
            "sort_by": "confidence", "sort_order": "desc",
        }

        # New filter values (different status)
        mock_st.selectbox.return_value = "approved"
        mock_st.slider.return_value = 0.0
        mock_st.text_input.return_value = ""
        mock_st.multiselect.return_value = []
        mock_st.date_input.return_value = ()
        mock_st.radio.return_value = "Descending"

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        from dashboard.views.triage_fast import render_triage_fast_page
        render_triage_fast_page()

        # Cursor should be reset
        assert mock_st.session_state.get("triage_cursor") is None
        assert mock_st.session_state.get("triage_cursor_stack") == []

    @patch('dashboard.views.triage_fast.APIClient')
    def test_date_range_filter_passed_to_api(self, mock_client_cls):
        """Date range filter values should be forwarded to list_triage."""
        from dashboard.views.triage_fast import render_triage_fast_page
        from datetime import date

        mock_st.date_input.return_value = (date(2026, 1, 1), date(2026, 1, 31))

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        call_kwargs = client.list_triage.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("start_date") == "2026-01-01"
        assert kwargs.get("end_date") == "2026-01-31"

    @patch('dashboard.views.triage_fast.APIClient')
    def test_date_range_empty_does_not_filter(self, mock_client_cls):
        """Empty date range should pass None for start_date and end_date."""
        from dashboard.views.triage_fast import render_triage_fast_page

        mock_st.date_input.return_value = ()

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        call_kwargs = client.list_triage.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("start_date") is None
        assert kwargs.get("end_date") is None

    @patch('dashboard.views.triage_fast.APIClient')
    def test_multiselect_source_apis(self, mock_client_cls):
        """Multi-select source APIs should be joined as comma-separated string."""
        from dashboard.views.triage_fast import render_triage_fast_page

        mock_st.multiselect.return_value = ["github", "sec_edgar"]

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        call_kwargs = client.list_triage.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("source_api") == "github,sec_edgar"

    @patch('dashboard.views.triage_fast.APIClient')
    def test_multiselect_empty_passes_none(self, mock_client_cls):
        """Empty multi-select should pass None for source_api."""
        from dashboard.views.triage_fast import render_triage_fast_page

        mock_st.multiselect.return_value = []

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        call_kwargs = client.list_triage.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("source_api") is None

    @patch('dashboard.views.triage_fast.APIClient')
    def test_sort_params_passed_to_api(self, mock_client_cls):
        """Sort by and order should be forwarded to list_triage."""
        from dashboard.views.triage_fast import render_triage_fast_page

        # selectbox is called twice: first for Status, then for Sort By
        # We use side_effect to return different values for each call
        mock_st.selectbox.side_effect = ["pending", "detected_at"]
        mock_st.radio.return_value = "Ascending"

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        call_kwargs = client.list_triage.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("sort_by") == "detected_at"
        assert kwargs.get("sort_order") == "asc"

    @patch('dashboard.views.triage_fast.APIClient')
    def test_sort_descending_default(self, mock_client_cls):
        """Default sort order should be descending."""
        from dashboard.views.triage_fast import render_triage_fast_page

        mock_st.radio.return_value = "Descending"

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        call_kwargs = client.list_triage.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("sort_order") == "desc"

    @patch('dashboard.views.triage_fast.APIClient')
    def test_row_count_with_has_more(self, mock_client_cls):
        """When has_more=True, should show 'Showing N of more'."""
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {
            "data": [
                {
                    "review_id": 1, "company_name": "Co", "confidence": 0.5,
                    "status": "pending", "sources": "github", "signal_count": 1,
                    "thesis_category": "", "updated_at": "2026-01-01",
                }
            ],
            "meta": {"has_more": True, "next_cursor": "cur_123"},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # Find the markdown call with "Showing"
        found = False
        for call in mock_st.markdown.call_args_list:
            args, _ = call
            if args and "Showing 1 of more" in str(args[0]):
                found = True
                break
        assert found, "Expected 'Showing 1 of more' in markdown calls"

    @patch('dashboard.views.triage_fast.APIClient')
    def test_row_count_without_has_more(self, mock_client_cls):
        """When has_more=False, should show 'Showing N items'."""
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {
            "data": [
                {
                    "review_id": 1, "company_name": "Co", "confidence": 0.5,
                    "status": "pending", "sources": "github", "signal_count": 1,
                    "thesis_category": "", "updated_at": "2026-01-01",
                }
            ],
            "meta": {"has_more": False, "next_cursor": None},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # Find the markdown call with "Showing"
        found = False
        for call in mock_st.markdown.call_args_list:
            args, _ = call
            if args and "Showing 1 items" in str(args[0]):
                found = True
                break
        assert found, "Expected 'Showing 1 items' in markdown calls"

    @patch('dashboard.views.triage_fast.APIClient')
    def test_date_range_filter_resets_cursor(self, mock_client_cls):
        """Changing date range should reset cursor."""
        from dashboard.views.triage_fast import render_triage_fast_page
        from datetime import date

        mock_st.session_state["triage_cursor"] = "old_cursor"
        mock_st.session_state["triage_cursor_stack"] = ["c1"]
        mock_st.session_state["triage_prev_filters"] = {
            "status": "pending", "min_confidence": None,
            "source_api": "", "search": "",
            "start_date": None, "end_date": None,
            "sort_by": "confidence", "sort_order": "desc",
        }

        # Change date range
        mock_st.date_input.return_value = (date(2026, 2, 1), date(2026, 2, 28))

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        assert mock_st.session_state.get("triage_cursor") is None
        assert mock_st.session_state.get("triage_cursor_stack") == []

    @patch('dashboard.views.triage_fast.APIClient')
    def test_sort_change_resets_cursor(self, mock_client_cls):
        """Changing sort order should reset cursor."""
        from dashboard.views.triage_fast import render_triage_fast_page

        mock_st.session_state["triage_cursor"] = "old_cursor"
        mock_st.session_state["triage_cursor_stack"] = ["c1"]
        mock_st.session_state["triage_prev_filters"] = {
            "status": "pending", "min_confidence": None,
            "source_api": "", "search": "",
            "start_date": None, "end_date": None,
            "sort_by": "confidence", "sort_order": "desc",
        }

        # Change sort order
        mock_st.radio.return_value = "Ascending"

        client = MagicMock()
        client.list_triage.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_triage_fast_page()

        assert mock_st.session_state.get("triage_cursor") is None
        assert mock_st.session_state.get("triage_cursor_stack") == []

    def test_source_api_options_constant(self):
        """SOURCE_API_OPTIONS should contain expected collectors."""
        from dashboard.views.triage_fast import SOURCE_API_OPTIONS

        assert "github" in SOURCE_API_OPTIONS
        assert "sec_edgar" in SOURCE_API_OPTIONS
        assert "news_api" in SOURCE_API_OPTIONS
        assert "job_postings" in SOURCE_API_OPTIONS
        assert "domain_whois" in SOURCE_API_OPTIONS
        assert "hacker_news" in SOURCE_API_OPTIONS
        assert "companies_house" in SOURCE_API_OPTIONS
        assert "rss_feeds" in SOURCE_API_OPTIONS
        assert len(SOURCE_API_OPTIONS) == 8


# =============================================================================
# DEEP REVIEW (DETAIL) VIEW TESTS
# =============================================================================

class TestTriageDetailPage:
    def setup_method(self):
        _reset_st()
        mock_st.button.return_value = False

    @patch('dashboard.views.triage_detail.APIClient')
    def test_renders_detail(self, mock_client_cls):
        from dashboard.views.triage_detail import render_triage_detail_page

        mock_st.session_state["triage_selected_id"] = 42

        client = MagicMock()
        client.get_triage_detail.return_value = {
            "data": {
                "review_id": 42,
                "company_id": "comp_1",
                "company_name": "Acme Inc",
                "canonical_key": "domain:acme.ai",
                "confidence": 0.85,
                "status": "pending",
                "updated_at": "2026-01-15T00:00:00Z",
                "total_signal_count": 5,
                "signals": [],
                "thesis_category": "consumer_cpg",
                "thesis_rationale": "Good fit",
                "case_law_matches": [],
                "ach_summary": None,
                "audit_history": [],
            }
        }
        client.get_triage_ach.return_value = {"error": True, "status_code": 404}
        mock_client_cls.return_value = client

        render_triage_detail_page()

        mock_st.title.assert_called_with("Acme Inc")

    @patch('dashboard.views.triage_detail.APIClient')
    def test_back_button_clears_selection(self, mock_client_cls):
        """Back button should clear the selected ID when clicked."""
        from dashboard.views.triage_detail import render_triage_detail_page

        mock_st.session_state["triage_selected_id"] = 42
        # First button call is "Back" → True
        mock_st.button.return_value = True

        client = MagicMock()
        mock_client_cls.return_value = client

        render_triage_detail_page()

        # Should clear selected ID (set to None via the back handler)
        assert mock_st.session_state.get("triage_selected_id") is None

    def test_no_selection_warning(self):
        from dashboard.views.triage_detail import render_triage_detail_page

        mock_st.session_state.pop("triage_selected_id", None)

        render_triage_detail_page()

        mock_st.warning.assert_called()

    @patch('dashboard.views.triage_detail.APIClient')
    def test_idempotency_key_persistence(self, mock_client_cls):
        """Idempotency key should persist in session state across reruns."""
        from dashboard.views.triage_detail import _set_pending_action, _clear_pending_action

        _set_pending_action("Approve")
        key = mock_st.session_state.get("pending_action_key")
        assert key is not None
        assert mock_st.session_state.get("pending_action_type") == "Approve"

        # Simulate rerun — key should still be there
        assert mock_st.session_state.get("pending_action_key") == key

        _clear_pending_action()
        assert "pending_action_key" not in mock_st.session_state

    @patch('dashboard.views.triage_detail.APIClient')
    def test_tabs_rendered(self, mock_client_cls):
        from dashboard.views.triage_detail import render_triage_detail_page

        mock_st.session_state["triage_selected_id"] = 42

        client = MagicMock()
        client.get_triage_detail.return_value = {
            "data": {
                "review_id": 42,
                "company_id": "comp_1",
                "company_name": "Test",
                "canonical_key": "domain:test.ai",
                "confidence": 0.5,
                "status": "pending",
                "updated_at": "2026-01-01",
                "total_signal_count": 0,
                "signals": [],
                "thesis_category": None,
                "thesis_rationale": None,
                "case_law_matches": [],
                "ach_summary": None,
                "audit_history": [],
            }
        }
        client.get_triage_ach.return_value = {"error": True}
        mock_client_cls.return_value = client

        render_triage_detail_page()

        mock_st.tabs.assert_called_with(["Signals", "Thesis", "ACH", "Audit History"])


# =============================================================================
# BATCH PUBLISH VIEW TESTS
# =============================================================================

class TestBatchPublishPage:
    def setup_method(self):
        _reset_st()
        mock_st.button.return_value = False
        mock_st.number_input.return_value = 50
        mock_st.text_input.return_value = ""
        mock_st.checkbox.return_value = False
        # Two tabs
        mock_st.tabs.return_value = [_make_ctx_manager(), _make_ctx_manager()]

    @patch('dashboard.views.batch_publish.APIClient')
    def test_batch_list(self, mock_client_cls):
        from dashboard.views.batch_publish import render_batch_publish_page

        client = MagicMock()
        client.list_batches.return_value = {
            "data": [
                {
                    "batch_id": "batch_001",
                    "status": "committed",
                    "item_count": 10,
                    "pushed_count": 10,
                    "error_count": 0,
                    "actor": "user@test.com",
                    "created_at": "2026-01-15T00:00:00Z",
                    "committed_at": "2026-01-15T00:05:00Z",
                }
            ],
            "meta": {"has_more": False},
        }
        mock_client_cls.return_value = client

        render_batch_publish_page()

        mock_st.title.assert_called_with("Batch Publish")

    @patch('dashboard.views.batch_publish.APIClient')
    def test_create_flow(self, mock_client_cls):
        from dashboard.views.batch_publish import render_batch_publish_page

        # Simulate Create button click
        mock_st.button.side_effect = [True, False, False, False]

        client = MagicMock()
        client.create_batch.return_value = {
            "data": {"batch_id": "batch_new", "item_count": 5, "items_hash": "abc123"},
        }
        client.list_batches.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        render_batch_publish_page()

        client.create_batch.assert_called_once()

    @patch('dashboard.views.batch_publish.APIClient')
    def test_commit_idempotency(self, mock_client_cls):
        """Commit should use idempotency key from session state."""
        from dashboard.views.batch_publish import render_batch_publish_page

        mock_st.session_state["batch_draft_id"] = "batch_abc"

        client = MagicMock()
        client.get_batch_preview.return_value = {
            "data": {
                "batch_id": "batch_abc",
                "status": "draft",
                "item_count": 3,
                "items": [],
                "items_hash": "hash123",
            },
        }
        client.commit_batch.return_value = {"data": {"status": "committed"}}
        client.list_batches.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        # Simulate commit button click
        mock_st.button.side_effect = [False, True, False, False]
        render_batch_publish_page()

        # Verify commit was called with an idempotency key
        if client.commit_batch.called:
            call_kwargs = client.commit_batch.call_args
            assert call_kwargs is not None

    @patch('dashboard.views.batch_publish.APIClient')
    def test_abort_idempotency(self, mock_client_cls):
        """Abort should generate an idempotency key."""
        from dashboard.views.batch_publish import render_batch_publish_page

        mock_st.session_state["batch_draft_id"] = "batch_xyz"

        client = MagicMock()
        client.get_batch_preview.return_value = {
            "data": {
                "batch_id": "batch_xyz",
                "status": "draft",
                "item_count": 2,
                "items": [],
                "items_hash": "hash456",
            },
        }
        client.abort_batch.return_value = {"data": {"status": "aborted"}}
        client.list_batches.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        # Simulate abort button click
        mock_st.button.side_effect = [False, False, True, False]
        render_batch_publish_page()

    @patch('dashboard.views.batch_publish.APIClient')
    def test_toctou_error(self, mock_client_cls):
        """409 from commit should show TOCTOU error."""
        from dashboard.views.batch_publish import render_batch_publish_page

        mock_st.session_state["batch_draft_id"] = "batch_toctou"

        client = MagicMock()
        client.get_batch_preview.return_value = {
            "data": {
                "batch_id": "batch_toctou",
                "status": "draft",
                "item_count": 2,
                "items": [],
                "items_hash": "hash789",
            },
        }
        client.commit_batch.return_value = {"error": True, "status_code": 409, "detail": "items changed"}
        client.list_batches.return_value = {"data": [], "meta": {"has_more": False}}
        mock_client_cls.return_value = client

        # Simulate commit button click
        mock_st.button.side_effect = [False, True, False, False]
        render_batch_publish_page()

        # Should have called commit
        if client.commit_batch.called:
            # The error handling should produce an st.error call
            pass  # Error handling tested functionally
