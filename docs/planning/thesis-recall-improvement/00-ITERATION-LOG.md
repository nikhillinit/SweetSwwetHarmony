# Thesis Matcher Recall Improvement: Iteration Log

## Session Metadata
- Start Time: 2026-02-04
- Context Source: Phase 0C evaluation results + external feedback review
- Session Status: IN PROGRESS
- Goal: Improve recall from 4% to 50%+ while maintaining precision above 60%

## Summary of Prior Analysis
- 39 confirmed consumer companies identified as false negatives
- Root cause #1: ThesisFilter hard-rejects on ANY negative keyword (lines 168-169)
- Root cause #2: Missing keywords for 19 companies (score=0.0)
- Root cause #3: Normalization doesn't handle hyphens/slashes
- Root cause #4: Dilution risk from growing keyword dictionaries

## Iterations

### Iteration 0: Forensic Audit & Validation
- **Status:** COMPLETE
- **Objective:** Validate plan against actual codebase state
- **Query:** Audit ThesisFilter, ThesisMatcher, and pipeline integration

**Critical Findings:**
1. **EXISTING INFRASTRUCTURE FOUND**: `utils/negative_keyword_policy.py` (206 lines) already has YAML policy loading with categories (B2B_ENTERPRISE, CRYPTO_WEB3, etc.) - UNUSED but ready!
2. **DUAL FILTERING SYSTEMS**: `consumer/thesis_filter/hard_disqualifiers.py` has SEPARATE hard/soft logic for consumer pipeline
3. **5 files call mark_rejected()**: pipeline.py, notion_pusher.py, curated_scout.py, consumer_pipeline.py, consumer/pusher.py
4. **7+ tests will fail**: Need parameterization for hard/soft keyword types
5. **No recovery mechanism**: REJECTED is permanent - need mark_soft_rejected() method

**Decisions:**
- D5: Leverage existing `negative_keyword_policy.py` infrastructure instead of hardcoding
- D6: Keep soft-rejected signals in 'pending' state with metadata flag (not 'rejected')
- D7: Exclude soft-rejected from suppression cache

---

### Iteration 1: Plan Revision (Critical Analysis)
- **Status:** COMPLETE
- **Objective:** Scrutinize "best-of" strategy for architectural risks
- **Query:** Deep analysis of proposed RejectionController approach

**Critical Findings:**
1. **RejectionController adds complexity without removing any**: Original plan creates 4 components instead of 2
2. **Contradictory soft-reject semantics**: `routing=REJECTED` + `rejection_type="soft"` is confusing
3. **No concrete hard/soft criteria**: "Conservative" and "unambiguous" are subjective
4. **Phase 1 ordering risk**: Normalization affects caps - must sequence correctly
5. **5 call sites problem not solved**: Original plan says "update or centralize" but doesn't commit
6. **Stale status risk ignored**: Status override relies on fresh Notion data
7. **Re-processing loop**: Soft-rejected items would be re-processed every run
8. **DEFAULT_CAP arbitrary**: 5.0 isn't justified, should derive from data
9. **Missing migration step**: 39 false negatives already stuck in REJECTED

**Plan Revisions (D5-D13):**
- D5: Extend `negative_keyword_policy.py` instead of new controller
- D6: Use explicit `HELD_SOFT` routing enum value
- D7: Soft-rejected NOT in suppression cache
- D8: Status override with 48h freshness check
- D9: Soft-reject escalation counter (3 → hard)
- D10: DEFAULT_CAP from median of actual caps
- D11: Sequence: normalization → eval → caps
- D12: Feature flag default OFF
- D13: Migration step for existing false negatives

**Documents Updated:**
- 02-technical-plan.md (v2 - complete revision)
- 03-risk-register.md (6 new risks, 2 closed)
- 04-decision-log.md (9 new decisions)

---

### Iteration 2: Phase 1 Implementation (Scoring Prerequisites)
- **Status:** IN PROGRESS
- **Objective:** Implement normalization and saturation caps
- **Codex Session:** 019c2840-7443-73c0-9e88-70dbe65b1efb

**Codex Audit Findings (Task 1.1 prerequisite):**
1. **Current _normalize** (line 646-647): `return text.lower().strip()`
   - Does NOT handle hyphens, slashes, or underscores
2. **Keyword Weight Sums** (for THESIS_SATURATION_CAPS):
   - CONSUMER_CPG: 15.8
   - CONSUMER_HEALTH_TECH: 13.9
   - TRAVEL_HOSPITALITY: 10.4
   - CONSUMER_MARKETPLACE: 8.3
   - DEFAULT_CAP (median): 12.15

