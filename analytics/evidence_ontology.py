"""Derived evidence ontology — pure functions over existing signal columns.

This module is the Phase 0 (red-team v2 task `p0.1`) implementation of the
evidence-class taxonomy proposed by the Startup Discovery Engine strategy
document. It is implemented as **derived values computed from existing
columns**, not as a schema migration.

The four classes are:
  - INFRASTRUCTURE_INTENT: domain registrations, filings, certificate transparency
  - HUMAN_TRANSITION:      founder departures, repo activity, profile changes
  - HIRING_VALIDATION:     ATS postings (greenhouse, lever)
  - AMBIENT_CORROBORATION: news, HN, ArXiv, RSS, PH — corroborate but never sole-qualify

Critical contract:
  - This module performs ZERO database writes.
  - This module is NOT imported by any production pipeline path.
  - Tier-2 evidence-bundle rules computed here run only in the shadow sidecar
    until Phase 2 shadow ladder evaluation completes.

See: `artifacts/red-team-execution/phase0/evidence-ontology.md`
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, List, Mapping, Optional, Sequence


class EvidenceClass(str, Enum):
    """Derived evidence classes (do not persist; recompute as needed).

    ANALYST_SEED is a separate class for manually-curated entries (e.g.
    `manual_seed`, `manual_seed_buzz`). These are NOT discovery signals —
    they are the analyst's prior knowledge entered into the pipeline. They
    are non-ambient (so they pass the U5 demotion check) but they should
    not count as system-discovered evidence.
    """

    INFRASTRUCTURE_INTENT = "infrastructure_intent"
    HUMAN_TRANSITION = "human_transition"
    HIRING_VALIDATION = "hiring_validation"
    AMBIENT_CORROBORATION = "ambient_corroboration"
    ANALYST_SEED = "analyst_seed"
    UNKNOWN = "unknown"


# Source-API → EvidenceClass mapping. Keep this table the single source of
# truth for class derivation. Add new collectors here ONLY after they ship in
# the shadow sidecar; production collector writes are not required for a
# source to be classified (the function returns UNKNOWN for unknown sources,
# not an exception).
_SOURCE_API_TO_CLASS: Mapping[str, EvidenceClass] = {
    # Infrastructure intent
    "domain_whois": EvidenceClass.INFRASTRUCTURE_INTENT,
    "sec_edgar": EvidenceClass.INFRASTRUCTURE_INTENT,
    "companies_house": EvidenceClass.INFRASTRUCTURE_INTENT,
    "opencorporates": EvidenceClass.INFRASTRUCTURE_INTENT,
    # Future shadow-only collectors (analytics/shadow_collectors/*)
    "shadow_ct_log": EvidenceClass.INFRASTRUCTURE_INTENT,
    "shadow_dns_fingerprint": EvidenceClass.INFRASTRUCTURE_INTENT,
    # Human transition
    "linkedin": EvidenceClass.HUMAN_TRANSITION,
    "github_activity": EvidenceClass.HUMAN_TRANSITION,
    "shadow_gh_negative_space": EvidenceClass.HUMAN_TRANSITION,
    # Hiring validation — production uses per-ATS source names, not the
    # generic "job_postings". Map all ATS sub-sources to the same class.
    "job_postings": EvidenceClass.HIRING_VALIDATION,
    "greenhouse_jobs": EvidenceClass.HIRING_VALIDATION,
    "lever_jobs": EvidenceClass.HIRING_VALIDATION,
    "ashby_jobs": EvidenceClass.HIRING_VALIDATION,
    # Ambient corroboration (NEVER sole-qualifies tier 2)
    "hacker_news": EvidenceClass.AMBIENT_CORROBORATION,
    "arxiv": EvidenceClass.AMBIENT_CORROBORATION,
    "rss_feeds": EvidenceClass.AMBIENT_CORROBORATION,
    "news_api": EvidenceClass.AMBIENT_CORROBORATION,
    "product_hunt": EvidenceClass.AMBIENT_CORROBORATION,
    "uspto": EvidenceClass.AMBIENT_CORROBORATION,
    "crunchbase": EvidenceClass.AMBIENT_CORROBORATION,
    "github": EvidenceClass.AMBIENT_CORROBORATION,
    # Analyst seed — manual curation, NOT discovery
    "manual_seed": EvidenceClass.ANALYST_SEED,
    "manual_seed_buzz": EvidenceClass.ANALYST_SEED,
}


# Sources that NEVER sole-qualify a company-episode for the shadow tier-2 surface.
# This is the U5 demotion: HN/ArXiv/RSS corroborate but do not initiate.
AMBIENT_ONLY_SOURCES = frozenset(
    src
    for src, cls in _SOURCE_API_TO_CLASS.items()
    if cls == EvidenceClass.AMBIENT_CORROBORATION
)


def classify_source_api(source_api: Optional[str]) -> EvidenceClass:
    """Map a single signals.source_api value to its EvidenceClass.

    Returns EvidenceClass.UNKNOWN for unrecognised sources rather than raising,
    so the function is safe to apply over the entire signals table without
    pre-validation.
    """
    if not source_api:
        return EvidenceClass.UNKNOWN
    return _SOURCE_API_TO_CLASS.get(source_api.strip().lower(), EvidenceClass.UNKNOWN)


def classify_signal_row(row: Mapping[str, object]) -> EvidenceClass:
    """Classify a row from `signals` (or any dict with `source_api`).

    Accepts dict-like rows so it can be applied to aiosqlite Row objects,
    plain dicts, or pandas-style records.
    """
    return classify_source_api(row.get("source_api"))  # type: ignore[arg-type]


@dataclass(frozen=True)
class CompanyEvidenceBundle:
    """Aggregated evidence for one company-episode (derived view).

    Computed by `aggregate_company_evidence()`. This is a value object — it
    has no persistent identity and is not stored. It exists only for shadow
    sidecar evaluation.
    """

    company_id: str
    classes_present: frozenset
    distinct_class_count: int
    has_non_ambient_class: bool
    earliest_signal_at: Optional[datetime]
    latest_signal_at: Optional[datetime]
    source_api_counts: Mapping[str, int]


def aggregate_company_evidence(
    company_id: str,
    signal_rows: Iterable[Mapping[str, object]],
) -> CompanyEvidenceBundle:
    """Aggregate signal rows for one company into a CompanyEvidenceBundle.

    `signal_rows` is any iterable of dict-like rows that contain at least
    `source_api` and `detected_at` keys. The function tolerates missing
    `detected_at` (treats as None) and unknown `source_api` (treats as
    UNKNOWN class, not counted toward classes_present).

    No DB access, no I/O.
    """
    classes: set = set()
    source_counts: dict = {}
    earliest: Optional[datetime] = None
    latest: Optional[datetime] = None

    for row in signal_rows:
        source = row.get("source_api")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1

        cls = classify_signal_row(row)
        if cls != EvidenceClass.UNKNOWN:
            classes.add(cls)

        ts_raw = row.get("detected_at") or row.get("created_at")
        ts = _parse_iso(ts_raw) if isinstance(ts_raw, str) else None
        if ts is not None:
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts

    has_non_ambient = any(c != EvidenceClass.AMBIENT_CORROBORATION for c in classes)

    return CompanyEvidenceBundle(
        company_id=company_id,
        classes_present=frozenset(classes),
        distinct_class_count=len(classes),
        has_non_ambient_class=has_non_ambient,
        earliest_signal_at=earliest,
        latest_signal_at=latest,
        source_api_counts=dict(source_counts),
    )


# ---- Tier rules (P3 disambiguation) ----------------------------------------


class ShadowTier(str, Enum):
    """Shadow-only tiers from the hybrid evidence-bundle rule (red-team v2 P3).

    NONE      — does not qualify for any shadow surface
    TIER_1    — internal watchlist (engine-only, never analyst-visible)
    TIER_2    — analyst shadow queue (sidecar UI / export only, NOT Notion)
    TIER_3    — eligible for Notion push IF AND ONLY IF the live thesis filter
                does not reject. Tier-3 evaluation does NOT happen in this
                module; it is the responsibility of the live processing stage.
    """

    NONE = "none"
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


def evaluate_shadow_tier(
    bundle: CompanyEvidenceBundle,
    *,
    require_two_classes_for_tier2: bool = True,
) -> ShadowTier:
    """Compute the shadow tier for a company-episode.

    Tier rules (red-team v2 U8 + P3):
      - TIER_NONE: no signals; only AMBIENT_CORROBORATION; only ANALYST_SEED;
                   or only (AMBIENT + ANALYST_SEED) — ANALYST_SEED is not
                   discovery and ambient cannot sole-qualify (U5)
      - TIER_1:    exactly one *discovery* class
                   (INFRASTRUCTURE_INTENT, HUMAN_TRANSITION, HIRING_VALIDATION)
      - TIER_2:    two or more distinct discovery classes
      - TIER_3:    NOT computed here — tier-3 promotion to Notion is gated by
                   the live `LLM_THESIS_MODE=active` filter and is the
                   responsibility of `workflows/pipeline.py`. This function
                   intentionally returns at most TIER_2 to enforce the
                   safety contract that the shadow sidecar cannot push to
                   Notion under any circumstance.

    `require_two_classes_for_tier2` is exposed so Phase 1 can A/B-test the
    relaxation to "one ultra-strong infrastructure bundle" without forking
    this function.
    """
    if bundle.distinct_class_count == 0:
        return ShadowTier.NONE

    discovery_classes = {
        c
        for c in bundle.classes_present
        if c
        not in (
            EvidenceClass.AMBIENT_CORROBORATION,
            EvidenceClass.ANALYST_SEED,
        )
    }
    n_discovery = len(discovery_classes)

    if n_discovery == 0:
        # Only ambient corroboration and/or analyst seeds → never qualifies.
        # Ambient can't sole-qualify (U5); analyst seeds are priors, not
        # discoveries.
        return ShadowTier.NONE

    if require_two_classes_for_tier2:
        if n_discovery >= 2:
            return ShadowTier.TIER_2
        return ShadowTier.TIER_1

    # Relaxed mode (Phase 1 A/B): one discovery class is enough for tier 2.
    return ShadowTier.TIER_2


# ---- Helpers ---------------------------------------------------------------


def _parse_iso(ts: object) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string. Tolerates trailing Z and naive."""
    if not isinstance(ts, str):
        return None
    candidate = ts.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "EvidenceClass",
    "CompanyEvidenceBundle",
    "ShadowTier",
    "AMBIENT_ONLY_SOURCES",
    "classify_source_api",
    "classify_signal_row",
    "aggregate_company_evidence",
    "evaluate_shadow_tier",
]
