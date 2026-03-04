"""Tests for config-driven company-name policy decisions."""

from __future__ import annotations

from utils.company_name_policy import (
    Candidate,
    CanonicalState,
    decide_promote_candidate_review,
    decide_write_candidate,
    decide_write_canonical_auto,
    load_company_name_policy,
    normalize_company_name,
    validate_company_name_candidate,
)


def test_load_policy_defaults():
    policy = load_company_name_policy()
    assert policy["policy_id"] == "COMPANY_NAME_WRITE_POLICY"
    assert policy["default_deny"] is True
    assert "regex" in policy["sources"]


def test_normalize_company_name_applies_suffix_standardization():
    policy = load_company_name_policy()
    normalized = normalize_company_name("Acme, Incorporated", policy)
    assert normalized == "acme inc"


def test_validate_candidate_rejects_generic_terms_only():
    policy = load_company_name_policy()
    valid, reason = validate_company_name_candidate("Culinary Program", policy)
    assert valid is False
    assert reason == "GENERIC_TERMS_ONLY"


def test_decide_auto_write_allows_regex_fill_when_empty():
    policy = load_company_name_policy()
    decision = decide_write_canonical_auto(
        actor_id="system:bulk_company_name_backfill_v1",
        existing=CanonicalState(name=None, normalized=None, source=None),
        candidate=Candidate(
            name="FreshBowl",
            source="regex",
            source_version="regex_ruleset_2026-03-03",
        ),
        policy=policy,
    )
    assert decision.allowed is True
    assert decision.action == "write_canonical"
    assert decision.write_payload is not None
    assert decision.write_payload["company_name"] == "FreshBowl"
    assert decision.write_payload["company_name_source"] == "regex"


def test_decide_auto_write_blocks_ner_source():
    policy = load_company_name_policy()
    decision = decide_write_canonical_auto(
        actor_id="system:bulk_company_name_backfill_v1",
        existing=CanonicalState(name=None, normalized=None, source=None),
        candidate=Candidate(
            name="Acme Candidate",
            source="ner",
            source_version="en_core_web_sm@3.7.0",
        ),
        policy=policy,
    )
    assert decision.allowed is False
    assert decision.reason in {
        "CANDIDATE_SOURCE_NOT_ALLOWED_FOR_ACTOR",
        "CANDIDATE_SOURCE_NOT_ALLOWED_FOR_AUTO_FILL",
        "SOURCE_AUTO_WRITE_NOT_ALLOWED",
    }


def test_decide_auto_write_never_overwrites_non_empty():
    policy = load_company_name_policy()
    decision = decide_write_canonical_auto(
        actor_id="system:bulk_company_name_backfill_v1",
        existing=CanonicalState(name="ExistingCo", normalized="existingco", source="regex"),
        candidate=Candidate(
            name="NewCo",
            source="regex",
            source_version="regex_ruleset_2026-03-03",
        ),
        policy=policy,
    )
    assert decision.allowed is False
    assert decision.reason == "EXISTING_NON_EMPTY_NO_OVERWRITE"


def test_decide_write_candidate_allows_ner_candidate_actor():
    policy = load_company_name_policy()
    decision = decide_write_candidate(
        actor_id="system:ner_candidate_extraction_v1",
        candidate=Candidate(
            name="Richtech Robotics Inc.",
            source="ner",
            source_version="en_core_web_sm@3.7.0",
        ),
        policy=policy,
    )
    assert decision.allowed is True
    assert decision.action == "write_candidate"
    assert decision.write_payload is not None
    assert decision.write_payload["candidate_source"] == "ner"


def test_reviewer_promotion_requires_reason_and_sets_ner_approved():
    policy = load_company_name_policy()
    candidate = Candidate(
        name="Wonderbelly",
        source="ner",
        source_version="en_core_web_sm@3.7.0",
    )

    denied = decide_promote_candidate_review(
        actor_id="reviewer:alice",
        existing=CanonicalState(name=None, normalized=None, source=None),
        candidate=candidate,
        reason="",
        policy=policy,
    )
    assert denied.allowed is False
    assert denied.reason == "REASON_REQUIRED"

    approved = decide_promote_candidate_review(
        actor_id="reviewer:alice",
        existing=CanonicalState(name=None, normalized=None, source=None),
        candidate=candidate,
        reason="Headline subject confirmed in body text",
        policy=policy,
    )
    assert approved.allowed is True
    assert approved.action == "write_canonical"
    assert approved.write_payload is not None
    assert approved.write_payload["company_name_source"] == "ner_approved"
