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

    # Conflict tracking
    conflict_flags: List[ConflictFlag] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        """Returns True if any conflicts were detected during consolidation."""
        return len(self.conflict_flags) > 0

    @property
    def signal_count(self) -> int:
        """Number of signals that contributed to this consolidation."""
        return len(self.contributing_signal_ids)


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
