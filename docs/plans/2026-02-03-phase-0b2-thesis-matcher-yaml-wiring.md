# Phase 0B-2: ThesisMatcher YAML Wiring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire ThesisMatcher scoring to read negative keyword weights from YAML when v2 is enabled, with shadow mode comparison logging that never changes outward results.

**Architecture:** Extract core scoring into composable helpers (`_compute_core`, `_compute_penalty`, `_apply_adjustments`, `_build_fit`). Shadow mode computes both v1 and v2 in parallel, attaches diff to trace, logs only high-signal divergences. Always returns v1 result in shadow mode.

**Tech Stack:** Python dataclasses, pytest, existing YAML policy infrastructure from Phase 0B-1

---

## Task 1: Add `v2_shadow` field to ThesisFitTrace

**Files:**
- Modify: `utils/thesis_matcher.py:239-284` (ThesisFitTrace dataclass)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (new file)

**Step 1: Write the failing test**

Create new test file `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
"""Tests for ThesisMatcher v2 shadow mode (Phase 0B-2)."""

import pytest


class TestThesisFitTraceV2Shadow:
    """Test v2_shadow field on ThesisFitTrace."""

    def test_v2_shadow_field_exists_and_defaults_to_none(self):
        """ThesisFitTrace should have v2_shadow field defaulting to None."""
        from utils.thesis_matcher import ThesisFitTrace

        trace = ThesisFitTrace()
        assert hasattr(trace, "v2_shadow")
        assert trace.v2_shadow is None

    def test_v2_shadow_included_in_to_dict_when_present(self):
        """to_dict() should include v2_shadow when it has a value."""
        from utils.thesis_matcher import ThesisFitTrace

        shadow_diff = {
            "v1": {"score": 0.5},
            "v2": {"score": 0.45},
            "delta_score": -0.05,
        }
        trace = ThesisFitTrace(v2_shadow=shadow_diff)
        result = trace.to_dict()

        assert "v2_shadow" in result
        assert result["v2_shadow"] == shadow_diff

    def test_v2_shadow_excluded_from_to_dict_when_none(self):
        """to_dict() should exclude v2_shadow when None."""
        from utils.thesis_matcher import ThesisFitTrace

        trace = ThesisFitTrace()
        result = trace.to_dict()

        assert "v2_shadow" not in result
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestThesisFitTraceV2Shadow -v`
Expected: FAIL with "AttributeError: 'ThesisFitTrace' object has no attribute 'v2_shadow'"

**Step 3: Write minimal implementation**

In `utils/thesis_matcher.py`, modify `ThesisFitTrace` dataclass (around line 239-284):

```python
@dataclass
class ThesisFitTrace:
    """Explainability trace for thesis classification debugging.
    ...existing docstring...
    """
    matched_hard_negatives: List[str] = field(default_factory=list)
    soft_negatives: List[Tuple[str, float]] = field(default_factory=list)
    rescue_anchors_matched: Dict[str, List[str]] = field(default_factory=dict)
    rescue_blocked_by: Optional[str] = None
    aggregator_exception_triggered: bool = False
    applied_ai_path: Optional[str] = None
    final_score: float = 0.0
    routing_decision: str = "UNKNOWN"
    explanation: str = ""
    # Phase 0B-2: Shadow mode comparison data
    v2_shadow: Optional[Dict] = None

    def to_dict(self) -> Dict:
        """Convert trace to dictionary for serialization."""
        result = {
            "matched_hard_negatives": self.matched_hard_negatives,
            "soft_negatives": [{"keyword": kw, "penalty": penalty}
                              for kw, penalty in self.soft_negatives],
            "rescue_anchors_matched": self.rescue_anchors_matched,
            "rescue_blocked_by": self.rescue_blocked_by,
            "aggregator_exception_triggered": self.aggregator_exception_triggered,
            "applied_ai_path": self.applied_ai_path,
            "final_score": round(self.final_score, 3),
            "routing_decision": self.routing_decision,
            "explanation": self.explanation,
        }
        # Phase 0B-2: Only include v2_shadow if present
        if self.v2_shadow is not None:
            result["v2_shadow"] = self.v2_shadow
        return result
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestThesisFitTraceV2Shadow -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add tests/utils/test_thesis_matcher_v2_shadow.py utils/thesis_matcher.py
git commit -m "feat(thesis): add v2_shadow field to ThesisFitTrace for shadow mode"
```

---

## Task 2: Add internal `_CoreScore` and `_PenaltyResult` dataclasses

**Files:**
- Modify: `utils/thesis_matcher.py` (add after ThesisFit, before ThesisMatcher class)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestInternalDataclasses:
    """Test internal dataclasses for scoring decomposition."""

    def test_core_score_is_frozen(self):
        """_CoreScore should be immutable (frozen)."""
        from utils.thesis_matcher import _CoreScore, ConsumerThesis

        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": ["food"]},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=[],
            domain_match=False,
        )

        with pytest.raises(AttributeError):
            core.base_score = 0.9

    def test_penalty_result_is_frozen(self):
        """_PenaltyResult should be immutable (frozen)."""
        from utils.thesis_matcher import _PenaltyResult

        penalty = _PenaltyResult(
            matches=["enterprise"],
            raw_penalty=0.5,
            applied_penalty=0.25,
        )

        with pytest.raises(AttributeError):
            penalty.raw_penalty = 0.9

    def test_core_score_fields(self):
        """_CoreScore should have all required fields."""
        from utils.thesis_matcher import _CoreScore, ConsumerThesis

        core = _CoreScore(
            normalized="test text",
            scores={"consumer_cpg": 0.5, "consumer_health_tech": 0.3},
            all_matches={"consumer_cpg": ["food"], "consumer_health_tech": []},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=["join waitlist"],
            domain_match=True,
        )

        assert core.normalized == "test text"
        assert core.best_thesis == ConsumerThesis.CONSUMER_CPG
        assert core.base_score == 0.5
        assert core.domain_match is True

    def test_penalty_result_fields(self):
        """_PenaltyResult should have all required fields."""
        from utils.thesis_matcher import _PenaltyResult

        penalty = _PenaltyResult(
            matches=["enterprise", "b2b"],
            raw_penalty=1.0,
            applied_penalty=0.5,
        )

        assert penalty.matches == ["enterprise", "b2b"]
        assert penalty.raw_penalty == 1.0
        assert penalty.applied_penalty == 0.5
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestInternalDataclasses -v`
Expected: FAIL with "cannot import name '_CoreScore' from 'utils.thesis_matcher'"

**Step 3: Write minimal implementation**

Add after `ThesisFit` class (around line 326) in `utils/thesis_matcher.py`:

```python
@dataclass(frozen=True)
class _CoreScore:
    """Internal: Core scoring result before penalty application.

    Captures positive thesis scoring + intent/domain signals.
    Shared between v1 and v2 paths to ensure differences only come from negatives.
    """
    normalized: str
    scores: Dict[str, float]
    all_matches: Dict[str, List[str]]
    best_thesis: ConsumerThesis
    base_score: float  # Best score BEFORE negative penalty + boosts
    matched_kws: List[str]
    intent_matches: List[str]
    domain_match: bool


