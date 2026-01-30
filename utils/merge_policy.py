"""
Merge Policy Module for Phase G Synthesis Enhancement

Defines field-level merge policies that govern how conflicting values
from multiple signals are resolved. Each policy specifies:
- merge rule (how to pick the winner)
- materiality (conflict severity)
- normalization (canonicalization)
- equivalence (tolerance to avoid fake conflicts)
- candidate caps (bounded artifacts)

Usage:
    from utils.merge_policy import FIELD_MERGE_POLICIES, POLICY_VERSION

    policy = FIELD_MERGE_POLICIES["company_name"]
    normalized = policy.normalize_fn(raw_value)
    is_equivalent = policy.equivalence_fn(value1, value2)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from utils.synthesis_types import FieldProvenance, ConflictRecord


# Policy version - included in all decision records for cache invalidation
POLICY_VERSION = "g_v1.0"


# =============================================================================
# SOURCE AUTHORITY CONFIGURATION
# =============================================================================

# Global source authority weights (0.0-1.0)
# Higher values = more authoritative for company data
SOURCE_AUTHORITY: Dict[str, float] = {
    # Official registries (highest authority)
    "companies_house": 0.95,    # UK official registry
    "sec_edgar": 0.90,          # US SEC filings
    "opencorporates": 0.85,     # Global corporate registry aggregator
    # Curated data providers
    "crunchbase": 0.75,         # Curated startup data
    "linkedin": 0.70,           # Professional network
    # Community/self-reported
    "product_hunt": 0.60,       # Founder-submitted
    "github": 0.55,             # Repo names may differ from company names
    "hacker_news": 0.50,        # Community submissions
    # Low authority (scraping, aggregation)
    "domain_whois": 0.45,       # Registration data only
    "job_postings": 0.50,       # From ATS systems
    "arxiv": 0.40,              # Academic affiliations
    "uspto": 0.65,              # Patent filings (official but narrow)
    "news_api": 0.45,           # News articles
    "rss_feeds": 0.40,          # Aggregated RSS
}

# Default authority for unknown sources
DEFAULT_AUTHORITY: float = 0.5


# =============================================================================
# ENUMS
# =============================================================================

class MergeRule(str, Enum):
    """Rules for resolving field conflicts."""
    PICK_HIGHEST_SCORE = "pick_highest_score"
    CONCAT_TOP_K = "concat_top_k"
    # Future expansion (Sprint 1b+):
    # SET_UNION = "set_union"
    # BOOLEAN_ANY = "boolean_any"
    # SUM = "sum"
    # MAX = "max"
    # LATEST = "latest"


class Materiality(str, Enum):
    """Conflict severity levels."""
    CRITICAL = "critical"    # Requires human review or LLM arbitration
    IMPORTANT = "important"  # Logged as warning, auto-resolved
    MINOR = "minor"          # Logged as info, auto-resolved


# =============================================================================
# TYPE ALIASES
# =============================================================================

NormalizeFn = Callable[[Any], Any]
EquivalenceFn = Callable[[Any, Any], bool]


# =============================================================================
# FIELD MERGE POLICY
# =============================================================================

@dataclass(frozen=True)
class FieldMergePolicy:
    """
    Policy governing how a single field is merged across signals.

    Attributes:
        field_name: Name of the field this policy applies to
        merge_rule: How to select the winning value
        materiality: Severity of conflicts for this field
        recency_half_life_days: If set, applies exponential decay to older signals
        source_authority_override: Per-field authority weights (overrides global)
        normalize_fn: Function to canonicalize values for comparison
        equivalence_fn: Function to determine if two values are "close enough"
        max_candidates: Maximum candidates to retain in audit (bounded)
        audit_merge_rule: Alternative rule for audit-only output (e.g., CONCAT_TOP_K)
        audit_top_k: Number of candidates for audit concatenation
        audit_max_chars: Max characters for audit concatenation output
    """
    field_name: str
    merge_rule: MergeRule
    materiality: Materiality

    # Optional recency decay (None = no decay for stable facts)
    recency_half_life_days: Optional[int] = None

    # Optional per-field authority override
    source_authority_override: Optional[Dict[str, float]] = None

    # Normalization and equivalence
    normalize_fn: Optional[NormalizeFn] = None
    equivalence_fn: Optional[EquivalenceFn] = None

    # Candidate retention cap
    max_candidates: int = 5

    # Audit-only behavior
    audit_merge_rule: Optional[MergeRule] = None
    audit_top_k: int = 3
    audit_max_chars: int = 900


# =============================================================================
# NORMALIZATION FUNCTIONS
# =============================================================================

# Company name suffixes to strip (lowercase)
_SUFFIXES = {
    "inc", "inc.", "incorporated",
    "llc", "l.l.c", "l.l.c.",
    "ltd", "ltd.", "limited",
    "corp", "corp.", "corporation",
    "co", "co.", "company",
    "plc", "gmbh", "sarl", "ag", "bv", "oy", "pte", "pty",
}

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_company_name(v: Any) -> str:
    """
    Normalize company name for comparison.

    - Lowercase
    - Remove punctuation
    - Remove common corporate suffixes (Inc, LLC, Ltd, etc.)
    - Collapse whitespace

    Args:
        v: Raw company name value

    Returns:
        Normalized string for comparison
    """
    if v is None:
        return ""
    s = str(v).strip().lower()
    # Remove punctuation
    s = _PUNCT_RE.sub(" ", s)
    # Collapse whitespace
    s = _WS_RE.sub(" ", s).strip()
    # Remove suffixes
    parts = [p for p in s.split(" ") if p and p not in _SUFFIXES]
    return " ".join(parts)


def normalize_description(v: Any) -> str:
    """
    Normalize description for comparison.

    - Strip leading/trailing whitespace
    - Collapse internal whitespace
    - Preserve case (descriptions are display text)

    Args:
        v: Raw description value

    Returns:
        Normalized string
    """
    if v is None:
        return ""
    s = str(v).strip()
    s = _WS_RE.sub(" ", s).strip()
    return s


def normalize_date(v: Any) -> Optional[date]:
    """
    Normalize date value to date object.

    Handles:
    - date objects (returned as-is)
    - datetime objects (date part extracted)
    - ISO format strings ("YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS")

    Args:
        v: Raw date value

    Returns:
        date object or None if unparseable
    """
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.fromisoformat(str(v)).date()
    except (ValueError, TypeError):
        return None


# =============================================================================
# EQUIVALENCE FUNCTIONS
# =============================================================================

def eq_normalized_str(a: Any, b: Any, normalize_fn: Callable[[Any], str]) -> bool:
    """
    Check if two values are equivalent after normalization.

    Args:
        a: First value
        b: Second value
        normalize_fn: Function to normalize values

    Returns:
        True if normalized values are equal
    """
    return normalize_fn(a) == normalize_fn(b)


def dates_within_days(a: Any, b: Any, days: int) -> bool:
    """
    Check if two dates are within a tolerance window.

    Args:
        a: First date value
        b: Second date value
        days: Tolerance in days

    Returns:
        True if dates are within tolerance (or both None)
    """
    da = normalize_date(a)
    db = normalize_date(b)
    if da is None or db is None:
        return False
    return abs((da - db).days) <= days


# =============================================================================
# FIELD MERGE POLICIES
# =============================================================================

FIELD_MERGE_POLICIES: Dict[str, FieldMergePolicy] = {
    "company_name": FieldMergePolicy(
        field_name="company_name",
        merge_rule=MergeRule.PICK_HIGHEST_SCORE,
        materiality=Materiality.CRITICAL,
        recency_half_life_days=None,  # Stable fact: don't penalize old signals
        normalize_fn=normalize_company_name,
        equivalence_fn=lambda a, b: eq_normalized_str(a, b, normalize_company_name),
        max_candidates=5,
    ),
    "description": FieldMergePolicy(
        field_name="description",
        merge_rule=MergeRule.PICK_HIGHEST_SCORE,  # Primary display value
        audit_merge_rule=MergeRule.CONCAT_TOP_K,  # Audit-only context
        audit_top_k=3,
        audit_max_chars=900,
        materiality=Materiality.IMPORTANT,
        recency_half_life_days=None,  # Can add later for freshness preference
        normalize_fn=normalize_description,
        equivalence_fn=lambda a, b: eq_normalized_str(a, b, normalize_description),
        max_candidates=5,
    ),
    "founding_date": FieldMergePolicy(
        field_name="founding_date",
        merge_rule=MergeRule.PICK_HIGHEST_SCORE,
        materiality=Materiality.IMPORTANT,
        recency_half_life_days=None,  # Authority beats recency for facts
        normalize_fn=normalize_date,
        equivalence_fn=lambda a, b: dates_within_days(a, b, days=30),
        max_candidates=5,
    ),
}


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def calculate_effective_score(
    prov: "FieldProvenance",
    policy: FieldMergePolicy,
) -> float:
    """
    Calculate effective merge score for a candidate value.

    The score combines three factors:
    - Source authority (how trustworthy is the source)
    - Signal confidence (how confident was the collector)
    - Recency decay (optional exponential decay for time-sensitive fields)

    Formula: score = authority * confidence * recency_decay

    Args:
        prov: FieldProvenance with source_key, confidence, detected_at
        policy: FieldMergePolicy with optional recency_half_life_days
               and source_authority_override

    Returns:
        Effective score in [0.0, 1.0] range
    """
    # 1. Resolve authority
    authority: float
    if policy.source_authority_override and prov.source_key in policy.source_authority_override:
        authority = policy.source_authority_override[prov.source_key]
    elif prov.source_key in SOURCE_AUTHORITY:
        authority = SOURCE_AUTHORITY[prov.source_key]
    else:
        authority = DEFAULT_AUTHORITY

    # 2. Get confidence (clamp to valid range)
    confidence = max(0.0, min(1.0, prov.confidence))

    # 3. Calculate recency decay
    recency_decay: float = 1.0
    if policy.recency_half_life_days:
        # Compute age in days
        detected_at = prov.detected_at
        # Treat naive datetime as UTC
        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        age_seconds = (now_utc - detected_at).total_seconds()
        age_days = age_seconds / 86400.0
        # Exponential decay: 2^(-age/half_life)
        recency_decay = 2.0 ** (-age_days / policy.recency_half_life_days)

    # 4. Compute final score
    score = authority * confidence * recency_decay

    # 5. Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, score))


def detect_conflicts(
    candidates: list["FieldProvenance"],
    policy: FieldMergePolicy,
) -> Optional["ConflictRecord"]:
    """
    Detect if candidates have conflicting values, with equivalence suppression.

    This function checks if the candidate values represent a genuine conflict.
    It uses the policy's equivalence_fn (if available) to suppress conflicts
    where values differ syntactically but are semantically equivalent.

    Examples of equivalence suppression:
    - "Acme Inc" vs "ACME LLC" → equivalent (both normalize to "acme")
    - "2024-01-15" vs "2024-01-30" → equivalent (within 30-day tolerance for dates)

    Args:
        candidates: List of FieldProvenance objects with candidate values
        policy: FieldMergePolicy defining equivalence rules

    Returns:
        ConflictRecord if conflict detected, None otherwise
    """
    # Import here to avoid circular import
    from utils.synthesis_types import ConflictRecord

    # No conflict possible with 0 or 1 candidates
    if len(candidates) < 2:
        return None

    # Map materiality to severity
    severity_map = {
        Materiality.CRITICAL: "CRITICAL",
        Materiality.IMPORTANT: "WARNING",
        Materiality.MINOR: "INFO",
    }
    severity = severity_map.get(policy.materiality, "INFO")

    # Check if all candidates are equivalent
    # Use equivalence_fn if available, otherwise compare normalized_value
    equivalence_fn = policy.equivalence_fn

    has_conflict = False
    first_candidate = candidates[0]

    for other in candidates[1:]:
        if equivalence_fn:
            # Use policy equivalence function with raw values
            if not equivalence_fn(first_candidate.value, other.value):
                has_conflict = True
                break
        else:
            # Fall back to normalized value comparison
            if first_candidate.normalized_value != other.normalized_value:
                has_conflict = True
                break

    if not has_conflict:
        return None

    # Build conflict record
    return ConflictRecord(
        field_name=policy.field_name,
        candidates=list(candidates),  # Include all candidates
        conflict_type="VALUE_MISMATCH",
        severity=severity,
        resolution=None,  # Resolution happens in merge step
    )
