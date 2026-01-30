"""
Signal Consolidation for Discovery Engine

Merges multiple signals for the same company into a unified ConsolidatedSignal
with field-level merge strategies, conflict detection, and provenance tracking.

Usage:
    consolidator = SignalConsolidator()
    consolidated = consolidator.consolidate(signals)

    if consolidated.has_conflicts:
        # Route to human review
        pass
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, TYPE_CHECKING

# Phase G imports
from utils.merge_policy import (
    POLICY_VERSION,
    FIELD_MERGE_POLICIES,
    FieldMergePolicy,
    calculate_effective_score,
    SOURCE_AUTHORITY,
    DEFAULT_AUTHORITY,
)
from utils.synthesis_types import FieldChoice, FieldProvenance, ConflictRecord

if TYPE_CHECKING:
    from storage.signal_store import StoredSignal

logger = logging.getLogger(__name__)

# Source priority for company_name selection (lower = higher priority)
SOURCE_PRIORITY = {
    "companies_house": 1,  # Official UK registry
    "sec_edgar": 2,        # SEC filings
    "crunchbase": 3,       # Curated startup DB
    "linkedin": 4,         # Professional network
    "product_hunt": 5,     # Product launches
    "hacker_news": 6,      # Tech news
    "github": 7,           # May be repo name
    "domain_whois": 8,     # Registrant info
    "arxiv": 9,            # Research papers
    "uspto": 10,           # Patent filings
}

DEFAULT_PRIORITY = 99  # For unknown sources

# Fields to extract descriptions from (in order of preference)
DESCRIPTION_FIELDS = ["description", "tagline", "summary", "bio", "about"]

# Social proof fields to aggregate (summed across signals)
SOCIAL_PROOF_FIELDS = [
    "stars", "recent_stars", "forks", "watchers",  # GitHub
    "votes", "upvotes", "comments",  # Product Hunt
    "followers", "connections",  # LinkedIn
    "mentions",  # Hacker News
]

# Founding date fields to extract from raw_data (checked in order)
FOUNDING_DATE_FIELDS = ["founding_date", "registered_date", "incorporation_date", "created_date"]


@dataclass
class ConflictFlag:
    """Indicates a conflict during consolidation."""
    field: str  # Which field has conflicting values
    values: List[str]  # The conflicting values found
    severity: str = "warning"  # "warning" or "error"

    def __str__(self) -> str:
        return f"{self.field}: {self.values} ({self.severity})"


@dataclass
class ConsolidatedSignal:
    """
    Result of consolidating multiple StoredSignals for the same company.

    Preserves provenance via contributing_signal_ids so original signals
    can be traced back.

    Phase G Extensions:
    - policy_version: Version of merge policies used
    - field_choices: Winner + decision reason for each field
    - field_candidates: Bounded list of candidates per field
    - field_conflicts: Material conflicts detected per field
    - raw_signal_bundle: Full signal data for audit
    - to_public(): API-safe stable output
    - to_audit(): Full provenance/conflicts for debugging
    """
    # Identity
    canonical_key: str
    company_name: str

    # Provenance (which signals contributed to this)
    contributing_signal_ids: List[int]
    signal_types: List[str]
    source_apis: List[str]

    # Aggregated metrics
    aggregated_confidence: float
    earliest_detected_at: datetime
    latest_detected_at: datetime

    # Optional aggregated fields
    descriptions: List[str] = field(default_factory=list)
    why_now_parts: List[str] = field(default_factory=list)
    founding_date: Optional[datetime] = None
    social_proof: Dict[str, int] = field(default_factory=dict)  # e.g., {"stars": 100, "votes": 50}

    # Raw data aggregation (merged from all signals)
    merged_raw_data: Dict[str, Any] = field(default_factory=dict)

    # Conflict tracking (legacy)
    conflict_flags: List[ConflictFlag] = field(default_factory=list)

    # Phase G: Audit artifacts
    policy_version: str = POLICY_VERSION
    field_choices: Dict[str, FieldChoice] = field(default_factory=dict)
    field_candidates: Dict[str, List[FieldProvenance]] = field(default_factory=dict)
    field_conflicts: Dict[str, ConflictRecord] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    field_audit_context: Dict[str, str] = field(default_factory=dict)
    raw_signal_bundle: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        """Returns True if any conflicts were detected during consolidation."""
        return len(self.conflict_flags) > 0 or len(self.field_conflicts) > 0

    @property
    def signal_count(self) -> int:
        """Number of signals that contributed to this consolidation."""
        return len(self.contributing_signal_ids)

    def get_primary_description(self) -> str:
        """
        Safe accessor for primary description.

        Returns the first description or empty string if none available.
        Handles None values in the list gracefully.
        """
        if not self.descriptions:
            return ""
        first = self.descriptions[0]
        return first if first else ""

    def to_public(self) -> Dict[str, Any]:
        """
        Return API-safe stable output shape.

        This is the "clean" view for downstream product surfaces.
        Never includes internal audit fields.
        """
        return {
            "canonical_key": self.canonical_key,
            "company_name": self.company_name,
            "description": self.get_primary_description(),
            "confidence": self.aggregated_confidence,
            "signal_count": self.signal_count,
            "signal_types": list(self.signal_types),
            "source_apis": list(self.source_apis),
            "founding_date": self.founding_date.isoformat() if self.founding_date else None,
            "social_proof": dict(self.social_proof) if self.social_proof else {},
            "why_now": self.why_now_parts[:3] if self.why_now_parts else [],
            "has_conflicts": self.has_conflicts,
        }

    def to_audit(self) -> Dict[str, Any]:
        """
        Return full provenance/conflicts/candidates for debugging.

        This is the "debug" view for calibration and engineering iteration.
        Includes all internal audit fields.
        """
        # Serialize field_choices
        field_choices_dict = {}
        for field_name, choice in self.field_choices.items():
            field_choices_dict[field_name] = choice.to_dict()

        # Serialize field_candidates
        field_candidates_dict = {}
        for field_name, candidates in self.field_candidates.items():
            field_candidates_dict[field_name] = [c.to_dict() for c in candidates]

        # Serialize field_conflicts
        field_conflicts_dict = {}
        for field_name, conflict in self.field_conflicts.items():
            field_conflicts_dict[field_name] = conflict.to_dict()

        # Serialize conflict_flags (legacy)
        conflict_flags_list = [
            {"field": cf.field, "values": cf.values, "severity": cf.severity}
            for cf in self.conflict_flags
        ]

        return {
            # Public fields
            "canonical_key": self.canonical_key,
            "company_name": self.company_name,
            "description": self.get_primary_description(),
            "confidence": self.aggregated_confidence,
            "signal_count": self.signal_count,
            # Audit fields
            "policy_version": self.policy_version,
            "contributing_signal_ids": list(self.contributing_signal_ids),
            "signal_types": list(self.signal_types),
            "source_apis": list(self.source_apis),
            "earliest_detected_at": self.earliest_detected_at.isoformat() if self.earliest_detected_at else None,
            "latest_detected_at": self.latest_detected_at.isoformat() if self.latest_detected_at else None,
            "descriptions": list(self.descriptions),
            "why_now_parts": list(self.why_now_parts),
            "founding_date": self.founding_date.isoformat() if self.founding_date else None,
            "social_proof": dict(self.social_proof) if self.social_proof else {},
            # Phase G audit artifacts
            "field_choices": field_choices_dict,
            "field_candidates": field_candidates_dict,
            "field_conflicts": field_conflicts_dict,
            "field_confidence": dict(self.field_confidence),
            "field_audit_context": dict(self.field_audit_context),
            # Legacy
            "conflict_flags": conflict_flags_list,
            "raw_signal_bundle": list(self.raw_signal_bundle),
        }


class SignalConsolidator:
    """
    Consolidates multiple StoredSignals for the same company.

    Applies field-level merge strategies:
    - company_name: source priority (Companies House > SEC > etc.)
    - confidence: weighted average by source priority
    - descriptions: concatenate unique values
    - social_proof: aggregate (sum stars, votes, etc.)
    """

    def __init__(self):
        self.source_priority = SOURCE_PRIORITY

    def consolidate(self, signals: List["StoredSignal"]) -> ConsolidatedSignal:
        """
        Consolidate multiple signals into a single ConsolidatedSignal.

        Args:
            signals: List of StoredSignal objects for the same canonical_key

        Returns:
            ConsolidatedSignal with merged fields and conflict flags
        """
        if not signals:
            raise ValueError("Cannot consolidate empty signal list")

        # Sort by source priority
        sorted_signals = sorted(
            signals,
            key=lambda s: self.source_priority.get(s.source_api, DEFAULT_PRIORITY)
        )

        # Select company_name from highest priority source that has it
        company_name = self._select_company_name(sorted_signals)

        # Detect conflicts
        conflict_flags = self._detect_conflicts(signals)

        # Basic aggregation
        canonical_key = signals[0].canonical_key
        contributing_ids = [s.id for s in signals]
        signal_types = list(set(s.signal_type for s in signals))
        source_apis = list(set(s.source_api for s in signals))

        # Confidence: weighted average by source priority
        avg_confidence = self._calculate_weighted_confidence(signals)

        # Time bounds
        earliest = min(s.detected_at for s in signals)
        latest = max(s.detected_at for s in signals)

        # Aggregate descriptions from raw_data
        descriptions = self._aggregate_descriptions(signals)

        # Aggregate social proof metrics
        social_proof = self._aggregate_social_proof(signals)

        # Extract founding date
        founding_date = self._extract_founding_date(signals)

        # Aggregate why_now reasons
        why_now_parts = self._aggregate_why_now(signals)

        return ConsolidatedSignal(
            canonical_key=canonical_key,
            company_name=company_name,
            contributing_signal_ids=contributing_ids,
            signal_types=signal_types,
            source_apis=source_apis,
            aggregated_confidence=avg_confidence,
            earliest_detected_at=earliest,
            latest_detected_at=latest,
            descriptions=descriptions,
            why_now_parts=why_now_parts,
            founding_date=founding_date,
            social_proof=social_proof,
            conflict_flags=conflict_flags,
        )

    def _select_company_name(self, sorted_signals: List["StoredSignal"]) -> str:
        """Select company_name from highest priority source that has it."""
        for signal in sorted_signals:
            if signal.company_name and signal.company_name.strip():
                return signal.company_name.strip()
        return "Unknown Company"

    def _calculate_weighted_confidence(self, signals: List["StoredSignal"]) -> float:
        """
        Calculate weighted average confidence.

        Weight is inversely proportional to source priority (lower priority number = higher weight).
        This ensures high-quality sources like Companies House and SEC EDGAR
        have more influence on the final confidence score.
        """
        if len(signals) == 1:
            return signals[0].confidence

        total_weight = 0.0
        weighted_sum = 0.0

        for signal in signals:
            # Invert priority: priority 1 -> weight 10, priority 10 -> weight 1
            priority = self.source_priority.get(signal.source_api, DEFAULT_PRIORITY)
            weight = 11 - min(priority, 10)  # Clamp priority to max 10

            weighted_sum += signal.confidence * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _detect_conflicts(self, signals: List["StoredSignal"]) -> List[ConflictFlag]:
        """Detect conflicts between signal field values."""
        conflicts = []

        # Check company_name conflicts
        company_names = set()
        for signal in signals:
            if signal.company_name and signal.company_name.strip():
                company_names.add(signal.company_name.strip())

        if len(company_names) > 1:
            conflicts.append(ConflictFlag(
                field="company_name",
                values=sorted(company_names),
                severity="warning",
            ))
            logger.warning(
                f"Conflict detected for {signals[0].canonical_key}: "
                f"multiple company names: {company_names}"
            )

        return conflicts

    def _aggregate_descriptions(self, signals: List["StoredSignal"]) -> List[str]:
        """Extract and deduplicate descriptions from signal raw_data."""
        seen = set()
        descriptions = []

        for signal in signals:
            raw_data = signal.raw_data or {}
            for field in DESCRIPTION_FIELDS:
                value = raw_data.get(field)
                if isinstance(value, str) and value.strip():
                    normalized = value.strip()
                    if normalized not in seen:
                        seen.add(normalized)
                        descriptions.append(normalized)

        return descriptions

    def _aggregate_social_proof(self, signals: List["StoredSignal"]) -> Dict[str, int]:
        """Aggregate social proof metrics from signal raw_data."""
        totals: Dict[str, int] = {}
        for signal in signals:
            raw_data = signal.raw_data or {}
            for field in SOCIAL_PROOF_FIELDS:
                value = raw_data.get(field)
                if isinstance(value, (int, float)) and value > 0:
                    totals[field] = totals.get(field, 0) + int(value)
        return totals

    def _extract_founding_date(self, signals: List["StoredSignal"]) -> Optional[datetime]:
        """Extract earliest founding/registration date from signals."""
        dates = []
        for signal in signals:
            raw_data = signal.raw_data or {}
            for field in FOUNDING_DATE_FIELDS:
                value = raw_data.get(field)
                if value:
                    parsed = self._parse_date(value)
                    if parsed:
                        dates.append(parsed)
        return min(dates) if dates else None

    def _parse_date(self, value: Any) -> Optional[datetime]:
        """Parse a date from various formats."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None

    def _aggregate_why_now(self, signals: List["StoredSignal"]) -> List[str]:
        """Extract why_now reasons from signal raw_data."""
        seen = set()
        parts = []
        for signal in signals:
            raw_data = signal.raw_data or {}
            why_now = raw_data.get("why_now")
            if isinstance(why_now, str) and why_now.strip():
                normalized = why_now.strip()
                if normalized not in seen:
                    seen.add(normalized)
                    parts.append(normalized)
        # Fallback if no explicit why_now found
        if not parts:
            signal_types = list(set(s.signal_type for s in signals))
            parts.append(f"Detected via {', '.join(signal_types)}")
        return parts

    def pick_highest_score(
        self,
        signals: List["StoredSignal"],
        policy: FieldMergePolicy,
        field_name: str,
    ) -> tuple["StoredSignal", float]:
        """
        Select the signal with highest effective score for a field.

        Uses calculate_effective_score from merge_policy to compute scores,
        then applies deterministic tie-breakers:
        1. Higher effective score
        2. Higher source authority
        3. More recent detected_at
        4. Lexical ordering on normalized value (stable fallback)

        Args:
            signals: List of StoredSignal objects
            policy: FieldMergePolicy for the field
            field_name: Name of the field being merged (e.g., "company_name")

        Returns:
            Tuple of (winning_signal, winning_score)

        Raises:
            ValueError: If no signals provided or no valid candidates
        """
        if not signals:
            raise ValueError("No signals provided for pick_highest_score")

        # Build candidates with scores
        candidates: list[tuple["StoredSignal", FieldProvenance, float]] = []

        # Get field extractor and normalizer
        normalize_fn = policy.normalize_fn
        if normalize_fn is None:
            # Default normalization: strip and lowercase strings
            normalize_fn = lambda v: str(v).strip().lower() if v else ""

        for signal in signals:
            # Extract field value
            value = self._get_field_value(signal, field_name)
            if not value or (isinstance(value, str) and not value.strip()):
                continue  # Skip empty values

            # Normalize value
            normalized = normalize_fn(value)

            # Create provenance
            prov = FieldProvenance(
                value=value,
                normalized_value=normalized,
                source_key=signal.source_api,
                signal_id=signal.id,
                confidence=signal.confidence,
                detected_at=signal.detected_at,
                evidence_ref=f"signal:{signal.id}",
            )

            # Calculate effective score
            score = calculate_effective_score(prov, policy)
            candidates.append((signal, prov, score))

        if not candidates:
            raise ValueError(f"No candidates with valid {field_name} values")

        # Sort by deterministic tie-breaker chain:
        # 1. Higher score (descending)
        # 2. Higher authority (descending)
        # 3. More recent detected_at (descending)
        # 4. Lexical on normalized_value (ascending, for stability)
        def sort_key(item: tuple["StoredSignal", FieldProvenance, float]):
            signal, prov, score = item
            # Resolve authority for tie-breaker
            if policy.source_authority_override and prov.source_key in policy.source_authority_override:
                authority = policy.source_authority_override[prov.source_key]
            elif prov.source_key in SOURCE_AUTHORITY:
                authority = SOURCE_AUTHORITY[prov.source_key]
            else:
                authority = DEFAULT_AUTHORITY

            # Return tuple for multi-level sort
            # Negate score and authority for descending order
            # Use timestamp for recency (larger = more recent)
            return (
                -score,
                -authority,
                -prov.detected_at.timestamp() if prov.detected_at else 0,
                str(prov.normalized_value),  # Ascending lexical for final stability
            )

        candidates.sort(key=sort_key)
        winner_signal, winner_prov, winner_score = candidates[0]
        return winner_signal, winner_score

    def _get_field_value(self, signal: "StoredSignal", field_name: str) -> Any:
        """Extract a field value from a StoredSignal."""
        if field_name == "company_name":
            return signal.company_name
        elif field_name == "description":
            raw_data = signal.raw_data or {}
            for desc_field in DESCRIPTION_FIELDS:
                value = raw_data.get(desc_field)
                if value:
                    return value
            return None
        elif field_name == "founding_date":
            raw_data = signal.raw_data or {}
            for date_field in FOUNDING_DATE_FIELDS:
                value = raw_data.get(date_field)
                if value:
                    return value
            return None
        else:
            # Generic: try attribute then raw_data
            if hasattr(signal, field_name):
                return getattr(signal, field_name)
            return (signal.raw_data or {}).get(field_name)