@dataclass(frozen=True)
class _PenaltyResult:
    """Internal: Result of negative keyword penalty calculation.

    Separates penalty computation from score adjustment for v1/v2 comparison.
    """
    matches: List[str]
    raw_penalty: float  # sum(weights)
    applied_penalty: float  # raw_penalty * 0.5 (current behavior)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestInternalDataclasses -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add _CoreScore and _PenaltyResult internal dataclasses"
```

---

## Task 3: Extract `_compute_core()` helper method

**Files:**
- Modify: `utils/thesis_matcher.py` (add method to ThesisMatcher)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestComputeCore:
    """Test _compute_core() helper extraction."""

    def test_compute_core_returns_core_score(self):
        """_compute_core() should return a _CoreScore dataclass."""
        from utils.thesis_matcher import ThesisMatcher, _CoreScore

        matcher = ThesisMatcher(v2_enablement="disabled")
        normalized = "healthy meal kits delivered"
        core = matcher._compute_core(normalized, domain_name=None)

        assert isinstance(core, _CoreScore)
        assert core.normalized == normalized

    def test_compute_core_scores_all_theses(self):
        """_compute_core() should score all thesis categories."""
        from utils.thesis_matcher import ThesisMatcher, ConsumerThesis

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("healthy meal kits delivered", domain_name=None)

        # Should have scores for all theses
        assert "consumer_cpg" in core.scores
        assert "consumer_health_tech" in core.scores
        assert "travel_hospitality" in core.scores
        assert "consumer_marketplace" in core.scores

    def test_compute_core_finds_best_thesis(self):
        """_compute_core() should identify best matching thesis."""
        from utils.thesis_matcher import ThesisMatcher, ConsumerThesis

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("fitness app wellness workout", domain_name=None)

        assert core.best_thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert core.base_score > 0

    def test_compute_core_detects_intent_phrases(self):
        """_compute_core() should detect intent phrases."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("join waitlist for our app", domain_name=None)

        assert "join waitlist" in core.intent_matches

    def test_compute_core_detects_domain_patterns(self):
        """_compute_core() should detect consumer domain patterns."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("some text", domain_name="getfitness.com")

        assert core.domain_match is True

    def test_compute_core_no_domain_match_for_non_consumer_domain(self):
        """_compute_core() should not match non-consumer domains."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = matcher._compute_core("some text", domain_name="example.com")

        assert core.domain_match is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestComputeCore -v`
Expected: FAIL with "AttributeError: 'ThesisMatcher' object has no attribute '_compute_core'"

**Step 3: Write minimal implementation**

Add method to `ThesisMatcher` class (after `__init__`, before `score`):

```python
def _compute_core(
    self,
    normalized: str,
    domain_name: Optional[str],
) -> _CoreScore:
    """Compute core scoring: positive thesis scores + intent/domain signals.

    This is shared between v1 and v2 paths so differences only come from negatives.

    Args:
        normalized: Normalized text to score
        domain_name: Optional domain for pattern matching

    Returns:
        _CoreScore with all positive scoring results
    """
    # Score each thesis (existing logic)
    scores: Dict[str, float] = {}
    all_matches: Dict[str, List[str]] = {}

    for thesis, keywords in self.keywords.items():
        score, matches = self._score_thesis(normalized, keywords)
        scores[thesis.value] = score
        all_matches[thesis.value] = matches

    # Find best thesis
    if scores:
        best_thesis_name = max(scores, key=scores.get)
        base_score = scores[best_thesis_name]
        best_thesis = ConsumerThesis(best_thesis_name)
        matched_kws = all_matches.get(best_thesis_name, [])
    else:
        best_thesis = ConsumerThesis.UNKNOWN
        base_score = 0.0
        matched_kws = []

    # Intent phrases and domain patterns
    intent_matches = self._find_intent_phrases(normalized)
    domain_match = self._check_domain_patterns(domain_name)

    return _CoreScore(
        normalized=normalized,
        scores=scores,
        all_matches=all_matches,
        best_thesis=best_thesis,
        base_score=base_score,
        matched_kws=matched_kws,
        intent_matches=intent_matches,
        domain_match=domain_match,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestComputeCore -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): extract _compute_core() helper for shared scoring"
```

---

## Task 4: Refactor `_find_negative_keywords()` with keyword-only vocab parameter

