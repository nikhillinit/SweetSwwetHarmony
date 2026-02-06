# Forensic Engineer Validation: Hard/Soft Negative Strategy v4

## Goal
Validate plan v4 against actual codebase state before implementation begins.

## Status: IN_PROGRESS

---

## Phase 1: ANALYZE (Iteration 0)
**Objective:** Validate assumptions against codebase reality
**Status:** `complete`

### Checklist
- [x] 1.1 Verify `rejection_type` field is actually missing from ThesisFilterResult ✅
- [x] 1.2 Confirm `is_hard_negative()` doesn't exist in negative_keyword_policy.py ✅
- [x] 1.3 Verify `_norm_kw()` normalization helper doesn't exist ✅
- [x] 1.4 Check thesis_filter.py routing logic locations (lines 168-173, 225-230) ✅
- [x] 1.5 Verify pipeline.py rejection block at line 1575 ✅
- [x] 1.6 Confirm `update_signal_status()` signature accepts canonical_key ✅
- [x] 1.7 Verify YAML policy file exists and has expected categories ✅

### Findings
**All plan v4 assumptions VALIDATED:**

| Component | Status | Location |
|-----------|--------|----------|
| rejection_type field | MISSING | thesis_filter.py:46-66 |
| is_hard_negative() | MISSING | negative_keyword_policy.py (206 lines) |
| _norm_kw() | MISSING | thesis_matcher.py has only basic _normalize() |
| Hard-reject logic | CONFIRMED | thesis_filter.py:168-169, 225-226 |
| Pipeline rejection | CONFIRMED | workflows/pipeline.py:1575-1578 |
| update_signal_status() | canonical_key param | storage/signal_store.py:2626-2631 |
| YAML policy | EXISTS | config/v2/negative_keyword_policy.yaml (40 keywords, 6 categories) |

**Key Observations:**
- "series b" in STAGES with weight 0.3 - must keep STAGES soft
- "devops", "sdk" are in B2B_ENTERPRISE (not DEVTOOLS)
- "library", "framework", "plugin" in DEVTOOLS - correctly excluded from hard overrides

---

## Phase 2: PLAN (Iteration 1)
**Objective:** Refine plan with findings from ANALYZE
**Status:** `in_progress`

### Checklist
- [ ] 2.1 Validate HARD_CATEGORIES decision (only CRYPTO_WEB3)
- [ ] 2.2 Confirm HARD_KEYWORD_OVERRIDES list
- [ ] 2.3 Verify three-tier routing logic covers all cases
- [ ] 2.4 Validate rescue tokenizer approach
- [ ] 2.5 Confirm circuit breaker design

---

## Phase 3: EXECUTE (Iteration 2)
**Objective:** Step-by-step implementation with verification
**Status:** `blocked_by_phase_2`

### Tasks
1. Add THESIS_SATURATION_CAPS to thesis_matcher.py
2. Add normalized index + is_hard_negative() to negative_keyword_policy.py
3. Add rejection_type to ThesisFilterResult
4. Add rescue tokenizer functions
5. Update ThesisFilter classification logic
6. Update pipeline rejection block
7. Add circuit breaker

---

## Phase 4: VERIFY (Iteration 3)
**Objective:** Confirm requirements are met
**Status:** `blocked_by_phase_3`

### Verification Commands
```bash
# Test is_hard_negative
python -c "from utils.negative_keyword_policy import is_hard_negative; print(is_hard_negative('blockchain'))"

# Test rescue tokenizer
python -c "from utils.thesis_filter import has_consumer_rescue_signal; print(has_consumer_rescue_signal('fertility,'))"

# Run tests
python -m pytest tests/utils/test_thesis_filter.py -v

# Full evaluation
python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_results_v4.jsonl
```

### Success Criteria
- [ ] Recall: 50%+ (from 4.1%)
- [ ] Precision: 60%+ (from 81%)
- [ ] All existing tests pass
- [ ] Circuit breaker doesn't trip on normal loads

---

## Files to Modify

| File | Changes | Status |
|------|---------|--------|
| utils/negative_keyword_policy.py | Add _norm_kw, normalized_index, HARD_CATEGORIES, HARD_KEYWORD_OVERRIDES, is_hard_negative() | pending |
| utils/thesis_filter.py | Add rejection_type, rescue tokenizer, complete routing logic | pending |
| utils/thesis_matcher.py | Add THESIS_SATURATION_CAPS, update _score_thesis signature | pending |
| workflows/pipeline.py | Update rejection block, add circuit breaker integration | pending |
| tests/utils/test_thesis_filter.py | Add hard/soft tests, rescue tests, routing gap tests | pending |

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
