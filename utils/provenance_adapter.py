"""
Provenance Adapter Module for Phase G Synthesis Enhancement

Converts StoredSignal objects into FieldProvenance objects for synthesis.
This is the glue between the existing signal storage layer and the new
policy-driven consolidation system.

Usage:
    from utils.provenance_adapter import extract_field_provenance

    # Extract provenance for all policy-managed fields from a signal
    provenance = extract_field_provenance(signal, run_id="pipeline-run-123")

    # provenance = {
    #     "company_name": FieldProvenance(...),
    #     "description": FieldProvenance(...),
    #     "founding_date": FieldProvenance(...),
    # }
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable

from utils.merge_policy import (
    normalize_company_name,
    normalize_description,
    normalize_date,
)
from utils.synthesis_types import FieldProvenance


# =============================================================================
# PROTOCOL FOR STORED SIGNAL
# =============================================================================

@runtime_checkable
class StoredSignalProtocol(Protocol):
    """
    Protocol defining the interface for StoredSignal objects.

    This allows the adapter to work with both the real StoredSignal
    from storage/signal_store.py and mock objects in tests.
    """
    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    company_name: Any
    confidence: float
    raw_data: Dict[str, Any]
    detected_at: Any
    company_id: Any


# =============================================================================
# FIELD EXTRACTION CONFIGURATION
# =============================================================================

# Fields to check for description (in priority order)
DESCRIPTION_FIELDS = ["description", "tagline", "summary", "bio", "about"]

# Fields to check for founding date (in priority order)
FOUNDING_DATE_FIELDS = ["founding_date", "registered_date", "incorporation_date", "created_date"]


# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================

def extract_field_provenance(
    signal: StoredSignalProtocol,
    run_id: str,
) -> Dict[str, FieldProvenance]:
    """
    Extract FieldProvenance for each policy-managed field from a StoredSignal.

    Extracts:
    - company_name: from signal.company_name
    - description: from raw_data (description, tagline, summary, etc.)
    - founding_date: from raw_data (founding_date, registered_date, etc.)

    Args:
        signal: StoredSignal object
        run_id: Pipeline run identifier for tracking

    Returns:
        Dict mapping field_name to FieldProvenance
    """
    results: Dict[str, FieldProvenance] = {}
    raw = signal.raw_data or {}

    # Extract company_name
    if signal.company_name and str(signal.company_name).strip():
        results["company_name"] = FieldProvenance(
            value=signal.company_name,
            normalized_value=normalize_company_name(signal.company_name),
            source_key=signal.source_api,
            signal_id=signal.id,
            confidence=signal.confidence,
            detected_at=signal.detected_at,
            evidence_ref=f"signal:{signal.id}",
            run_id=run_id,
        )

    # Extract description (try multiple fields in priority order)
    for desc_field in DESCRIPTION_FIELDS:
        value = raw.get(desc_field)
        if value and str(value).strip():
            results["description"] = FieldProvenance(
                value=value,
                normalized_value=normalize_description(value),
                source_key=signal.source_api,
                signal_id=signal.id,
                confidence=signal.confidence,
                detected_at=signal.detected_at,
                evidence_ref=f"signal:{signal.id}:{desc_field}",
                run_id=run_id,
            )
            break  # Take first found

    # Extract founding_date (try multiple fields in priority order)
    for date_field in FOUNDING_DATE_FIELDS:
        value = raw.get(date_field)
        if value:
            # Store raw value; normalization happens during merge scoring
            normalized = normalize_date(value)
            results["founding_date"] = FieldProvenance(
                value=value,
                normalized_value=normalized,
                source_key=signal.source_api,
                signal_id=signal.id,
                confidence=signal.confidence,
                detected_at=signal.detected_at,
                evidence_ref=f"signal:{signal.id}:{date_field}",
                run_id=run_id,
            )
            break  # Take first found

    return results


def extract_field_provenance_batch(
    signals: List[StoredSignalProtocol],
    run_id: str,
) -> Dict[int, Dict[str, FieldProvenance]]:
    """
    Extract field provenance for a batch of signals.

    Args:
        signals: List of StoredSignal objects
        run_id: Pipeline run identifier

    Returns:
        Dict mapping signal_id to Dict[field_name, FieldProvenance]
    """
    return {
        signal.id: extract_field_provenance(signal, run_id)
        for signal in signals
    }