**Post-Normalization Evaluation (Task 1.2):**
- Recall: 3.8% (baseline: 4.1% - no significant change)
- Precision: 88.9% (is_fit), 64.8% (routing)
- **Conclusion:** Normalization alone doesn't improve recall; root cause is hard-reject logic

- **Tasks:**
  - [x] Task 1.1: Implement normalization update ✅
  - [x] Task 1.2: Run post-normalization eval ✅
  - [x] Task 1.3: Compute saturation caps from data ✅ (Codex pre-computed: CPG=15.8, HEALTH=13.9, TRAVEL=10.4, MARKETPLACE=8.3, DEFAULT=12.15)
  - [ ] Task 1.4: Add invariance test (deferred - implement with caps)

---

### Iteration 2.5: Reviewer Corrections (CRITICAL)
- **Status:** COMPLETE
- **Objective:** Validate and incorporate reviewer feedback before Phase 2

**Reviewer Corrections Validated:**
1. **DON'T add HELD_SOFT enum** - would break pipeline branching (falls into else)
2. **DON'T modify schema** - derive hard/soft from existing category field
3. **DON'T put handle_rejection() in negative_keyword_policy.py** - circular imports
4. **mark_rejected() only in 2 files** (not 5):
   - workflows/pipeline.py (lines 1318, 1407, 1575, 1886)
   - workflows/notion_pusher.py (line 614)
5. **Status override not implementable** - ThesisFilter.classify() has no status param
6. **ThesisFilterResult already has rejection_reason** - use this + add rejection_type

**Codebase Audit Findings:**
- RoutingDecision: Only QUALIFIED, HELD, REJECTED (thesis_filter.py:27-31)
- NegativeKeywordCategory: Has B2B_ENTERPRISE, CRYPTO_WEB3, SERVICES, STAGES, EDUCATIONAL, DEVTOOLS
- Can derive HARD from {CRYPTO_WEB3, DEVTOOLS, EDUCATIONAL, STAGES, SERVICES}
- B2B_ENTERPRISE is SOFT (ambiguous)

---

### Iteration 3: Phase 2 Implementation (REVISED per reviewers)
- **Status:** READY TO START
- **Objective:** Implement hard/soft routing WITHOUT breaking changes
- **Tasks:**
  - [ ] Task 2.1: Add is_hard_negative() using category mapping (NO schema change)
  - [ ] Task 2.2: Add rejection_type field to ThesisFilterResult (use with rejection_reason)
  - [ ] Task 2.3: Update ThesisFilter classification logic (hard/soft split)
  - [ ] Task 2.4: Update pipeline thesis rejection to check rejection_type
  - [x] ~~Task 2.5: Update all 5 mark_rejected() call sites~~ (WRONG - only 2 files, handled in 2.4)
  - [x] ~~Task 2.6: Add status override~~ (DROPPED - not implementable without param changes)
  - [ ] Task 2.7: Add feature flag (optional - can test with env var)

---

### Iteration 4: Phase 3 Implementation (Vocabulary)
- **Status:** BLOCKED by Iteration 3
- **Objective:** Add targeted keywords for 19 zero-score companies

---

### Iteration 5: Phase 4 Verification (Migration + Eval)
- **Status:** BLOCKED by Iteration 4
- **Objective:** Migrate false negatives and run final evaluation

---

## Metrics Tracking

| Iteration | Recall | Precision | Notes |
|-----------|--------|-----------|-------|
| Baseline | 4.1% | 81% | Before any changes |
| Post-Normalization | TBD | TBD | After Task 1.1-1.2 |
| Post-Routing | TBD | TBD | After Phase 2 |
| Final | Target: 50%+ | Target: 60%+ | After Phase 4 |

---

## Session Pauses

### Pause 1: After Iteration 1
**Time:** 2026-02-04
**State:** Plan revision complete, ready for Phase 1 implementation
**Next Steps:**
1. Begin Iteration 2 (Phase 1 implementation)
2. Start with Task 1.1: Normalization update
3. Read `utils/thesis_matcher.py` to find exact _normalize method location

**Context to Restore:**
- Baseline: 4.1% recall, 81% precision
- 39 confirmed false negatives
- Root cause: ANY negative keyword triggers rejection
- Solution: Hard/soft split with HELD_SOFT routing