**Files:**
- Modify: `utils/thesis_matcher.py:614-620` (`_find_negative_keywords` method)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestFindNegativeKeywordsRefactor:
    """Test _find_negative_keywords() with keyword-only vocab parameter."""

    def test_default_path_equals_explicit_v1_vocab(self):
        """Default path should equal explicit NEGATIVE_KEYWORDS vocab."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise api platform logistics"

        default_result = matcher._find_negative_keywords(text)
        explicit_result = matcher._find_negative_keywords(text, negative_vocab=NEGATIVE_KEYWORDS)

        assert default_result == explicit_result

    def test_custom_vocab_limits_matches(self):
        """Custom vocab should limit which keywords are matched."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise crypto blockchain"

        # Full vocab would match all three
        full_result = matcher._find_negative_keywords(text)
        assert "enterprise" in full_result
        assert "crypto" in full_result

        # Subset vocab only matches what's in the subset
        subset = {"enterprise": 0.5}
        subset_result = matcher._find_negative_keywords(text, negative_vocab=subset)
        assert subset_result == ["enterprise"]

    def test_passing_string_raises_type_error(self):
        """Passing a single string instead of iterable should raise TypeError."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise platform"

        with pytest.raises(TypeError, match="iterable of keywords"):
            matcher._find_negative_keywords(text, negative_vocab="enterprise")

    def test_vocab_can_be_dict_keys(self):
        """Vocab can be a dict (iterates keys)."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise b2b platform"

        vocab_dict = {"enterprise": 0.5, "b2b": 0.5}
        result = matcher._find_negative_keywords(text, negative_vocab=vocab_dict)

        assert "enterprise" in result
        assert "b2b" in result
        assert len(result) == 2

    def test_vocab_can_be_list(self):
        """Vocab can be a list of keywords."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        text = "enterprise b2b platform"

        vocab_list = ["enterprise"]
        result = matcher._find_negative_keywords(text, negative_vocab=vocab_list)

        assert result == ["enterprise"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestFindNegativeKeywordsRefactor -v`
Expected: FAIL with "TypeError: _find_negative_keywords() got an unexpected keyword argument 'negative_vocab'"

**Step 3: Write minimal implementation**

Replace `_find_negative_keywords` method in `utils/thesis_matcher.py`:

```python
def _find_negative_keywords(
    self,
    text: str,
    *,
    negative_vocab: Optional[Iterable[str]] = None,
) -> List[str]:
    """Find negative keywords in text.

    Args:
        text: Normalized text to search
        negative_vocab: Optional iterable of keywords to search for.
            If None, uses NEGATIVE_KEYWORDS (v1 behavior).
            Can be a dict (iterates keys), list, or any iterable.

    Returns:
        List of matched negative keywords

    Raises:
        TypeError: If negative_vocab is a single string instead of iterable
    """
    # Preserve v1 behavior exactly: iterate the dict (keys) in insertion order
    vocab = NEGATIVE_KEYWORDS if negative_vocab is None else negative_vocab

    # Guardrail: passing a single string would iterate characters
    if isinstance(vocab, str):
        raise TypeError(
            "negative_vocab must be an iterable of keywords, not a single string"
        )

    matches: List[str] = []
    for keyword in vocab:
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, text):
            matches.append(keyword)
    return matches
```

Also add `Iterable` to the imports at the top of the file:

```python
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestFindNegativeKeywordsRefactor -v`
Expected: PASS (5 tests)

**Step 5: Run existing tests to verify no regression**

Run: `pytest tests/utils/test_thesis_matcher.py -v`
Expected: All existing tests still pass

**Step 6: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add keyword-only negative_vocab param to _find_negative_keywords"
```

---

## Task 5: Add `_compute_penalty()` helper method

**Files:**
- Modify: `utils/thesis_matcher.py` (add method to ThesisMatcher)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestComputePenalty:
    """Test _compute_penalty() helper method."""

    def test_compute_penalty_returns_penalty_result(self):
        """_compute_penalty() should return a _PenaltyResult dataclass."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        result = matcher._compute_penalty("enterprise platform", NEGATIVE_KEYWORDS)

        assert isinstance(result, _PenaltyResult)

    def test_compute_penalty_calculates_raw_and_applied(self):
        """_compute_penalty() should calculate raw and applied penalties."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        # "enterprise" has weight 0.5 in NEGATIVE_KEYWORDS
        result = matcher._compute_penalty("enterprise software", NEGATIVE_KEYWORDS)

        assert "enterprise" in result.matches
        assert result.raw_penalty == 0.5
        assert result.applied_penalty == 0.25  # raw * 0.5

    def test_compute_penalty_sums_multiple_matches(self):
        """_compute_penalty() should sum weights for multiple matches."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        # "enterprise" (0.5) + "b2b" (0.5) = 1.0 raw
        result = matcher._compute_penalty("enterprise b2b platform", NEGATIVE_KEYWORDS)

        assert "enterprise" in result.matches
        assert "b2b" in result.matches
        assert result.raw_penalty == 1.0
        assert result.applied_penalty == 0.5

    def test_compute_penalty_with_custom_weights(self):
        """_compute_penalty() should use custom weights dict."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        custom_weights = {"enterprise": 0.8, "platform": 0.3}
        result = matcher._compute_penalty("enterprise platform", custom_weights)

        assert result.matches == ["enterprise", "platform"]
        assert result.raw_penalty == 1.1
        assert result.applied_penalty == pytest.approx(0.55)

    def test_compute_penalty_no_matches(self):
        """_compute_penalty() should return zeros when no matches."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        result = matcher._compute_penalty("healthy food delivery", NEGATIVE_KEYWORDS)

        assert result.matches == []
        assert result.raw_penalty == 0.0
        assert result.applied_penalty == 0.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestComputePenalty -v`
Expected: FAIL with "AttributeError: 'ThesisMatcher' object has no attribute '_compute_penalty'"

**Step 3: Write minimal implementation**

Add method to `ThesisMatcher` class:

```python
def _compute_penalty(
    self,
    normalized: str,
    weights: Dict[str, float],
) -> _PenaltyResult:
    """Compute negative keyword penalty using specified weights.

    Args:
        normalized: Normalized text to search
        weights: Dict mapping keywords to penalty weights

    Returns:
        _PenaltyResult with matches, raw penalty sum, and applied penalty
    """
    matches = self._find_negative_keywords(normalized, negative_vocab=weights)
    raw = sum(weights.get(kw, 0.2) for kw in matches)
    applied = raw * 0.5
    return _PenaltyResult(matches=matches, raw_penalty=raw, applied_penalty=applied)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestComputePenalty -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add _compute_penalty() helper for negative keyword scoring"
