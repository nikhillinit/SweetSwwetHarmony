"""Tests for analytics.kg_bridge — verifies the three-taxonomy reconciliation."""

from __future__ import annotations

import pytest

from analytics.evidence_ontology import EvidenceClass
from analytics.kg_bridge import (
    CLASS_TO_PREFERRED_FAMILY,
    EVIDENCE_FAMILY_KG_NODE_ID,
    EVIDENCE_FAMILY_TO_CLASS,
    class_for_signal_row,
    class_to_family,
    family_to_class,
    family_to_kg_node_id,
    lookup_evidence_family_for_signal_row,
)


# ---- family_to_class -------------------------------------------------------


def test_family_to_class_developer_is_human_transition():
    assert family_to_class("developer") == EvidenceClass.HUMAN_TRANSITION


def test_family_to_class_regulatory_is_infrastructure_intent():
    assert family_to_class("regulatory") == EvidenceClass.INFRASTRUCTURE_INTENT


def test_family_to_class_web_presence_is_infrastructure_intent():
    """v50 splits infra into regulatory + web_presence; both → INFRASTRUCTURE_INTENT."""
    assert family_to_class("web_presence") == EvidenceClass.INFRASTRUCTURE_INTENT


def test_family_to_class_hiring_is_hiring_validation():
    assert family_to_class("hiring") == EvidenceClass.HIRING_VALIDATION


def test_family_to_class_public_buzz_is_ambient():
    assert family_to_class("public_buzz") == EvidenceClass.AMBIENT_CORROBORATION


def test_family_to_class_unknown():
    assert family_to_class("unknown") == EvidenceClass.UNKNOWN
    assert family_to_class(None) == EvidenceClass.UNKNOWN
    assert family_to_class("") == EvidenceClass.UNKNOWN
    assert family_to_class("not_a_real_family") == EvidenceClass.UNKNOWN


def test_family_to_class_case_insensitive_and_strips():
    assert family_to_class("  DEVELOPER  ") == EvidenceClass.HUMAN_TRANSITION


def test_family_to_class_covers_all_v50_families():
    """Every v50 evidence_family must have a Phase 0 mapping."""
    expected = {"developer", "regulatory", "web_presence", "hiring", "public_buzz", "unknown"}
    assert set(EVIDENCE_FAMILY_TO_CLASS.keys()) == expected


# ---- class_to_family -------------------------------------------------------


def test_class_to_family_round_trip_for_unique_classes():
    """Classes with a 1:1 family map should round-trip cleanly."""
    assert class_to_family(EvidenceClass.HIRING_VALIDATION) == "hiring"
    assert class_to_family(EvidenceClass.AMBIENT_CORROBORATION) == "public_buzz"


def test_class_to_family_infrastructure_collapses_to_web_presence():
    """INFRASTRUCTURE_INTENT is the union of regulatory + web_presence."""
    # The bridge picks web_presence as the preferred reverse mapping
    # (documented in the module docstring)
    assert class_to_family(EvidenceClass.INFRASTRUCTURE_INTENT) == "web_presence"


def test_class_to_family_analyst_seed_is_unknown():
    """ANALYST_SEED has no v50 equivalent and must map to 'unknown'."""
    assert class_to_family(EvidenceClass.ANALYST_SEED) == "unknown"


def test_class_to_family_unknown_is_unknown():
    assert class_to_family(EvidenceClass.UNKNOWN) == "unknown"


def test_class_to_family_covers_all_phase0_classes():
    """Every Phase 0 EvidenceClass must have a v50 family mapping."""
    for cls in EvidenceClass:
        assert cls in CLASS_TO_PREFERRED_FAMILY, f"missing mapping for {cls}"


# ---- family_to_kg_node_id --------------------------------------------------


def test_family_to_kg_node_id_returns_v50_seed_id():
    assert family_to_kg_node_id("developer") == "ef:developer"
    assert family_to_kg_node_id("regulatory") == "ef:regulatory"
    assert family_to_kg_node_id("web_presence") == "ef:web_presence"
    assert family_to_kg_node_id("hiring") == "ef:hiring"
    assert family_to_kg_node_id("public_buzz") == "ef:public_buzz"
    assert family_to_kg_node_id("unknown") == "ef:unknown"


def test_family_to_kg_node_id_returns_none_for_unrecognised():
    assert family_to_kg_node_id("not_a_real_family") is None
    assert family_to_kg_node_id(None) is None
    assert family_to_kg_node_id("") is None


def test_kg_node_ids_match_v50_seeds_in_migration():
    """The v50 migration seeds 6 evidence_family rows. We must reference
    the exact same string IDs so JOINs against kg_nodes work.

    This test pins the contract: if the v50 migration changes its seed
    IDs, this test will fail and the bridge must be updated.
    """
    expected_seeds = {
        "ef:developer",
        "ef:regulatory",
        "ef:web_presence",
        "ef:hiring",
        "ef:public_buzz",
        "ef:unknown",
    }
    assert set(EVIDENCE_FAMILY_KG_NODE_ID.values()) == expected_seeds


# ---- lookup_evidence_family_for_signal_row + class_for_signal_row ----------


def test_lookup_uses_production_classifier():
    """Verify the wrapper actually defers to verification.evidence_families."""
    # github_activity → developer (per _SIGNAL_TYPE_FAMILIES)
    assert lookup_evidence_family_for_signal_row("github_activity", "github") == "developer"
    # incorporation → regulatory
    assert lookup_evidence_family_for_signal_row("incorporation", "sec_edgar") == "regulatory"
    # hacker_news_mention → public_buzz
    assert (
        lookup_evidence_family_for_signal_row("hacker_news_mention", "hacker_news")
        == "public_buzz"
    )


def test_lookup_returns_unknown_for_missing_inputs():
    assert lookup_evidence_family_for_signal_row(None, "github") == "unknown"
    assert lookup_evidence_family_for_signal_row("github_activity", None) == "unknown"
    assert lookup_evidence_family_for_signal_row("", "") == "unknown"


def test_class_for_signal_row_end_to_end():
    """The end-to-end function: signal row → EvidenceClass via v50 family."""
    # github_activity → developer → HUMAN_TRANSITION
    assert (
        class_for_signal_row("github_activity", "github")
        == EvidenceClass.HUMAN_TRANSITION
    )
    # incorporation → regulatory → INFRASTRUCTURE_INTENT
    assert (
        class_for_signal_row("incorporation", "sec_edgar")
        == EvidenceClass.INFRASTRUCTURE_INTENT
    )
    # domain_registration → web_presence → INFRASTRUCTURE_INTENT
    assert (
        class_for_signal_row("domain_registration", "domain_whois")
        == EvidenceClass.INFRASTRUCTURE_INTENT
    )
    # hacker_news_mention → public_buzz → AMBIENT_CORROBORATION
    assert (
        class_for_signal_row("hacker_news_mention", "hacker_news")
        == EvidenceClass.AMBIENT_CORROBORATION
    )


def test_class_for_signal_row_handles_unknown_signal_type():
    """Unknown signal_type → 'unknown' family → UNKNOWN class.

    This is the key invariant: the bridge MUST NOT silently default an
    unknown signal_type to AMBIENT_CORROBORATION (the closest analogue
    to the v50 invariant #4).
    """
    assert (
        class_for_signal_row("not_a_real_signal_type", "hacker_news")
        == EvidenceClass.UNKNOWN
    )
