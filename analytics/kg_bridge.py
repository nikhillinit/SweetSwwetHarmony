"""Bridge between the three evidence taxonomies in the Discovery Engine.

This module is the Phase 0+ schema-flow enhancement for the v50 knowledge
graph. It does NOT modify the v50 schema. It does NOT write to signals.db.
It is a pure-Python reconciliation layer that lets queries move freely
between three taxonomies that already exist in the codebase:

  1. `verification.evidence_families.get_family()` (PRODUCTION authoritative)
     Maps (signal_type, source_api) → one of:
       developer / regulatory / web_presence / hiring / public_buzz / unknown
     This is the taxonomy used by the v51 confidence ledger and the v50
     `kg_nodes(node_type='evidence_family')` seed rows.

  2. `analytics.evidence_ontology.EvidenceClass` (Phase 0 NEW)
     Maps source_api → one of:
       infrastructure_intent / human_transition / hiring_validation /
       ambient_corroboration / analyst_seed / unknown
     This is the taxonomy used by the shadow sidecar and the discovery KPI
     baseline.

  3. v50 `kg_nodes` evidence_family seed nodes (`ef:developer`, etc.)
     Stable string IDs in the production KG. Currently the live KG has
     only the 10 seed nodes (4 sectors + 6 evidence_families) — the
     company graph itself is dormant.

## Reconciliation table

| evidence_family (v50) | EvidenceClass (Phase 0) | kg_node id |
|---|---|---|
| developer            | HUMAN_TRANSITION         | ef:developer |
| regulatory           | INFRASTRUCTURE_INTENT    | ef:regulatory |
| web_presence         | INFRASTRUCTURE_INTENT    | ef:web_presence |
| hiring               | HIRING_VALIDATION        | ef:hiring |
| public_buzz          | AMBIENT_CORROBORATION    | ef:public_buzz |
| unknown              | UNKNOWN                  | ef:unknown |

## Divergences (not collapsible)

These are the points where the two taxonomies do NOT cleanly map:

1. v50 splits infrastructure into `regulatory` (filings) and
   `web_presence` (domains). Phase 0 collapses both into
   INFRASTRUCTURE_INTENT. Direction of conversion: v50 → Phase 0 loses
   information; Phase 0 → v50 cannot recover the split without going
   back to the underlying signal_type.

2. Phase 0 introduces ANALYST_SEED for `manual_seed` and `manual_seed_buzz`.
   v50 has no equivalent — it would classify these as `unknown` because
   they don't appear in the `_SIGNAL_TYPE_FAMILIES` map. This is a real
   schema-flow gap that the live KPI baseline already surfaces (20/118
   promoted companies have manual_seed sources that v50 cannot classify).

3. v50 classifies `github_activity` as `developer`. Phase 0 also classifies
   it as HUMAN_TRANSITION (via the developer→human_transition mapping),
   so this is consistent.

## Why a bridge instead of a unification

The KG construction skill says "ontology first; changing later is
expensive." v50 ships with `evidence_family` baked into:
  - `kg_nodes(node_type='evidence_family')` seed rows
  - the v50 enum CHECK constraint (cannot add new node types without a
    follow-up migration)
  - production `verification.confidence_breakdown.evidence_families` field
  - the SPC `convergence_score` metric calculation

Changing the production taxonomy mid-regret-window is forbidden.
Conversely, the Phase 0 EvidenceClass was designed for the SHADOW pipeline
and intentionally introduces ANALYST_SEED for analytical clarity.

The right answer is a **lossless bridge in one direction (signals →
classification in either system)** plus a **documented best-effort bridge
in the other direction**. This module is that bridge.

## Safety contract

- Zero database writes
- Zero schema migrations
- Read-only when accessed via `lookup_evidence_family_for_signal_row()`,
  which uses the existing `verification.evidence_families.get_family()`
  function (the production source of truth)
- Tested by `analytics/test_kg_bridge.py`
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from analytics.evidence_ontology import EvidenceClass

logger = logging.getLogger(__name__)


# v50 evidence_family node IDs (the seeded `kg_nodes(node_type='evidence_family')` rows)
EVIDENCE_FAMILY_KG_NODE_ID: Mapping[str, str] = {
    "developer": "ef:developer",
    "regulatory": "ef:regulatory",
    "web_presence": "ef:web_presence",
    "hiring": "ef:hiring",
    "public_buzz": "ef:public_buzz",
    "unknown": "ef:unknown",
}


# v50 evidence_family → Phase 0 EvidenceClass (lossy bridge — 6 → 5)
EVIDENCE_FAMILY_TO_CLASS: Mapping[str, EvidenceClass] = {
    "developer": EvidenceClass.HUMAN_TRANSITION,
    "regulatory": EvidenceClass.INFRASTRUCTURE_INTENT,
    "web_presence": EvidenceClass.INFRASTRUCTURE_INTENT,
    "hiring": EvidenceClass.HIRING_VALIDATION,
    "public_buzz": EvidenceClass.AMBIENT_CORROBORATION,
    "unknown": EvidenceClass.UNKNOWN,
}


# Phase 0 EvidenceClass → preferred v50 evidence_family (best-effort, lossy)
# - INFRASTRUCTURE_INTENT collapses to "web_presence" by convention because
#   the live signals.source_api distribution (90 days) shows web_presence
#   sources (domain_whois) are more common than regulatory ones.
# - ANALYST_SEED has no v50 equivalent → "unknown".
# - HIRING_VALIDATION → "hiring" (1:1).
# - HUMAN_TRANSITION → "developer" (closest fit; v50 has no human-transition
#   concept and developer is where github_activity / linkedin sit today).
CLASS_TO_PREFERRED_FAMILY: Mapping[EvidenceClass, str] = {
    EvidenceClass.INFRASTRUCTURE_INTENT: "web_presence",
    EvidenceClass.HUMAN_TRANSITION: "developer",
    EvidenceClass.HIRING_VALIDATION: "hiring",
    EvidenceClass.AMBIENT_CORROBORATION: "public_buzz",
    EvidenceClass.ANALYST_SEED: "unknown",
    EvidenceClass.UNKNOWN: "unknown",
}


def family_to_class(evidence_family: Optional[str]) -> EvidenceClass:
    """Lossy bridge from v50 evidence_family → Phase 0 EvidenceClass.

    Returns EvidenceClass.UNKNOWN for None or unrecognised families
    (rather than raising), so the function is safe to apply over the
    entire signals table without pre-validation.
    """
    if not evidence_family:
        return EvidenceClass.UNKNOWN
    return EVIDENCE_FAMILY_TO_CLASS.get(
        evidence_family.strip().lower(),
        EvidenceClass.UNKNOWN,
    )


def class_to_family(evidence_class: EvidenceClass) -> str:
    """Best-effort bridge from Phase 0 EvidenceClass → v50 evidence_family.

    This direction is lossy: ANALYST_SEED has no v50 equivalent and maps
    to "unknown"; INFRASTRUCTURE_INTENT collapses to "web_presence".
    """
    return CLASS_TO_PREFERRED_FAMILY.get(evidence_class, "unknown")


def family_to_kg_node_id(evidence_family: Optional[str]) -> Optional[str]:
    """Map an evidence_family name to its v50 kg_nodes seed row ID.

    Returns None for unknown families. The returned ID is the literal
    string used in `kg_nodes.id` (e.g. `ef:developer`), so callers can
    use it in JOINs against the live KG without further translation.
    """
    if not evidence_family:
        return None
    return EVIDENCE_FAMILY_KG_NODE_ID.get(evidence_family.strip().lower())


def lookup_evidence_family_for_signal_row(
    signal_type: Optional[str],
    source_api: Optional[str],
) -> str:
    """Production-authoritative classification for a signal row.

    Defers to `verification.evidence_families.get_family()` — that
    module is the v50 source of truth and is referenced by both the
    confidence ledger and the v50 KG. This wrapper exists so the rest
    of the analytics module can call it without taking a hard
    dependency on the verification package's import order.
    """
    if not signal_type or not source_api:
        return "unknown"
    # Imported lazily to avoid pulling the verification package at import
    # time (it has heavier dependencies than analytics needs).
    from verification.evidence_families import get_family

    return get_family(signal_type, source_api)


def class_for_signal_row(
    signal_type: Optional[str],
    source_api: Optional[str],
) -> EvidenceClass:
    """End-to-end: signal row → Phase 0 EvidenceClass via the v50 family.

    This is the function that lets you compute Phase 0 classes using the
    PRODUCTION-AUTHORITATIVE classifier. Two paths produce the result:

      1. The (signal_type, source_api) pair flows through
         verification.evidence_families.get_family() → evidence_family
      2. The evidence_family flows through EVIDENCE_FAMILY_TO_CLASS
         → EvidenceClass

    This is preferred over `analytics.evidence_ontology.classify_source_api()`
    when the signal row also has a `signal_type` field, because the
    production classifier is more precise (it uses the source-api
    overrides table for ambiguous types like funding_event).
    """
    family = lookup_evidence_family_for_signal_row(signal_type, source_api)
    return family_to_class(family)


__all__ = [
    "EVIDENCE_FAMILY_KG_NODE_ID",
    "EVIDENCE_FAMILY_TO_CLASS",
    "CLASS_TO_PREFERRED_FAMILY",
    "family_to_class",
    "class_to_family",
    "family_to_kg_node_id",
    "lookup_evidence_family_for_signal_row",
    "class_for_signal_row",
]