```

---

## Task 6: Add `_apply_adjustments()` helper method

**Files:**
- Modify: `utils/thesis_matcher.py` (add method to ThesisMatcher)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestApplyAdjustments:
    """Test _apply_adjustments() helper method."""

    def test_apply_adjustments_subtracts_penalty(self):
        """_apply_adjustments() should subtract applied penalty from base score."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult

        matcher = ThesisMatcher(v2_enablement="disabled")
        penalty = _PenaltyResult(matches=["enterprise"], raw_penalty=0.5, applied_penalty=0.25)

        result = matcher._apply_adjustments(
            base_score=0.6,
            penalty=penalty,
            intent_matches=[],
            domain_match=False,
        )

        assert result == pytest.approx(0.35)

    def test_apply_adjustments_adds_intent_boost(self):
        """_apply_adjustments() should add intent phrase boost."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult

        matcher = ThesisMatcher(v2_enablement="disabled")
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        result = matcher._apply_adjustments(
            base_score=0.5,
            penalty=penalty,
            intent_matches=["join waitlist"],  # 0.3 boost
            domain_match=False,
        )

        assert result == pytest.approx(0.8)

    def test_apply_adjustments_adds_domain_boost(self):
        """_apply_adjustments() should add domain pattern boost."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult

        matcher = ThesisMatcher(v2_enablement="disabled")
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        result = matcher._apply_adjustments(
            base_score=0.5,
            penalty=penalty,
            intent_matches=[],
            domain_match=True,  # 0.15 boost
        )

        assert result == pytest.approx(0.65)

    def test_apply_adjustments_clamps_to_zero(self):
        """_apply_adjustments() should not go below 0."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult

        matcher = ThesisMatcher(v2_enablement="disabled")
        penalty = _PenaltyResult(matches=["a", "b"], raw_penalty=2.0, applied_penalty=1.0)

        result = matcher._apply_adjustments(
            base_score=0.3,
            penalty=penalty,
            intent_matches=[],
            domain_match=False,
        )

        assert result == 0.0

    def test_apply_adjustments_clamps_to_one(self):
        """_apply_adjustments() should not exceed 1.0."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult

        matcher = ThesisMatcher(v2_enablement="disabled")
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        result = matcher._apply_adjustments(
            base_score=0.9,
            penalty=penalty,
            intent_matches=["join waitlist", "pricing"],  # 0.3 + 0.25 = 0.55
            domain_match=True,  # 0.15
        )

        assert result == 1.0

    def test_apply_adjustments_order_penalty_then_boosts(self):
        """_apply_adjustments() should apply penalty first, then boosts."""
        from utils.thesis_matcher import ThesisMatcher, _PenaltyResult

        matcher = ThesisMatcher(v2_enablement="disabled")
        # Penalty: 0.25 applied, Intent: 0.3, Domain: 0.15
        # Order: 0.6 - 0.25 = 0.35, then 0.35 + 0.3 = 0.65, then 0.65 + 0.15 = 0.8
        penalty = _PenaltyResult(matches=["enterprise"], raw_penalty=0.5, applied_penalty=0.25)

        result = matcher._apply_adjustments(
            base_score=0.6,
            penalty=penalty,
            intent_matches=["join waitlist"],
            domain_match=True,
        )

        assert result == pytest.approx(0.8)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestApplyAdjustments -v`
Expected: FAIL with "AttributeError: 'ThesisMatcher' object has no attribute '_apply_adjustments'"

**Step 3: Write minimal implementation**

Add method to `ThesisMatcher` class:

```python
def _apply_adjustments(
    self,
    base_score: float,
    penalty: _PenaltyResult,
    intent_matches: List[str],
    domain_match: bool,
) -> float:
    """Apply penalty and boosts to base score.

    Order preserved from original score():
    1. Subtract penalty (if matches)
    2. Add intent phrase boost (if matches)
    3. Add domain pattern boost (if match)

    Args:
        base_score: Score before adjustments
        penalty: Penalty result from _compute_penalty
        intent_matches: Intent phrases matched
        domain_match: Whether domain pattern matched

    Returns:
        Final score clamped to [0.0, 1.0]
    """
    score = base_score

    # Apply negative penalty
    if penalty.matches:
        score = max(0.0, score - penalty.applied_penalty)

    # Apply intent phrase boost
    if intent_matches:
        intent_boost = sum(INTENT_PHRASES.get(p, 0.1) for p in intent_matches)
        score = min(1.0, score + intent_boost)

    # Apply domain pattern boost
    if domain_match:
        score = min(1.0, score + 0.15)

    return score
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestApplyAdjustments -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add _apply_adjustments() helper for score modifications"
```

---

## Task 7: Add `_negative_weights_v1()` and `_negative_weights_v2()` accessors

