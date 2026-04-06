"""Tests for analytics.evidence_ontology — pure functions, no DB."""

from __future__ import annotations

from analytics.evidence_ontology import (
    AMBIENT_ONLY_SOURCES,
    CompanyEvidenceBundle,
    EvidenceClass,
    ShadowTier,
    aggregate_company_evidence,
    classify_signal_row,
    classify_source_api,
    evaluate_shadow_tier,
)


# ---- classify_source_api ---------------------------------------------------


def test_classify_known_infrastructure_intent():
    assert classify_source_api("domain_whois") == EvidenceClass.INFRASTRUCTURE_INTENT
    assert classify_source_api("sec_edgar") == EvidenceClass.INFRASTRUCTURE_INTENT
    assert classify_source_api("companies_house") == EvidenceClass.INFRASTRUCTURE_INTENT


def test_classify_known_human_transition():
    assert classify_source_api("linkedin") == EvidenceClass.HUMAN_TRANSITION
    assert classify_source_api("github_activity") == EvidenceClass.HUMAN_TRANSITION


def test_classify_known_hiring_validation():
    assert classify_source_api("job_postings") == EvidenceClass.HIRING_VALIDATION


def test_classify_ambient():
    for src in ("hacker_news", "arxiv", "rss_feeds", "news_api", "product_hunt"):
        assert classify_source_api(src) == EvidenceClass.AMBIENT_CORROBORATION


def test_classify_unknown_returns_unknown_not_raises():
    assert classify_source_api("not_a_real_source") == EvidenceClass.UNKNOWN
    assert classify_source_api(None) == EvidenceClass.UNKNOWN
    assert classify_source_api("") == EvidenceClass.UNKNOWN


def test_classify_is_case_insensitive_and_strips():
    assert classify_source_api("  SEC_EDGAR  ") == EvidenceClass.INFRASTRUCTURE_INTENT


def test_classify_signal_row_dict():
    row = {"source_api": "domain_whois", "company_name": "x"}
    assert classify_signal_row(row) == EvidenceClass.INFRASTRUCTURE_INTENT


def test_ambient_sources_set_matches_table():
    # Sanity check the materialised AMBIENT_ONLY_SOURCES set
    assert "hacker_news" in AMBIENT_ONLY_SOURCES
    assert "arxiv" in AMBIENT_ONLY_SOURCES
    assert "domain_whois" not in AMBIENT_ONLY_SOURCES
    assert "linkedin" not in AMBIENT_ONLY_SOURCES


# ---- aggregate_company_evidence -------------------------------------------


def _row(source_api, detected_at="2026-04-01T00:00:00Z"):
    return {"source_api": source_api, "detected_at": detected_at}


def test_aggregate_empty_returns_empty_bundle():
    b = aggregate_company_evidence("co_1", [])
    assert b.distinct_class_count == 0
    assert b.has_non_ambient_class is False
    assert b.earliest_signal_at is None
    assert b.latest_signal_at is None
    assert b.source_api_counts == {}


def test_aggregate_single_ambient_signal_has_no_non_ambient():
    b = aggregate_company_evidence("co_1", [_row("hacker_news")])
    assert EvidenceClass.AMBIENT_CORROBORATION in b.classes_present
    assert b.has_non_ambient_class is False
    assert b.distinct_class_count == 1


def test_aggregate_strong_class_has_non_ambient():
    b = aggregate_company_evidence("co_1", [_row("sec_edgar")])
    assert b.has_non_ambient_class is True


def test_aggregate_two_distinct_classes():
    b = aggregate_company_evidence(
        "co_1",
        [_row("sec_edgar"), _row("linkedin")],
    )
    assert b.distinct_class_count == 2
    assert b.has_non_ambient_class is True


def test_aggregate_counts_each_source_independently():
    rows = [
        _row("sec_edgar"),
        _row("sec_edgar"),
        _row("linkedin"),
    ]
    b = aggregate_company_evidence("co_1", rows)
    assert b.source_api_counts == {"sec_edgar": 2, "linkedin": 1}


def test_aggregate_timestamps():
    rows = [
        _row("sec_edgar", "2026-04-01T00:00:00Z"),
        _row("linkedin", "2026-04-03T00:00:00Z"),
        _row("hacker_news", "2026-04-02T00:00:00Z"),
    ]
    b = aggregate_company_evidence("co_1", rows)
    assert b.earliest_signal_at is not None
    assert b.latest_signal_at is not None
    assert b.earliest_signal_at.day == 1
    assert b.latest_signal_at.day == 3


def test_aggregate_ignores_unknown_source_for_classes_but_keeps_count():
    b = aggregate_company_evidence(
        "co_1",
        [_row("not_a_real_source"), _row("sec_edgar")],
    )
    assert EvidenceClass.UNKNOWN not in b.classes_present
    assert b.source_api_counts.get("not_a_real_source") == 1


# ---- evaluate_shadow_tier --------------------------------------------------


def _bundle(*sources):
    return aggregate_company_evidence("co_1", [_row(s) for s in sources])


def test_tier_none_for_empty_bundle():
    assert evaluate_shadow_tier(_bundle()) == ShadowTier.NONE


