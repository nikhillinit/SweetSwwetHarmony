"""Golden set regression tests for thesis routing (Phase 4b).

Loads curated cases from tests/fixtures/thesis_golden_set.jsonl and verifies
that each case produces the expected routing decision and decision path code
when run through ThesisMatcher + ThesisFilter with cascade enabled.

Each case covers a specific routing path:
- VETO_HARD_REJECT: crypto/template/late-stage hard vetoes
- HOLD_HARD_HOLD: B2B/enterprise ambiguous terms
- QUALIFY_SECTOR: strong sector keyword match
- QUALIFY_CONSUMER_RESCUE: consumer signal rescue (cascade)
- HOLD_B2B_GUARD_BLOCK: consumer signal present but B2B dominance blocks
- HOLD_DEFAULT: no signal, unknown companies

Coverage: 56 cases across all 7 decision path codes + edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from utils.thesis_filter import (
    DecisionPathCode,
    RoutingDecision,
    ThesisFilter,
    ThesisFilterConfig,
)
from utils.thesis_matcher import ThesisMatcher

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOLDEN_SET_PATH = Path(__file__).parent.parent / "fixtures" / "thesis_golden_set.jsonl"


def _load_golden_cases() -> List[Dict[str, Any]]:
    """Load golden set cases from JSONL fixture."""
    cases = []
    with open(GOLDEN_SET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


GOLDEN_CASES = _load_golden_cases()
GOLDEN_IDS = [c["id"] for c in GOLDEN_CASES]


@pytest.fixture(scope="module")
def matcher() -> ThesisMatcher:
    return ThesisMatcher()


@pytest.fixture(scope="module")
def thesis_filter() -> ThesisFilter:
    config = ThesisFilterConfig()
    return ThesisFilter(config=config)


# ---------------------------------------------------------------------------
# Meta tests
# ---------------------------------------------------------------------------


def test_golden_set_minimum_count():
    """Golden set must have at least 50 curated cases."""
    assert len(GOLDEN_CASES) >= 50, (
        f"Golden set has {len(GOLDEN_CASES)} cases, minimum is 50"
    )


def test_golden_set_unique_ids():
    """All golden set case IDs must be unique."""
    assert len(GOLDEN_IDS) == len(set(GOLDEN_IDS)), "Duplicate IDs in golden set"


def test_golden_set_path_code_coverage():
    """Golden set must cover all decision path codes."""
    covered = {c["expected_decision_path_code"] for c in GOLDEN_CASES}
    required = {
        "veto_hard_reject",
        "hold_hard_hold",
        "qualify_sector",
        "qualify_consumer_rescue",
        "hold_b2b_guard_block",
        "hold_default",
    }
    missing = required - covered
    assert not missing, f"Golden set missing path codes: {missing}"


def test_golden_set_routing_coverage():
    """Golden set must cover all three routing decisions."""
    covered = {c["expected_routing"] for c in GOLDEN_CASES}
    required = {"REJECTED", "HELD", "QUALIFIED"}
    missing = required - covered
    assert not missing, f"Golden set missing routing decisions: {missing}"


# ---------------------------------------------------------------------------
# Parametrized routing assertion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=GOLDEN_IDS)
def test_golden_routing(
    case: Dict[str, Any],
    matcher: ThesisMatcher,
    thesis_filter: ThesisFilter,
) -> None:
    """Assert routing decision and path code for each golden case."""
    text = case["text"]
    company_name = case.get("company_name")
    domain = case.get("domain")

    fit = matcher.score(text=text, company_name=company_name, domain_name=domain)
    routing, path_code = thesis_filter._route_keyword_only(
        fit, cascade_enabled=True,
    )

    assert routing.value.upper() == case["expected_routing"], (
        f"[{case['id']}] routing: expected {case['expected_routing']}, "
        f"got {routing.value.upper()} "
        f"(score={fit.score:.4f}, css={fit.consumer_signal_score:.4f}, "
        f"b2b={fit.b2b_soft_score:.4f}, anchors={fit.consumer_anchor_count})"
    )
    assert path_code.value == case["expected_decision_path_code"], (
        f"[{case['id']}] path_code: expected {case['expected_decision_path_code']}, "
        f"got {path_code.value}"
    )


# ---------------------------------------------------------------------------
# Signal subset checks (optional per case)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [c for c in GOLDEN_CASES if c.get("expected_neg_keywords")],
    ids=[c["id"] for c in GOLDEN_CASES if c.get("expected_neg_keywords")],
)
def test_golden_negative_keywords(
    case: Dict[str, Any],
    matcher: ThesisMatcher,
) -> None:
    """Assert that expected negative keywords appear in matched negatives."""
    fit = matcher.score(
        text=case["text"],
        company_name=case.get("company_name"),
        domain_name=case.get("domain"),
    )
    matched_neg = set(fit.negative_keywords)
    for expected_kw in case["expected_neg_keywords"]:
        assert expected_kw in matched_neg, (
            f"[{case['id']}] expected negative keyword '{expected_kw}' "
            f"not found in {matched_neg}"
        )


@pytest.mark.parametrize(
    "case",
    [c for c in GOLDEN_CASES if c["expected_decision_path_code"] == "qualify_consumer_rescue"],
    ids=[c["id"] for c in GOLDEN_CASES if c["expected_decision_path_code"] == "qualify_consumer_rescue"],
)
def test_golden_rescue_prerequisites(
    case: Dict[str, Any],
    matcher: ThesisMatcher,
    thesis_filter: ThesisFilter,
) -> None:
    """Assert rescue cases have sufficient consumer signal and anchors."""
    fit = matcher.score(
        text=case["text"],
        company_name=case.get("company_name"),
        domain_name=case.get("domain"),
    )
    assert fit.consumer_signal_score >= thesis_filter.config.consumer_rescue_threshold, (
        f"[{case['id']}] consumer_signal_score {fit.consumer_signal_score:.4f} "
        f"below rescue threshold {thesis_filter.config.consumer_rescue_threshold}"
    )
    assert fit.consumer_anchor_count >= thesis_filter.config.consumer_anchor_min, (
        f"[{case['id']}] consumer_anchor_count {fit.consumer_anchor_count} "
        f"below minimum {thesis_filter.config.consumer_anchor_min}"
    )


@pytest.mark.parametrize(
    "case",
    [c for c in GOLDEN_CASES if c["expected_decision_path_code"] == "hold_b2b_guard_block"],
    ids=[c["id"] for c in GOLDEN_CASES if c["expected_decision_path_code"] == "hold_b2b_guard_block"],
)
def test_golden_guard_block_dominance_fail(
    case: Dict[str, Any],
    matcher: ThesisMatcher,
    thesis_filter: ThesisFilter,
) -> None:
    """Assert guard-block cases have consumer signal but fail dominance check."""
    fit = matcher.score(
        text=case["text"],
        company_name=case.get("company_name"),
        domain_name=case.get("domain"),
    )
    cfg = thesis_filter.config
    # Has signal + anchor
    assert fit.consumer_signal_score >= cfg.consumer_rescue_threshold
    assert fit.consumer_anchor_count >= cfg.consumer_anchor_min
    # Fails dominance (both conditions)
    margin_ok = (fit.consumer_signal_score - fit.b2b_soft_score) >= cfg.consumer_dominance_margin
    ratio_ok = (
        fit.consumer_signal_score / max(fit.b2b_soft_score, 0.01) >= cfg.signal_ratio_min
    )
    assert not (margin_ok or ratio_ok), (
        f"[{case['id']}] expected dominance to fail but "
        f"margin_ok={margin_ok}, ratio_ok={ratio_ok} "
        f"(css={fit.consumer_signal_score:.4f}, b2b={fit.b2b_soft_score:.4f})"
    )