**Files:**
- Modify: `utils/thesis_matcher.py` (add methods to ThesisMatcher)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestNegativeWeightsAccessors:
    """Test _negative_weights_v1() and _negative_weights_v2() accessors."""

    def test_negative_weights_v1_returns_hardcoded_dict(self):
        """_negative_weights_v1() should return NEGATIVE_KEYWORDS."""
        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher = ThesisMatcher(v2_enablement="disabled")
        weights = matcher._negative_weights_v1()

        assert weights is NEGATIVE_KEYWORDS

    def test_negative_weights_v2_returns_empty_when_disabled(self):
        """_negative_weights_v2() should return empty dict when v2 disabled."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        weights = matcher._negative_weights_v2()

        assert weights == {}

    def test_negative_weights_v2_returns_policy_weights(self, tmp_path):
        """_negative_weights_v2() should return weights from YAML policy."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.6\n"
            "    category: B2B_ENTERPRISE\n"
            "  blockchain:\n"
            "    weight: 0.7\n"
            "    category: CRYPTO_WEB3\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))
        weights = matcher._negative_weights_v2()

        assert weights == {"enterprise": 0.6, "blockchain": 0.7}

    def test_negative_weights_v2_matches_yaml_exactly(self, tmp_path):
        """_negative_weights_v2() weights should match YAML values exactly."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  test_keyword:\n"
            "    weight: 0.42\n"
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))
        weights = matcher._negative_weights_v2()

        assert weights["test_keyword"] == 0.42
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestNegativeWeightsAccessors -v`
Expected: FAIL with "AttributeError: 'ThesisMatcher' object has no attribute '_negative_weights_v1'"

**Step 3: Write minimal implementation**

Add methods to `ThesisMatcher` class:

```python
def _negative_weights_v1(self) -> Dict[str, float]:
    """Get v1 negative keyword weights (hardcoded NEGATIVE_KEYWORDS).

    Returns:
        NEGATIVE_KEYWORDS dict (not a copy - callers should not mutate)
    """
    return NEGATIVE_KEYWORDS

def _negative_weights_v2(self) -> Dict[str, float]:
    """Get v2 negative keyword weights from YAML policy.

    Returns:
        Dict mapping keywords to weights, or empty dict if v2 not enabled
    """
    if not getattr(self, "_negative_keyword_policy", None):
        return {}
    return {
        kw: entry.weight
        for kw, entry in self._negative_keyword_policy.keywords.items()
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestNegativeWeightsAccessors -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add _negative_weights_v1/v2() accessors for weight sources"
```

---

## Task 8: Add `_build_fit()` helper method

**Files:**
- Modify: `utils/thesis_matcher.py` (add method to ThesisMatcher)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestBuildFit:
    """Test _build_fit() helper method."""

    def test_build_fit_returns_thesis_fit(self):
        """_build_fit() should return a ThesisFit object."""
        from utils.thesis_matcher import (
            ThesisMatcher, ThesisFit, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": ["food"]},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=[],
            domain_match=False,
        )
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        fit = matcher._build_fit(core, final_score=0.5, penalty=penalty, negative_weights=NEGATIVE_KEYWORDS)

        assert isinstance(fit, ThesisFit)

    def test_build_fit_sets_confidence_high(self):
        """_build_fit() should set HIGH confidence for score >= 0.7."""
        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.8},
            all_matches={"consumer_cpg": ["food"]},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.8,
            matched_kws=["food"],
            intent_matches=[],
            domain_match=False,
        )
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        fit = matcher._build_fit(core, final_score=0.75, penalty=penalty, negative_weights=NEGATIVE_KEYWORDS)

        assert fit.confidence == "HIGH"

    def test_build_fit_sets_confidence_medium(self):
        """_build_fit() should set MEDIUM confidence for 0.4 <= score < 0.7."""
        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": ["food"]},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=[],
            domain_match=False,
        )
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        fit = matcher._build_fit(core, final_score=0.5, penalty=penalty, negative_weights=NEGATIVE_KEYWORDS)

        assert fit.confidence == "MEDIUM"

    def test_build_fit_sets_confidence_low(self):
        """_build_fit() should set LOW confidence for score < 0.4."""
        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.2},
            all_matches={"consumer_cpg": []},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.2,
            matched_kws=[],
            intent_matches=[],
            domain_match=False,
        )
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        fit = matcher._build_fit(core, final_score=0.2, penalty=penalty, negative_weights=NEGATIVE_KEYWORDS)

        assert fit.confidence == "LOW"

    def test_build_fit_sets_unknown_thesis_for_low_score(self):
        """_build_fit() should set UNKNOWN thesis when score <= 0.1."""
        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.05},
            all_matches={"consumer_cpg": []},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.05,
            matched_kws=[],
            intent_matches=[],
            domain_match=False,
        )
        penalty = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        fit = matcher._build_fit(core, final_score=0.05, penalty=penalty, negative_weights=NEGATIVE_KEYWORDS)

        assert fit.thesis == ConsumerThesis.UNKNOWN

    def test_build_fit_includes_trace(self):
        """_build_fit() should include a trace."""
        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="disabled")
        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": ["food"]},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=["food"],
            intent_matches=[],
            domain_match=False,
        )
        penalty = _PenaltyResult(matches=["enterprise"], raw_penalty=0.5, applied_penalty=0.25)

        fit = matcher._build_fit(core, final_score=0.25, penalty=penalty, negative_weights=NEGATIVE_KEYWORDS)

        assert fit.trace is not None
        assert fit.trace.final_score == 0.25
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestBuildFit -v`
Expected: FAIL with "AttributeError: 'ThesisMatcher' object has no attribute '_build_fit'"

**Step 3: Write minimal implementation**

First, update `_generate_trace` to accept `negative_weights` parameter. Find the method and update its signature:

```python
def _generate_trace(
    self,
    best_score: float,
    matched_keywords: List[str],
    negative_matches: List[str],
    intent_matches: List[str],
    domain_match: bool,
    *,
    negative_weights: Optional[Dict[str, float]] = None,
) -> ThesisFitTrace:
    """Generate explainability trace for thesis classification.
    ...existing docstring...
    """
    # Use provided weights or fall back to NEGATIVE_KEYWORDS
    weights = negative_weights if negative_weights is not None else NEGATIVE_KEYWORDS

    # Convert negative matches to soft negatives with penalties
    soft_negatives = [
        (kw, weights.get(kw, 0.2))
        for kw in negative_matches
    ]
    # ... rest of method unchanged ...
```

Then add `_build_fit` method:

```python
def _build_fit(
    self,
    core: _CoreScore,
    final_score: float,
    penalty: _PenaltyResult,
    negative_weights: Dict[str, float],
) -> ThesisFit:
    """Build ThesisFit result from core scoring and penalty.

    Args:
        core: Core scoring results
        final_score: Score after all adjustments
        penalty: Penalty calculation results
        negative_weights: Weights used for penalty (for trace)

    Returns:
        Complete ThesisFit object
    """
    # Determine confidence
    if final_score >= 0.7:
        confidence = "HIGH"
    elif final_score >= 0.4:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Generate trace
    trace = self._generate_trace(
        best_score=final_score,
        matched_keywords=core.matched_kws,
        negative_matches=penalty.matches,
        intent_matches=core.intent_matches,
        domain_match=core.domain_match,
        negative_weights=negative_weights,
    )

    return ThesisFit(
        thesis=core.best_thesis if final_score > 0.1 else ConsumerThesis.UNKNOWN,
        score=final_score,
        matched_keywords=core.matched_kws,
        negative_keywords=penalty.matches,
        all_scores=core.scores,
        confidence=confidence,
        intent_phrases_matched=core.intent_matches,
        domain_match=core.domain_match,
        domain_blacklisted=False,
        trace=trace,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestBuildFit -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add _build_fit() helper to construct ThesisFit"
```

---

## Task 9: Implement `_attach_v2_shadow_diff()` method

**Files:**
- Modify: `utils/thesis_matcher.py` (add method to ThesisMatcher)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestAttachV2ShadowDiff:
    """Test _attach_v2_shadow_diff() method."""

    def test_attaches_v2_shadow_to_trace(self, tmp_path):
        """_attach_v2_shadow_diff() should attach shadow diff to fit_v1.trace."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.6\n"
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import (
            ThesisMatcher, ThesisFit, ThesisFitTrace, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        # Create mock fit objects
        core = _CoreScore(
            normalized="enterprise software",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": []},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=[],
            intent_matches=[],
            domain_match=False,
        )

        # v1: enterprise weight 0.5, applied 0.25, score = 0.5 - 0.25 = 0.25
        p1 = _PenaltyResult(matches=["enterprise"], raw_penalty=0.5, applied_penalty=0.25)
        fit_v1 = matcher._build_fit(core, final_score=0.25, penalty=p1, negative_weights=NEGATIVE_KEYWORDS)

        # v2: enterprise weight 0.6, applied 0.30, score = 0.5 - 0.30 = 0.20
        p2 = _PenaltyResult(matches=["enterprise"], raw_penalty=0.6, applied_penalty=0.30)
        fit_v2 = matcher._build_fit(core, final_score=0.20, penalty=p2, negative_weights={"enterprise": 0.6})

        # Attach diff
        matcher._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)

        # Verify v2_shadow was attached
        assert fit_v1.trace.v2_shadow is not None
        assert "v1" in fit_v1.trace.v2_shadow
        assert "v2" in fit_v1.trace.v2_shadow
        assert "delta_score" in fit_v1.trace.v2_shadow

    def test_shadow_diff_contains_correct_values(self, tmp_path):
        """v2_shadow should contain correct v1/v2 comparison values."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords: {}\n"
        )

        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={"consumer_cpg": []},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=[],
            intent_matches=[],
            domain_match=False,
        )

        p1 = _PenaltyResult(matches=["enterprise"], raw_penalty=0.5, applied_penalty=0.25)
        fit_v1 = matcher._build_fit(core, final_score=0.25, penalty=p1, negative_weights=NEGATIVE_KEYWORDS)

        p2 = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)
        fit_v2 = matcher._build_fit(core, final_score=0.50, penalty=p2, negative_weights={})

        matcher._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)

        shadow = fit_v1.trace.v2_shadow
        assert shadow["v1"]["score"] == 0.25
        assert shadow["v2"]["score"] == 0.50
        assert shadow["delta_score"] == pytest.approx(0.25)
        assert shadow["v1"]["negative_keywords"] == ["enterprise"]
        assert shadow["v2"]["negative_keywords"] == []

    def test_shadow_diff_detects_would_change_is_fit(self, tmp_path):
        """v2_shadow should detect when is_fit would change."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords: {}\n"
        )

        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.5},
            all_matches={},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.5,
            matched_kws=[],
            intent_matches=[],
            domain_match=False,
        )

        # v1: score 0.35 (not fit, < 0.4)
        p1 = _PenaltyResult(matches=["enterprise"], raw_penalty=0.3, applied_penalty=0.15)
        fit_v1 = matcher._build_fit(core, final_score=0.35, penalty=p1, negative_weights=NEGATIVE_KEYWORDS)

        # v2: score 0.50 (fit, >= 0.4)
        p2 = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)
        fit_v2 = matcher._build_fit(core, final_score=0.50, penalty=p2, negative_weights={})

        matcher._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)

        assert fit_v1.trace.v2_shadow["would_change_is_fit"] is True

    def test_shadow_diff_detects_would_change_routing(self, tmp_path):
        """v2_shadow should detect when routing decision would change."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords: {}\n"
        )

        from utils.thesis_matcher import (
            ThesisMatcher, _CoreScore, _PenaltyResult,
            ConsumerThesis, NEGATIVE_KEYWORDS
        )

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        core = _CoreScore(
            normalized="test",
            scores={"consumer_cpg": 0.3},
            all_matches={},
            best_thesis=ConsumerThesis.CONSUMER_CPG,
            base_score=0.3,
            matched_kws=[],
            intent_matches=[],
            domain_match=False,
        )

        # v1: score 0.25 (HELD, 0.1 <= score < 0.3)
        p1 = _PenaltyResult(matches=["token"], raw_penalty=0.1, applied_penalty=0.05)
        fit_v1 = matcher._build_fit(core, final_score=0.25, penalty=p1, negative_weights=NEGATIVE_KEYWORDS)

        # v2: score 0.30 (QUALIFIED, >= 0.3)
        p2 = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)
        fit_v2 = matcher._build_fit(core, final_score=0.30, penalty=p2, negative_weights={})

        matcher._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)

        assert fit_v1.trace.v2_shadow["would_change_routing"] is True

    def test_no_attachment_when_trace_is_none(self, tmp_path):
        """_attach_v2_shadow_diff() should do nothing if fit_v1.trace is None."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords: {}\n"
        )

        from utils.thesis_matcher import (
            ThesisMatcher, ThesisFit, _PenaltyResult, ConsumerThesis
        )

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        # Create fit with trace=None
        fit_v1 = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.5,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="MEDIUM",
            trace=None,
        )
        fit_v2 = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.6,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="MEDIUM",
            trace=None,
        )

        p1 = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)
        p2 = _PenaltyResult(matches=[], raw_penalty=0.0, applied_penalty=0.0)

        # Should not raise
        matcher._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)

        # trace is still None
        assert fit_v1.trace is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestAttachV2ShadowDiff -v`
Expected: FAIL with "AttributeError: 'ThesisMatcher' object has no attribute '_attach_v2_shadow_diff'"

**Step 3: Write minimal implementation**

Add method to `ThesisMatcher` class:

```python
def _attach_v2_shadow_diff(
    self,
    fit_v1: ThesisFit,
    fit_v2: ThesisFit,
    p1: _PenaltyResult,
    p2: _PenaltyResult,
) -> None:
    """Attach v2 shadow diff to fit_v1's trace for observability.

    Computes diff between v1 and v2 results and attaches to trace.
    Logs high-signal divergences (routing change, is_fit change, large delta).

    Args:
        fit_v1: The v1 result (will be modified to add v2_shadow)
        fit_v2: The v2 result (for comparison)
        p1: v1 penalty result
        p2: v2 penalty result
    """
    if not fit_v1.trace:
        return

    def route(score: float) -> str:
        """Determine routing decision from score."""
        if score >= 0.3:
            return "QUALIFIED"
        if score >= 0.1:
            return "HELD"
        return "REJECTED"

    diff = {
        "v1": {
            "score": fit_v1.score,
            "penalty_raw": p1.raw_penalty,
            "negative_keywords": p1.matches,
            "routing": route(fit_v1.score),
            "thesis": fit_v1.thesis.value,
        },
        "v2": {
            "score": fit_v2.score,
            "penalty_raw": p2.raw_penalty,
            "negative_keywords": p2.matches,
            "routing": route(fit_v2.score),
            "thesis": fit_v2.thesis.value,
        },
        "delta_score": fit_v2.score - fit_v1.score,
        "would_change_is_fit": (fit_v2.is_fit != fit_v1.is_fit),
        "would_change_routing": (route(fit_v2.score) != route(fit_v1.score)),
        "would_change_thesis": (fit_v2.thesis != fit_v1.thesis),
    }

    fit_v1.trace.v2_shadow = diff

    # Log high-signal divergences only
    if (
        diff["would_change_routing"]
        or diff["would_change_is_fit"]
        or abs(diff["delta_score"]) >= 0.05
    ):
        logger.info("v2 shadow diff: %s", diff)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestAttachV2ShadowDiff -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): add _attach_v2_shadow_diff() for shadow mode observability"
