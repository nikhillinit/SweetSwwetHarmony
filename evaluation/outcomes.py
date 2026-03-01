"""Canonical outcome definitions for shadow feature governance."""

from __future__ import annotations

TARGET_OUTCOMES = [
    {
        "name": "signal_quality",
        "definition": (
            "Feature reduced false positives or surfaced companies later confirmed as valid."
        ),
        "data_source": "signal_quality_metrics",
    },
    {
        "name": "coverage_delta",
        "definition": "Feature increased the share of entities with non-null feature values.",
        "data_source": "signals + company_files",
    },
    {
        "name": "convergence_impact",
        "definition": "Feature improved entity convergence across distinct evidence families.",
        "data_source": "signals.evidence_family",
    },
]

VALID_OUTCOME_NAMES = {outcome["name"] for outcome in TARGET_OUTCOMES}


def validate_outcome(name: str) -> bool:
    """Return True when ``name`` is one of the canonical outcome identifiers."""
    return name in VALID_OUTCOME_NAMES

