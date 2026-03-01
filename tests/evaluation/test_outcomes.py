"""Tests for canonical evaluation outcomes."""

from evaluation.outcomes import TARGET_OUTCOMES, VALID_OUTCOME_NAMES, validate_outcome


def test_target_outcomes_defined():
    assert len(TARGET_OUTCOMES) >= 3
    for outcome in TARGET_OUTCOMES:
        assert "name" in outcome
        assert "definition" in outcome
        assert "data_source" in outcome


def test_validate_outcome_accepts_known():
    assert validate_outcome("signal_quality") is True


def test_validate_outcome_rejects_unknown():
    assert validate_outcome("nonexistent") is False


def test_valid_outcome_names_set():
    assert "signal_quality" in VALID_OUTCOME_NAMES
    assert "coverage_delta" in VALID_OUTCOME_NAMES
    assert "convergence_impact" in VALID_OUTCOME_NAMES