```

---

## Task 10: Refactor `score()` to use new helpers and implement shadow mode

**Files:**
- Modify: `utils/thesis_matcher.py:453-585` (`score` method)
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append integration tests)

**Step 1: Write the failing test**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestScoreShadowModeIntegration:
    """Integration tests for score() with shadow mode."""

    def test_shadow_mode_returns_v1_result(self, tmp_path):
        """Shadow mode should always return v1 result."""
        # Create policy with DIFFERENT weights than v1
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.9\n"  # Higher than v1's 0.5
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher, NEGATIVE_KEYWORDS

        matcher_v1 = ThesisMatcher(v2_enablement="disabled")
        matcher_shadow = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        text = "enterprise software for food delivery"

        fit_v1 = matcher_v1.score(text)
        fit_shadow = matcher_shadow.score(text)

        # Shadow should return same result as v1
        assert fit_shadow.score == fit_v1.score
        assert fit_shadow.thesis == fit_v1.thesis
        assert fit_shadow.confidence == fit_v1.confidence
        assert fit_shadow.negative_keywords == fit_v1.negative_keywords

    def test_shadow_mode_attaches_v2_diff(self, tmp_path):
        """Shadow mode should attach v2_shadow diff to trace."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.9\n"
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))
        fit = matcher.score("enterprise food delivery startup")

        assert fit.trace is not None
        assert fit.trace.v2_shadow is not None
        assert "v1" in fit.trace.v2_shadow
        assert "v2" in fit.trace.v2_shadow

    def test_shadow_mode_v2_diff_shows_different_penalty(self, tmp_path):
        """Shadow diff should show v2 uses different penalty weights."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.8\n"  # v1 is 0.5
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))
        fit = matcher.score("enterprise meal kit delivery")

        shadow = fit.trace.v2_shadow
        # v1 raw penalty: 0.5, v2 raw penalty: 0.8
        assert shadow["v1"]["penalty_raw"] == 0.5
        assert shadow["v2"]["penalty_raw"] == 0.8
        # v2 score should be lower due to higher penalty
        assert shadow["v2"]["score"] < shadow["v1"]["score"]

    def test_disabled_mode_no_v2_shadow(self):
        """Disabled mode should not have v2_shadow in trace."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")
        fit = matcher.score("enterprise food delivery")

        assert fit.trace is not None
        assert fit.trace.v2_shadow is None

    def test_live_mode_returns_v2_result(self, tmp_path):
        """Live mode should return v2 result."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.1\n"  # Much lower than v1's 0.5
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher_v1 = ThesisMatcher(v2_enablement="disabled")
        matcher_live = ThesisMatcher(v2_enablement="live", config_path=str(tmp_path))

        text = "enterprise food delivery startup"

        fit_v1 = matcher_v1.score(text)
        fit_live = matcher_live.score(text)

        # Live should return different (higher) score due to lower penalty
        assert fit_live.score > fit_v1.score

    def test_shadow_logs_high_signal_diff(self, tmp_path, caplog):
        """Shadow mode should log when diff is significant."""
        import logging

        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords: {}\n"  # Empty = no penalty in v2
        )

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))

        with caplog.at_level(logging.INFO):
            # "enterprise" in v1 has 0.5 penalty, v2 has 0 - delta >= 0.05
            fit = matcher.score("enterprise software")

        # Should have logged the diff
        assert any("v2 shadow diff" in record.message for record in caplog.records)

    def test_existing_score_behavior_unchanged(self):
        """All existing score() behaviors should be preserved."""
        from utils.thesis_matcher import ThesisMatcher, ConsumerThesis

        matcher = ThesisMatcher(v2_enablement="disabled")

        # Empty text
        fit = matcher.score("")
        assert fit.score == 0.0
        assert fit.thesis == ConsumerThesis.UNKNOWN

        # Domain blacklist
        fit = matcher.score("food delivery", domain_name="localhost:3000")
        assert fit.domain_blacklisted is True
        assert fit.score == 0.0

        # Positive match
        fit = matcher.score("healthy meal kits delivered to your door")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        assert fit.score > 0.4

        # Negative keyword penalty
        fit = matcher.score("enterprise b2b saas platform")
        assert "enterprise" in fit.negative_keywords
        assert "b2b" in fit.negative_keywords
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestScoreShadowModeIntegration -v`
Expected: Some tests will FAIL because `score()` hasn't been refactored yet to use the new helpers

