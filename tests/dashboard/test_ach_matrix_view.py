"""
Tests for ACH matrix dashboard view and triage hardening improvements.

Tests the ACH grid rendering (render_ach_view from dashboard.views.ach_matrix)
and extended filter/sort/count features in the triage fast pass view.

Follows the established dashboard test pattern: mock streamlit at module level,
mock pandas for ACH dataframe tests, patch APIClient for API mocking, and use
mock context managers for st.tabs/st.columns.
"""

import sys
from unittest.mock import MagicMock, patch, call

# Mock streamlit BEFORE importing views
if "streamlit" not in sys.modules or not isinstance(sys.modules["streamlit"], MagicMock):
    sys.modules["streamlit"] = MagicMock()
mock_st = sys.modules["streamlit"]

# Mock pandas BEFORE importing ACH view (it uses pandas for dataframe creation)
_real_pd_mock = MagicMock()
if "pandas" not in sys.modules or not isinstance(sys.modules["pandas"], MagicMock):
    sys.modules["pandas"] = _real_pd_mock
mock_pd = sys.modules["pandas"]


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
    """Create a mock context manager for st.tabs/st.columns/st.container entries."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=None)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _reset_st():
    """Reset streamlit mock and session state."""
    mock_st.reset_mock()
    mock_st.session_state = MockSessionState()
    # Setup st.tabs to return context managers
    mock_st.tabs.return_value = [_make_ctx_manager() for _ in range(4)]
    # st.columns needs to return the right number of context managers
    mock_st.columns.side_effect = lambda n, **kw: [
        _make_ctx_manager() for _ in range(n if isinstance(n, int) else len(n))
    ]
    # st.cache_data as passthrough decorator
    mock_st.cache_data = lambda **kwargs: (lambda fn: fn)
    # st.spinner as context manager
    mock_st.spinner.return_value = _make_ctx_manager()
    # st.container returns a context manager
    mock_st.container.return_value = _make_ctx_manager()
    # st.expander returns a context manager
    mock_st.expander.return_value = _make_ctx_manager()

    # Re-bind module-level st in ach_matrix if already imported
    if "dashboard.views.ach_matrix" in sys.modules:
        sys.modules["dashboard.views.ach_matrix"].st = mock_st


def _reset_pd():
    """Reset pandas mock for ACH dataframe tests."""
    mock_pd.reset_mock()
    # DataFrame constructor returns a mock with to_csv and style.map methods
    mock_df = MagicMock()
    mock_df.to_csv.return_value = "Evidence,H1,H2,H3,H4,H5\nKeyword score,+1,0,-1,0,0\n"
    mock_df.shape = (15, 6)  # 14 evidence rows + 1 summary row
    # style.map returns a styled mock for st.dataframe
    mock_styled = MagicMock()
    mock_df.style.map.return_value = mock_styled
    mock_pd.DataFrame.return_value = mock_df

    # Re-bind module-level pd in ach_matrix if already imported
    if "dashboard.views.ach_matrix" in sys.modules:
        sys.modules["dashboard.views.ach_matrix"].pd = mock_pd


# =============================================================================
# SAMPLE TEST DATA
# =============================================================================

SAMPLE_ACH_DATA = {
    "hypotheses": [
        {"id": "H1", "label": "Strong Thesis Fit", "description": "Pre-seed to Series A consumer"},
        {"id": "H2", "label": "Weak Thesis Fit", "description": "Consumer-adjacent"},
        {"id": "H3", "label": "B2B in Disguise", "description": "Appears consumer but B2B"},
        {"id": "H4", "label": "Too Early", "description": "Lacks traction"},
        {"id": "H5", "label": "Already Funded B+", "description": "Beyond target stage"},
    ],
    "evidence": [
        {"evidence_id": "E1", "label": "Keyword score", "raw_value": 0.85, "available": True},
        {"evidence_id": "E2", "label": "LLM fit score", "raw_value": 0.9, "available": True},
        {"evidence_id": "E3", "label": "LLM category", "raw_value": "Consumer CPG", "available": True},
        {"evidence_id": "E4", "label": "Competitor flag", "raw_value": False, "available": True},
        {"evidence_id": "E5", "label": "Case-law TP similarity", "raw_value": 0.72, "available": True},
        {"evidence_id": "E6", "label": "Case-law FP similarity", "raw_value": 0.3, "available": True},
        {"evidence_id": "E7", "label": "Distinct source count", "raw_value": 3, "available": True},
        {"evidence_id": "E8", "label": "Multi-source flag", "raw_value": True, "available": True},
        {"evidence_id": "E9", "label": "Stage estimate", "raw_value": "Pre-Seed", "available": True},
        {"evidence_id": "E10", "label": "Negative keyword hit", "raw_value": False, "available": True},
        {"evidence_id": "E11", "label": "Exemplar similarity", "raw_value": None, "available": False},
        {"evidence_id": "E12", "label": "Max signal confidence", "raw_value": 0.85, "available": True},
        {"evidence_id": "E13", "label": "Signal recency bucket", "raw_value": "recent", "available": True},
        {"evidence_id": "E14", "label": "Thesis rationale present", "raw_value": True, "available": True},
    ],
    "cells": [
        {"evidence_id": "E1", "hypothesis_id": "H1", "score": 1},
        {"evidence_id": "E1", "hypothesis_id": "H2", "score": 0},
        {"evidence_id": "E1", "hypothesis_id": "H3", "score": -1},
        {"evidence_id": "E1", "hypothesis_id": "H4", "score": 0},
        {"evidence_id": "E1", "hypothesis_id": "H5", "score": 0},
        {"evidence_id": "E2", "hypothesis_id": "H1", "score": 1},
        {"evidence_id": "E2", "hypothesis_id": "H2", "score": 0},
        {"evidence_id": "E2", "hypothesis_id": "H3", "score": -1},
        {"evidence_id": "E2", "hypothesis_id": "H4", "score": 0},
        {"evidence_id": "E2", "hypothesis_id": "H5", "score": 0},
        {"evidence_id": "E3", "hypothesis_id": "H1", "score": 1},
        {"evidence_id": "E3", "hypothesis_id": "H2", "score": 0},
        {"evidence_id": "E3", "hypothesis_id": "H3", "score": -1},
        {"evidence_id": "E3", "hypothesis_id": "H4", "score": 0},
        {"evidence_id": "E3", "hypothesis_id": "H5", "score": 0},
    ],
    "hypothesis_scores": {"H1": 5.0, "H2": 1.0, "H3": -3.0, "H4": -2.0, "H5": -1.0},
    "top_hypothesis": "H1",
    "top_score": 5.0,
    "evidence_count": 13,
}

SAMPLE_TRIBUNAL_DATA = {
    "bull_summary": "Keyword score of 85% strongly aligns with thesis [E1]. Multi-source verified [E8].",
    "bear_summary": "No exemplar match available [E11].",
    "differentiators": [
        {"evidence_id": "E1", "evidence_label": "Keyword score", "favors": ["H1"], "opposes": ["H3"]},
    ],
    "differentiator_count": 1,
}


# =============================================================================
# ACH GRID TESTS
# =============================================================================


class TestACHGridRendering:
    """Tests for the render_ach_view function from dashboard.views.ach_matrix."""

    def setup_method(self):
        _reset_st()
        _reset_pd()

    def test_grid_renders_with_dataframe(self):
        """Verify st.dataframe is called when valid ACH data is provided."""
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA)

        mock_st.dataframe.assert_called_once()

    def test_grid_dimensions_match_evidence_count(self):
        """Verify the DataFrame is constructed with correct rows and columns.

        Expects 15 rows (14 evidence + 1 TOTAL SCORE summary) and
        6 columns (Evidence + 5 hypothesis labels).
        """
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA)

        # pd.DataFrame should have been called with the rows list
        mock_pd.DataFrame.assert_called_once()
        call_args = mock_pd.DataFrame.call_args
        rows_arg = call_args[0][0] if call_args[0] else call_args[1].get("data", [])

        # 14 evidence items + 1 TOTAL SCORE summary row = 15 rows
        assert len(rows_arg) == 15, f"Expected 15 rows (14 evidence + 1 summary), got {len(rows_arg)}"

        # Each row should have 6 columns: Evidence + 5 hypothesis labels
        first_row = rows_arg[0]
        assert len(first_row) == 6, f"Expected 6 columns, got {len(first_row)}"

        # Verify column names include "Evidence" and hypothesis labels
        assert "Evidence" in first_row
        assert "Strong Thesis Fit" in first_row
        assert "Weak Thesis Fit" in first_row
        assert "B2B in Disguise" in first_row
        assert "Too Early" in first_row
        assert "Already Funded B+" in first_row

        # Verify the last row is the TOTAL SCORE summary
        last_row = rows_arg[-1]
        assert last_row["Evidence"] == "TOTAL SCORE", (
            f"Expected last row to be 'TOTAL SCORE', got '{last_row['Evidence']}'"
        )

    def test_hypothesis_scores_displayed(self):
        """Verify metric display for top hypothesis, score, and evidence count."""
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA)

        # st.metric should be called for top hypothesis, score, and evidence count
        metric_calls = mock_st.metric.call_args_list
        assert len(metric_calls) >= 3, (
            f"Expected at least 3 st.metric calls (hypothesis, score, evidence), "
            f"got {len(metric_calls)}"
        )

        # Collect metric labels and values
        metric_labels = [c[0][0] for c in metric_calls]
        metric_values = [c[0][1] for c in metric_calls]

        # Check top hypothesis metric
        assert "Top Hypothesis" in metric_labels, (
            f"'Top Hypothesis' not in metric labels: {metric_labels}"
        )
        top_idx = metric_labels.index("Top Hypothesis")
        assert "H1" in metric_values[top_idx], (
            f"Top hypothesis value should contain 'H1', got '{metric_values[top_idx]}'"
        )

        # Check score metric
        assert "Score" in metric_labels, f"'Score' not in metric labels: {metric_labels}"
        score_idx = metric_labels.index("Score")
        assert "+5.0" in metric_values[score_idx], (
            f"Score value should contain '+5.0', got '{metric_values[score_idx]}'"
        )

    def test_narrative_panel_renders_bull_bear(self):
        """With tribunal_data, verify bull and bear cases rendered via st.markdown."""
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA, tribunal_data=SAMPLE_TRIBUNAL_DATA)

        # Collect all st.markdown call args
        markdown_texts = [c[0][0] for c in mock_st.markdown.call_args_list]

        # The narrative panel renders "**Bull Case**" and "**Bear Case**" headings
        # plus the formatted narrative text
        bull_heading_found = any("Bull Case" in text for text in markdown_texts)
        bear_heading_found = any("Bear Case" in text for text in markdown_texts)

        assert bull_heading_found, (
            f"Bull case heading not found in markdown calls: {markdown_texts}"
        )
        assert bear_heading_found, (
            f"Bear case heading not found in markdown calls: {markdown_texts}"
        )

    def test_narrative_not_rendered_without_tribunal(self):
        """With tribunal_data=None, no bull/bear narrative headings should be rendered."""
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA, tribunal_data=None)

        # Gather all markdown calls
        markdown_texts = [c[0][0] for c in mock_st.markdown.call_args_list]

        bull_found = any("Bull Case" in text for text in markdown_texts)
        bear_found = any("Bear Case" in text for text in markdown_texts)

        assert not bull_found, "Bull case should not render when tribunal_data is None"
        assert not bear_found, "Bear case should not render when tribunal_data is None"

    def test_export_button_present(self):
        """Verify st.download_button is called for CSV export."""
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA)

        mock_st.download_button.assert_called_once()
        call_kwargs = mock_st.download_button.call_args
        # Verify it exports as CSV via keyword args or positional
        all_kwargs = call_kwargs[1] if call_kwargs[1] else {}
        assert all_kwargs.get("mime") == "text/csv", (
            f"Export button should have mime='text/csv', got: {all_kwargs}"
        )

    def test_empty_ach_shows_message(self):
        """Empty ach_data (no evidence) should show a warning message, not crash."""
        from dashboard.views.ach_matrix import render_ach_view

        empty_ach = {
            "hypotheses": SAMPLE_ACH_DATA["hypotheses"],
            "evidence": [],
            "cells": [],
            "hypothesis_scores": {},
            "top_hypothesis": None,
            "top_score": None,
            "evidence_count": 0,
        }

        render_ach_view(ach_data=empty_ach)

        # The view shows st.warning for incomplete ACH data (missing evidence)
        mock_st.warning.assert_called()
        # Should NOT call st.dataframe for empty evidence
        mock_st.dataframe.assert_not_called()

    def test_na_cells_handled(self):
        """Evidence with available=False should show N/A in all hypothesis columns."""
        from dashboard.views.ach_matrix import render_ach_view

        render_ach_view(ach_data=SAMPLE_ACH_DATA)

        # Verify pd.DataFrame was called with rows
        mock_pd.DataFrame.assert_called_once()
        call_args = mock_pd.DataFrame.call_args
        rows_arg = call_args[0][0] if call_args[0] else call_args[1].get("data", [])

        # E11 (Exemplar similarity) has available=False.
        # The view prefixes evidence labels: "E11: Exemplar similarity"
        e11_row = None
        for row in rows_arg:
            evidence_label = row.get("Evidence", "")
            if "E11" in evidence_label and "Exemplar" in evidence_label:
                e11_row = row
                break

        assert e11_row is not None, (
            "Could not find E11 (Exemplar similarity) row in dataframe rows. "
            f"Available labels: {[r.get('Evidence', '') for r in rows_arg]}"
        )

        # All hypothesis columns should show "N/A" for unavailable evidence
        for h_label in [
            "Strong Thesis Fit",
            "Weak Thesis Fit",
            "B2B in Disguise",
            "Too Early",
            "Already Funded B+",
        ]:
            assert e11_row[h_label] == "N/A", (
                f"Expected N/A for unavailable evidence E11 under '{h_label}', "
                f"got '{e11_row[h_label]}'"
            )


# =============================================================================
# TRIAGE HARDENING TESTS
# =============================================================================


class TestTriageHardeningFilters:
    """Tests for extended filter/sort/count features in triage fast pass view."""

    def setup_method(self):
        _reset_st()
        # Provide sidebar as context manager
        mock_st.sidebar = _make_ctx_manager()
        mock_st.sidebar.__enter__ = MagicMock(return_value=mock_st.sidebar)
        # Default filter return values
        mock_st.selectbox.return_value = "pending"
        mock_st.selectbox.side_effect = None
        mock_st.slider.return_value = 0.0
        mock_st.slider.side_effect = None
        mock_st.text_input.return_value = ""
        mock_st.text_input.side_effect = None
        mock_st.number_input.return_value = 50
        mock_st.number_input.side_effect = None
        mock_st.checkbox.return_value = False
        mock_st.checkbox.side_effect = None
        mock_st.button.return_value = False
        mock_st.button.side_effect = None
        mock_st.date_input.return_value = ()
        mock_st.date_input.side_effect = None
        mock_st.multiselect.return_value = []
        mock_st.multiselect.side_effect = None
        mock_st.radio.return_value = "Descending"
        mock_st.radio.side_effect = None

    @patch("dashboard.views.triage_fast.APIClient")
    def test_date_range_filter_present(self, mock_client_cls):
        """Verify st.date_input is called in the sidebar for date range filtering."""
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {
            "data": [],
            "meta": {"has_more": False},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # st.date_input should be called for date range filtering
        mock_st.date_input.assert_called()

    @patch("dashboard.views.triage_fast.APIClient")
    def test_source_multiselect_present(self, mock_client_cls):
        """Verify st.multiselect is called with source API options for filtering."""
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {
            "data": [],
            "meta": {"has_more": False},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # st.multiselect should be called for source selection
        mock_st.multiselect.assert_called()
        # Verify the multiselect includes source API options
        multiselect_calls = mock_st.multiselect.call_args_list
        assert len(multiselect_calls) >= 1
        # The first positional arg should be the label, second should be the options
        options_arg = multiselect_calls[0][0][1] if len(multiselect_calls[0][0]) > 1 else []
        assert "github" in options_arg, (
            f"Expected 'github' in multiselect options, got: {options_arg}"
        )

    @patch("dashboard.views.triage_fast.APIClient")
    def test_sort_options_present(self, mock_client_cls):
        """Verify sort selectbox and radio order exist for controlling result ordering."""
        from dashboard.views.triage_fast import render_triage_fast_page

        client = MagicMock()
        client.list_triage.return_value = {
            "data": [],
            "meta": {"has_more": False},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # st.selectbox is called multiple times; at least 2 (status + sort_by)
        selectbox_calls = mock_st.selectbox.call_args_list
        assert len(selectbox_calls) >= 2, (
            f"Expected at least 2 selectbox calls (status + sort), got {len(selectbox_calls)}"
        )

        # One selectbox should offer sort columns
        sort_options_found = False
        for c in selectbox_calls:
            args = c[0]
            if len(args) > 1 and isinstance(args[1], (list, tuple)):
                if "confidence" in args[1] or "detected_at" in args[1]:
                    sort_options_found = True
                    break
        assert sort_options_found, (
            f"Expected a selectbox with sort column options, "
            f"calls: {selectbox_calls}"
        )

    @patch("dashboard.views.triage_fast.APIClient")
    def test_row_count_displayed(self, mock_client_cls):
        """Verify 'Showing N items' text is rendered when results are present."""
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
                },
                {
                    "review_id": 2,
                    "company_id": "comp_2",
                    "company_name": "Beta Corp",
                    "confidence": 0.65,
                    "status": "pending",
                    "sources": "sec_edgar",
                    "signal_count": 1,
                    "thesis_category": "",
                    "updated_at": "2026-01-16T00:00:00Z",
                    "created_at": "2026-01-16T00:00:00Z",
                },
            ],
            "meta": {"has_more": False, "next_cursor": None},
        }
        mock_client_cls.return_value = client

        render_triage_fast_page()

        # Verify row count is displayed via st.markdown: "Showing 2 items"
        markdown_calls = mock_st.markdown.call_args_list
        count_displayed = any(
            "2" in str(c[0][0]) and "item" in str(c[0][0]).lower()
            for c in markdown_calls
            if c[0]
        )
        assert count_displayed, (
            f"Expected row count display with '2 items', "
            f"markdown calls: {[c[0][0] for c in markdown_calls if c[0]]}"
        )
