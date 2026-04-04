"""Tests for ThesisFilter._is_operational_llm_failure()."""

import pytest
from unittest.mock import MagicMock

from utils.thesis_filter import ThesisFilter, ThesisFilterConfig


@pytest.fixture
def filter_instance():
    return ThesisFilter(ThesisFilterConfig())


def _make_llm_result(classification_status="success"):
    """Build a MagicMock LLM result with a status field."""
    m = MagicMock()
    m.classification_status = classification_status
    return m


@pytest.mark.parametrize(
    "status",
    [
        "error_api",
        "error_parse",
        "error_rate_limit",
        "error_circuit_breaker",
    ],
)
def test_non_success_status_is_operational_failure(filter_instance, status):
    result = _make_llm_result(classification_status=status)
    assert filter_instance._is_operational_llm_failure(result) is True


def test_success_status_is_not_operational_failure(filter_instance):
    result = _make_llm_result(classification_status="success")
    assert filter_instance._is_operational_llm_failure(result) is False


def test_missing_status_defaults_to_success(filter_instance):
    result = MagicMock(spec=[])
    assert filter_instance._is_operational_llm_failure(result) is False


def test_none_result_is_not_operational_failure(filter_instance):
    assert filter_instance._is_operational_llm_failure(None) is False


def test_legacy_payload_shape_still_detected(filter_instance):
    """Fallback heuristic should preserve fail-open behavior for legacy payloads."""
    result = MagicMock(spec=[])
    result.thesis_fit_score = 0.0
    result.category = "excluded"
    result.rationale = "classification failed"
    assert filter_instance._is_operational_llm_failure(result) is True


def test_explicit_success_status_overrides_legacy_payload_shape(filter_instance):
    """Status field should win over legacy rationale heuristics when present."""
    result = MagicMock()
    result.classification_status = "success"
    result.thesis_fit_score = 0.0
    result.category = "excluded"
    result.rationale = "classification failed"
    assert filter_instance._is_operational_llm_failure(result) is False