**Step 3: Write minimal implementation**

Replace the `score()` method in `utils/thesis_matcher.py`:

```python
def score(
    self,
    text: str,
    company_name: Optional[str] = None,
    domain_name: Optional[str] = None,
) -> ThesisFit:
    """Score text against all Consumer thesis categories.

    Args:
        text: Company description or README content
        company_name: Optional company name for additional context
        domain_name: Optional domain for pattern matching (Phase B)

    Returns:
        ThesisFit with score, matched keywords, and domain analysis

    Phase 0B-2: When v2 is enabled:
    - shadow mode: computes v1 and v2, returns v1, attaches diff to trace
    - live mode: returns v2 result
    """
    # Early return for empty text
    if not text:
        empty_trace = ThesisFitTrace(
            final_score=0.0,
            routing_decision="REJECTED",
            explanation="Score: 0.00. Empty text provided. Routed to REJECTED.",
        )
        return ThesisFit(
            thesis=ConsumerThesis.UNKNOWN,
            score=0.0,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="LOW",
            intent_phrases_matched=[],
            domain_match=False,
            domain_blacklisted=False,
            trace=empty_trace,
        )

    normalized = self._normalize(text)
    if company_name:
        normalized += " " + self._normalize(company_name)

    # Early return for blacklisted domain
    domain_blacklisted = self._check_domain_blacklist(domain_name)
    if domain_blacklisted:
        blacklist_trace = ThesisFitTrace(
            final_score=0.0,
            routing_decision="REJECTED",
            explanation=f"Score: 0.00. Domain '{domain_name}' matched blacklist "
                       f"(non-production). Routed to REJECTED.",
        )
        return ThesisFit(
            thesis=ConsumerThesis.UNKNOWN,
            score=0.0,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="LOW",
            intent_phrases_matched=[],
            domain_match=False,
            domain_blacklisted=True,
            trace=blacklist_trace,
        )

    # Compute core (shared between v1 and v2)
    core = self._compute_core(normalized, domain_name)

    # v1 path (always computed)
    w1 = self._negative_weights_v1()
    p1 = self._compute_penalty(core.normalized, w1)
    s1 = self._apply_adjustments(core.base_score, p1, core.intent_matches, core.domain_match)
    fit_v1 = self._build_fit(core, s1, p1, w1)

    # If v2 disabled OR execution disabled => return v1
    if (
        not self._controls
        or self._controls.v2_enablement == "disabled"
        or not self._controls.v2_execution_enabled
    ):
        return fit_v1

    # v2 path
    w2 = self._negative_weights_v2()

    # Safety: if v2 is live but weights are empty, warn and fall back to v1
    if self._controls.v2_enablement == "live" and not w2:
        logger.error(
            "v2 enabled (live) but negative_keyword_policy is empty; "
            "falling back to v1 to avoid silent penalty removal"
        )
        return fit_v1

    p2 = self._compute_penalty(core.normalized, w2)
    s2 = self._apply_adjustments(core.base_score, p2, core.intent_matches, core.domain_match)
    fit_v2 = self._build_fit(core, s2, p2, w2)

    # Shadow mode: attach diff and return v1
    if self._controls.v2_enablement == "shadow":
        self._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)
        return fit_v1

    # Live mode: return v2
    return fit_v2
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestScoreShadowModeIntegration -v`
Expected: PASS (7 tests)