def test_tier_none_for_ambient_only():
    assert evaluate_shadow_tier(_bundle("hacker_news")) == ShadowTier.NONE
    assert (
        evaluate_shadow_tier(_bundle("hacker_news", "arxiv", "rss_feeds"))
        == ShadowTier.NONE
    ), "U5: ambient-only never qualifies, no matter how many ambient sources"


def test_tier_1_for_one_strong_class():
    assert evaluate_shadow_tier(_bundle("sec_edgar")) == ShadowTier.TIER_1
    assert evaluate_shadow_tier(_bundle("linkedin")) == ShadowTier.TIER_1
    assert evaluate_shadow_tier(_bundle("job_postings")) == ShadowTier.TIER_1


def test_tier_1_for_one_strong_class_plus_ambient():
    # Ambient corroboration is not a separate non-ambient class
    assert (
        evaluate_shadow_tier(_bundle("sec_edgar", "hacker_news")) == ShadowTier.TIER_1
    )


def test_tier_2_for_two_distinct_strong_classes():
    assert (
        evaluate_shadow_tier(_bundle("sec_edgar", "linkedin")) == ShadowTier.TIER_2
    )
    assert (
        evaluate_shadow_tier(_bundle("sec_edgar", "job_postings")) == ShadowTier.TIER_2
    )


def test_tier_2_two_sub_sources_same_class_is_only_tier_1():
    # sec_edgar + companies_house are both INFRASTRUCTURE_INTENT
    assert (
        evaluate_shadow_tier(_bundle("sec_edgar", "companies_house"))
        == ShadowTier.TIER_1
    )


def test_relaxed_mode_promotes_single_class_to_tier_2():
    # Phase 1 A/B knob — used to test "ultra-strong infrastructure bundle"
    b = _bundle("sec_edgar")
    assert (
        evaluate_shadow_tier(b, require_two_classes_for_tier2=False)
        == ShadowTier.TIER_2
    )


def test_function_never_returns_tier_3():
    # Safety contract: tier 3 (Notion push) is never decided here
    for sources in (
        ("sec_edgar", "linkedin", "job_postings"),
        ("sec_edgar", "linkedin", "job_postings", "github_activity"),
    ):
        result = evaluate_shadow_tier(_bundle(*sources))
        assert result != ShadowTier.TIER_3, (
            "evaluate_shadow_tier must never return TIER_3 — that decision "
            "belongs to the live thesis filter, not the shadow ontology"
        )


# ---- ATS sub-source mapping (real production source_apis) ------------------


def test_classify_real_ats_subsource_names():
    """Production uses per-ATS names, not generic 'job_postings'."""
    assert classify_source_api("greenhouse_jobs") == EvidenceClass.HIRING_VALIDATION
    assert classify_source_api("lever_jobs") == EvidenceClass.HIRING_VALIDATION
    assert classify_source_api("ashby_jobs") == EvidenceClass.HIRING_VALIDATION
    # Generic name still works for tests / spec docs
    assert classify_source_api("job_postings") == EvidenceClass.HIRING_VALIDATION


def test_ats_subsources_count_as_one_class_for_convergence():
    """Multiple ATS sub-sources are still ONE non-ambient class."""
    b = _bundle("greenhouse_jobs", "lever_jobs", "ashby_jobs")
    # All three are HIRING_VALIDATION → only 1 distinct class
    assert evaluate_shadow_tier(b) == ShadowTier.TIER_1


# ---- ANALYST_SEED class (manual entries) ----------------------------------


def test_classify_manual_seed_as_analyst_seed():
    assert classify_source_api("manual_seed") == EvidenceClass.ANALYST_SEED
    assert classify_source_api("manual_seed_buzz") == EvidenceClass.ANALYST_SEED


def test_analyst_seed_alone_is_tier_none():
    """ANALYST_SEED is a prior, not a discovery — never sole-qualifies."""
    assert evaluate_shadow_tier(_bundle("manual_seed")) == ShadowTier.NONE
    assert evaluate_shadow_tier(_bundle("manual_seed_buzz")) == ShadowTier.NONE


def test_analyst_seed_plus_ambient_is_tier_none():
    """Manual seed + HN is still not a discovery."""
    assert (
        evaluate_shadow_tier(_bundle("manual_seed", "hacker_news")) == ShadowTier.NONE
    )


def test_analyst_seed_plus_one_discovery_class_is_tier_1():
    """Manual seed does NOT promote a single discovery class to tier_2."""
    b = _bundle("manual_seed", "greenhouse_jobs")
    # Real production case: 17 promoted companies are (manual_seed + ATS)
    # The evidence ontology says: this is tier_1, not tier_2 — the only
    # discovery class is HIRING_VALIDATION. The seed is the analyst's prior.
    assert evaluate_shadow_tier(b) == ShadowTier.TIER_1


def test_analyst_seed_plus_two_discovery_classes_is_tier_2():
    b = _bundle("manual_seed", "greenhouse_jobs", "linkedin")
    assert evaluate_shadow_tier(b) == ShadowTier.TIER_2
