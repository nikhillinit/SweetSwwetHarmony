# Execution Checklist: Thesis Matcher Recall Improvement (v2)

## Pre-Execution Baseline
- [x] Run baseline evaluation: `python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_baseline.jsonl`
- [x] Record baseline metrics in 00-ITERATION-LOG.md (4.1% recall, 81% precision)
- [x] Verify 39 confirmed consumer companies file exists
- [x] Audit existing infrastructure (negative_keyword_policy.py, hard_disqualifiers.py)

---

## Phase 1: Scoring Prerequisites

### Task 1.1: Implement Normalization Update
- [ ] Read `utils/thesis_matcher.py` to find `_normalize` method
- [ ] Backup current `_normalize` implementation
- [ ] Implement new normalization with hyphen/slash/underscore handling
- [ ] Verify: `m._normalize('api-first platform')` → `'api first platform'`
- [ ] Run unit tests: `python -m pytest tests/utils/test_thesis_matcher.py -v`

### Task 1.2: Run Post-Normalization Eval
- [ ] Run: `python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_post_norm.jsonl`
- [ ] Record recall/precision delta in 00-ITERATION-LOG.md
- [ ] Verify no regression (precision should stay similar)

### Task 1.3: Compute Saturation Caps from Data
- [ ] Run script to calculate current keyword sums per thesis
- [ ] Record actual values in 02-technical-plan.md
- [ ] Add `THESIS_SATURATION_CAPS` dict to `utils/thesis_matcher.py`
- [ ] Calculate `DEFAULT_CAP = statistics.median(caps.values())`
- [ ] Modify `_score_thesis` to use caps

### Task 1.4: Add Invariance Test
- [ ] Create test: adding keywords does NOT change scores for existing samples
- [ ] Run: `python -m pytest tests/utils/test_thesis_matcher.py::test_saturation_invariance -v`

---

## Phase 2: Unified Routing Controller

### Task 2.1: Add Severity to negative_keyword_policy.py
- [ ] Add `NegativeKeywordSeverity` enum (HARD, SOFT)
- [ ] Add `HARD_NEGATIVE_KEYWORDS` set with explicit criteria
- [ ] Add `is_hard_negative(keyword)` function
- [ ] Update validation to support severity field
- [ ] Run: `python -m pytest tests/utils/test_negative_keyword_policy.py -v`

### Task 2.2: Add HELD_SOFT to RoutingDecision
- [ ] Read `utils/thesis_filter.py` to find `RoutingDecision` enum
- [ ] Add `HELD_SOFT = "held_soft"` value
- [ ] Verify enum change doesn't break imports

### Task 2.3: Create Single Rejection Handler
- [ ] Add `handle_rejection()` function to `negative_keyword_policy.py`
- [ ] Implement hard → mark_rejected + suppression cache
- [ ] Implement soft → mark_held + increment counter (no cache)
- [ ] Add unit test for handler behavior

### Task 2.4: Update ThesisFilter Routing Logic
- [ ] Find exact lines for negative keyword check (~168-169, ~225-226)
- [ ] Add import for `is_hard_negative`
- [ ] Split negatives into hard_matches and soft_matches
- [ ] Update routing logic per decision waterfall
- [ ] Add status override with freshness check (Gate A)

### Task 2.5: Update All 5 mark_rejected() Call Sites
- [ ] Grep for `mark_rejected` to find exact locations
- [ ] Update `workflows/pipeline.py` → use `handle_rejection()`
- [ ] Update `workflows/notion_pusher.py` → use `handle_rejection()`
- [ ] Update `collectors/curated_scout.py` → use `handle_rejection()`
- [ ] Update `consumer/consumer_pipeline.py` → use `handle_rejection()`
- [ ] Update `consumer/pusher.py` → use `handle_rejection()`

### Task 2.6: Add Status Override with Freshness Check
- [ ] Add `NON_DROPPABLE_STATUSES` constant
- [ ] Add `STATUS_FRESHNESS_HOURS = 48` constant
- [ ] Implement freshness check in classify method
- [ ] Add test: status override never rejects fresh in-flight deals

### Task 2.7: Add Feature Flag
- [ ] Add `USE_HARD_SOFT_NEGATIVES` env var (default: false)
- [ ] Wrap new routing logic in feature flag check
- [ ] Add test for legacy behavior when flag is OFF

### Task 2.8: Run Phase 2 Tests
- [ ] Run: `python -m pytest tests/utils/test_thesis_filter.py -v`
- [ ] Run: `python -m pytest tests/utils/test_negative_keyword_policy.py -v`
- [ ] Verify all tests pass with flag ON and OFF

---

## Phase 3: Targeted Vocabulary Expansion

### Task 3.1: Add Keywords for 19 Zero-Score Companies
- [ ] Add CONSUMER_HEALTH_TECH keywords (12 terms)
- [ ] Add CONSUMER_CPG keywords (2 terms)
- [ ] Add TRAVEL_HOSPITALITY keywords (4 terms)
- [ ] Add CONSUMER_MARKETPLACE keywords (4 terms)
- [ ] Verify saturation caps prevent dilution (scores unchanged for existing samples)

### Task 3.2: Run Post-Vocabulary Eval
- [ ] Run: `python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_post_vocab.jsonl`
- [ ] Record recall/precision delta

---

## Phase 4: Migration + Final Verification

### Task 4.1: Migrate Existing False Negatives
- [ ] Create migration script for 39 confirmed companies
- [ ] Run: mark_pending() + remove_from_suppression_cache()
- [ ] Verify companies are no longer suppressed

### Task 4.2: Run Final Evaluation
- [ ] Enable feature flag: `USE_HARD_SOFT_NEGATIVES=true`
- [ ] Run: `python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_results_v2.jsonl`
- [ ] Compare metrics: recall target 50%+, precision target 60%+
- [ ] Document final metrics in 00-ITERATION-LOG.md

### Task 4.3: Spot-Check for B2B Leakage
- [ ] Review new false positives
- [ ] Identify any B2B terms that should be HARD negatives
- [ ] Update HARD_NEGATIVE_KEYWORDS if needed

### Task 4.4: Final Documentation
- [ ] Update all forensic docs with final state
- [ ] Create checkpoint in memory-keeper
- [ ] Commit changes with clear message

---

## Definition of Done
- [ ] All tests pass with feature flag ON
- [ ] Recall improved to 50%+ (from 4.1%)
- [ ] Precision maintained above 60% (from 81%)
- [ ] No B2B leakage in spot-check
- [ ] All 6 forensic files are up-to-date
- [ ] Changes committed with clear message
- [ ] Feature flag can be toggled for rollback

---

## Quick Verification Commands
```bash
# Normalization test
python -c "from utils.thesis_matcher import ThesisMatcher; m = ThesisMatcher(); print(m._normalize('api-first platform'))"

# Hard/soft classification test
python -c "from utils.negative_keyword_policy import is_hard_negative; print(f'blockchain={is_hard_negative(\"blockchain\")}'); print(f'platform={is_hard_negative(\"platform\")}')"

# Full test suite
python -m pytest tests/utils/test_thesis_filter.py tests/utils/test_thesis_matcher.py tests/utils/test_negative_keyword_policy.py -v

# Full evaluation
python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_results_v2.jsonl
```