**Step 5: Run all tests to verify no regression**

Run: `pytest tests/utils/test_thesis_matcher*.py -v`
Expected: All tests pass

**Step 6: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "feat(thesis): refactor score() with shadow mode v1/v2 comparison"
```

---

## Task 11: Final integration test and cleanup

**Files:**
- Test: `tests/utils/test_thesis_matcher_v2_shadow.py` (append final tests)
- Modify: `utils/thesis_matcher.py` (cleanup any unused code)

**Step 1: Write the final integration tests**

Append to `tests/utils/test_thesis_matcher_v2_shadow.py`:

```python
class TestPhase0B2Complete:
    """Final integration tests for Phase 0B-2 completion."""

    def test_shadow_mode_end_to_end_with_production_yaml(self):
        """Shadow mode works with the actual production YAML policy."""
        from utils.thesis_matcher import ThesisMatcher

        # Use actual config/v2 directory (production YAML)
        matcher = ThesisMatcher(v2_enablement="shadow")

        # Should not raise, should work with production policy
        fit = matcher.score("enterprise blockchain crypto startup")

        assert fit.trace is not None
        assert fit.trace.v2_shadow is not None
        # Both v1 and v2 should find the same negative keywords
        # (since production YAML mirrors NEGATIVE_KEYWORDS)
        assert "enterprise" in fit.negative_keywords
        assert "blockchain" in fit.negative_keywords

    def test_v1_v2_parity_with_mirrored_yaml(self):
        """When YAML mirrors NEGATIVE_KEYWORDS exactly, v1 == v2."""
        from utils.thesis_matcher import ThesisMatcher

        # Production YAML should mirror NEGATIVE_KEYWORDS
        matcher = ThesisMatcher(v2_enablement="shadow")

        fit = matcher.score("enterprise b2b saas platform for food delivery")

        shadow = fit.trace.v2_shadow
        # With mirrored weights, scores should be identical
        assert shadow["v1"]["score"] == shadow["v2"]["score"]
        assert shadow["delta_score"] == 0.0
        assert shadow["would_change_is_fit"] is False
        assert shadow["would_change_routing"] is False

    def test_to_dict_serialization_with_v2_shadow(self, tmp_path):
        """ThesisFit.to_dict() should include v2_shadow when present."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text(
            "version: '2.0'\n"
            "schema: 'negative_keyword_policy_v1'\n"
            "negative_keywords:\n"
            "  enterprise:\n"
            "    weight: 0.8\n"
            "    category: B2B_ENTERPRISE\n"
        )

        from utils.thesis_matcher import ThesisMatcher
        import json

        matcher = ThesisMatcher(v2_enablement="shadow", config_path=str(tmp_path))
        fit = matcher.score("enterprise software for meal delivery")

        # Should be JSON-serializable
        result = fit.to_dict()
        json_str = json.dumps(result)
        parsed = json.loads(json_str)

        assert "trace" in parsed
        assert "v2_shadow" in parsed["trace"]
        assert "delta_score" in parsed["trace"]["v2_shadow"]
```

**Step 2: Run the final tests**

Run: `pytest tests/utils/test_thesis_matcher_v2_shadow.py::TestPhase0B2Complete -v`
Expected: PASS (3 tests)

**Step 3: Run full test suite**

Run: `pytest tests/utils/test_thesis_matcher*.py tests/utils/test_negative_keyword_policy.py -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add tests/utils/test_thesis_matcher_v2_shadow.py
git commit -m "test(thesis): add Phase 0B-2 final integration tests"
```

---

## Summary

**Total Tasks:** 11
**Total Tests Added:** ~50 new tests across 11 test classes
**Files Modified:**
- `utils/thesis_matcher.py` - Added dataclasses, helpers, and refactored score()
- `tests/utils/test_thesis_matcher_v2_shadow.py` - New test file

**Key Deliverables:**
1. `v2_shadow` field on `ThesisFitTrace`
2. `_CoreScore` and `_PenaltyResult` internal dataclasses
3. Helper methods: `_compute_core()`, `_compute_penalty()`, `_apply_adjustments()`, `_build_fit()`
4. Weight accessors: `_negative_weights_v1()`, `_negative_weights_v2()`
5. Shadow diff: `_attach_v2_shadow_diff()` with selective logging
6. Refactored `score()` with shadow/live mode support

**Shadow Mode Behavior:**
- Always returns v1 result (no outward change)
- Computes v2 in parallel for comparison
- Attaches diff to `trace.v2_shadow`
- Logs only high-signal divergences (routing change, is_fit change, delta >= 0.05)
