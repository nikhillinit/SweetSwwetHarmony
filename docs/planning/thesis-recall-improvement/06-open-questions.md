# Open Questions: Thesis Matcher Recall Improvement

## Blocking Questions

### Q1: Feature Flag for ThesisFilter Changes?
**Context:** ThesisFilter changes affect the production pipeline routing logic.
**Question:** Should we add a feature flag (`use_hard_soft_negatives`) for gradual rollout?
**Impact:** If no flag, changes are immediate and irreversible without code rollback.
**Recommendation:** Yes - add config option with default True
**Status:** NEEDS DECISION

### Q2: What's the Acceptable Precision Floor?
**Context:** Improving recall may reduce precision from 81%.
**Question:** What's the minimum acceptable precision?
**Options:**
- 60% (aggressive recall improvement)
- 65% (balanced)
- 70% (conservative)
**Current Assumption:** 60% floor
**Status:** NEEDS CONFIRMATION

## Non-Blocking Questions

### Q3: Should "platform" be Hard or Soft?
**Context:** "platform" appears in both B2B ("data platform") and consumer ("wellness platform") descriptions.
**Current Decision:** SOFT (score penalty only, no auto-reject)
**Validation Needed:** Check if any known FPs contain "platform"
**Status:** PROCEED WITH SOFT

### Q4: Ground Truth Quality
**Context:** The 413 POSITIVE labels include many B2B companies mistakenly labeled.
**Question:** Should we re-label ground truth or use only 39 confirmed companies?
**Current Approach:** Use 39 confirmed for primary validation, full 413 for secondary
**Status:** PROCEED WITH CURRENT APPROACH

## Resolved Questions

### Q5: Is ThesisFilter the #1 Blocker? [RESOLVED]
**Answer:** YES - confirmed via agent verification
**Evidence:** Lines 168-169 hard-reject on ANY negative keyword match
