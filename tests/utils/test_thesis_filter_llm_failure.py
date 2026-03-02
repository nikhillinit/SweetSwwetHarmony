"""Tests for ThesisFilter._is_operational_llm_failure() — exhaustive coverage.

Validates all 5 failure markers, non-string rationale types, boundary cases,
and edge conditions per Phase 1 of thesis filter gap closure.
"""
import pytest
from unittest.mock import MagicMock

from utils.thesis_filter import ThesisFilter, ThesisFilterConfig


@pytest.fixture
def filter_instance():
    return ThesisFilter(ThesisFilterConfig())


def _make_llm_result(
    thesis_fit_score=0.0,
    category="excluded",
    rationale="classification failed",
    **overrides,
):
    """Build a MagicMock LLM result with standard attributes."""
    m = MagicMock()
    m.thesis_fit_score = thesis_fit_score
    m.category = category
    m.rationale = rationale
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


# ── Parametrized: all 5 failure markers ──────────────────────────────────────

_FAILURE_MARKERS = [
    "classification failed",
    "rate limit exceeded",
    "circuit breaker open",
    "gemini unavailable",
    "failed to parse response",
]


@pytest.mark.parametrize("marker", _FAILURE_MARKERS)
def test_individual_markers(filter_instance, marker):
    """Each failure marker with score=0.0 and category=excluded → True."""
    result = _make_llm_result(rationale=marker)
    assert filter_instance._is_operational_llm_failure(result) is True


# ── Parametrized: non-string rationale types (graceful, no exception) ────────

@pytest.mark.parametrize(
    "rationale",
    [None, 42, {"key": "val"}, ["list"], True],
    ids=["none", "int", "dict", "list", "bool"],
)
def test_non_string_rationale(filter_instance, rationale):
    """Non-string rationale with score=0.0 and category=excluded → False (no crash)."""
    result = _make_llm_result(rationale=rationale)
    assert filter_instance._is_operational_llm_failure(result) is False


# ── Individual edge cases ────────────────────────────────────────────────────

def test_score_none_immediate_true(filter_instance):
    """score=None with category=excluded → True (early return)."""
    result = _make_llm_result(thesis_fit_score=None)
    assert filter_instance._is_operational_llm_failure(result) is True


def test_real_exclusion_no_marker(filter_instance):
    """score=0.0, category=excluded, real rationale (no marker) → False."""
    result = _make_llm_result(rationale="Company is B2B SaaS")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_nonzero_score_with_marker(filter_instance):
    """score=0.001 with marker rationale → False (score must be exactly 0.0)."""
    result = _make_llm_result(thesis_fit_score=0.001, rationale="rate limit exceeded")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_category_not_excluded(filter_instance):
    """score=0.0, category=consumer_cpg with marker → False (category must be 'excluded')."""
    result = _make_llm_result(category="consumer_cpg", rationale="rate limit exceeded")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_none_result(filter_instance):
    """llm_result=None → False."""
    assert filter_instance._is_operational_llm_failure(None) is False


def test_missing_rationale_attr(filter_instance):
    """MagicMock without rationale attr → False."""
    m = MagicMock(spec=[])
    m.thesis_fit_score = 0.0
    m.category = "excluded"
    # No rationale attribute — getattr returns "" via fallback
    assert filter_instance._is_operational_llm_failure(m) is False


def test_empty_rationale(filter_instance):
    """score=0.0, category=excluded, rationale="" → False."""
    result = _make_llm_result(rationale="")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_mixed_case_rationale(filter_instance):
    """Mixed case rationale should match (lowered internally)."""
    result = _make_llm_result(rationale="Rate Limit Exceeded")
    assert filter_instance._is_operational_llm_failure(result) is True


def test_multiple_markers(filter_instance):
    """Rationale containing multiple failure markers → True."""
    result = _make_llm_result(
        rationale="classification failed and rate limit exceeded"
    )
    assert filter_instance._is_operational_llm_failure(result) is True


def test_partial_marker_no_match(filter_instance):
    """Partial marker 'rate limit' without 'exceeded' → False."""
    result = _make_llm_result(rationale="rate limit")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_non_float_score(filter_instance):
    """Non-numeric score string with category=excluded → False (TypeError caught)."""
    result = _make_llm_result(thesis_fit_score="N/A")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_missing_category_attr(filter_instance):
    """MagicMock without category attr → False."""
    m = MagicMock(spec=[])
    m.thesis_fit_score = 0.0
    m.rationale = "classification failed"
    # No category attribute — getattr returns None
    assert filter_instance._is_operational_llm_failure(m) is False
