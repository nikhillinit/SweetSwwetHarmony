# Decision Log: Thesis Matcher Recall Improvement

## Key Decisions

### D1: Prioritize ThesisFilter over ThesisMatcher
**Date:** 2026-02-04
**Decision:** Fix ThesisFilter hard-reject logic before expanding keywords
**Rationale:**
- Agent verification confirmed lines 168-169 hard-reject on ANY negative keyword
- Even a perfect score can't overcome a single "platform" match
- Expanding keywords without fixing this would have zero impact on affected companies

### D2: Conservative Hard/Soft Split
**Date:** 2026-02-04
**Decision:** Only unambiguous B2B terms go in HARD_NEGATIVES
**Rationale:**
- "platform" appears in many consumer companies (e.g., "wellness platform")
- "enterprise" alone is ambiguous (could be enterprise-grade consumer product)
- Start conservative; can move terms to HARD later with validation

### D3: Targeted vs Broad Keyword Expansion
**Date:** 2026-02-04
**Decision:** Add ONLY keywords needed for the 19 confirmed score=0 companies
**Rationale:**
- Broad expansion risks precision collapse
- 19 specific companies have known descriptions
- Each keyword addition should map to a specific missed company

### D4: Freeze Denominators Before Adding Keywords
**Date:** 2026-02-04
**Decision:** Implement THESIS_SATURATION_CAPS before Task 3
**Rationale:**
- Current formula dilutes scores as keywords are added
- Freezing first ensures existing behavior is preserved
- New keywords improve coverage without penalizing existing matches

### D5: Leverage Existing Infrastructure
**Date:** 2026-02-04
**Decision:** Extend `negative_keyword_policy.py` instead of creating new `rejection_controller.py`
**Rationale:**
- Existing module has YAML framework, categories, weights, validation (206 lines)
- Adding new controller creates 4 components instead of 2
- Reusing infrastructure reduces cognitive load and maintenance burden

### D6: Explicit HELD_SOFT Routing
**Date:** 2026-02-04
**Decision:** Use explicit `HELD_SOFT` enum value instead of `REJECTED` + metadata
**Rationale:**
- `routing=REJECTED` + `rejection_type="soft"` is contradictory
- All downstream consumers would need to check metadata (easy to miss)
- Explicit enum makes "recoverable" semantic clear in the type system
**Alternatives Considered:**
- Metadata approach: Rejected but risk of downstream misinterpretation
- New PENDING state: Conflicts with existing PENDING semantics

### D7: Soft-Rejected Stay Recoverable
**Date:** 2026-02-04
**Decision:** Soft-rejected signals marked HELD_SOFT, NOT written to suppression cache
**Rationale:**
- Suppression cache = permanent loss
- HELD_SOFT allows re-processing if company adds positive signals later
- Escalation counter prevents infinite re-processing loop

### D8: Status Override with Freshness Check
**Date:** 2026-02-04
**Decision:** Only trust status override if `status_updated_at > (now - 48h)`
**Rationale:**
- Notion sync can be stale
- Stale status could protect things that should be rejected
- 48h window balances safety with usability
**Alternatives Considered:**
- No freshness check: Risk of wrong overrides
- Shorter window (24h): Too aggressive, might miss valid overrides

### D9: Soft-Reject Escalation Counter
**Date:** 2026-02-04
**Decision:** After 3 soft-rejects, escalate to hard reject
**Rationale:**
- Prevents infinite re-processing loop
- If a company is soft-rejected 3 times, it's unlikely to improve
- 3 is conservative; can adjust based on observed behavior

### D10: Derive DEFAULT_CAP from Data
**Date:** 2026-02-04
**Decision:** Use `statistics.median(THESIS_SATURATION_CAPS.values())` for unknown thesis
**Rationale:**
- Original plan used arbitrary `5.0`
- Median of actual caps is data-driven
- Adapts if thesis configurations change

### D11: Sequence Normalization Before Caps
**Date:** 2026-02-04
**Decision:** Order: normalization → eval → saturation caps
**Rationale:**
- Normalization changes match rates
- Caps computed on pre-normalization state would be wrong
- Must measure impact of normalization before freezing denominators

### D12: Feature Flag Default OFF
**Date:** 2026-02-04
**Decision:** `USE_HARD_SOFT_NEGATIVES` defaults to `false`
**Rationale:**
- Preserves existing pipeline behavior by default
- Enable in eval/staging first
- Flip ON after metrics gates pass
- Allows rollback without redeploy

### D13: Migration Step for Existing False Negatives
**Date:** 2026-02-04
**Decision:** Batch update 39 confirmed companies: mark_pending() + remove_from_suppression_cache()
**Rationale:**
- These companies are already stuck in REJECTED state
- New logic won't help if they're suppressed
- One-time migration before final evaluation

---

## Pending Decisions

### PD1: Consumer Hard-Disqualifier Unification
**Question:** Should `consumer/thesis_filter/hard_disqualifiers.py` call the same policy module?
**Options:**
1. Yes - Import from `negative_keyword_policy.py` (full unification)
2. No - Keep separate but document divergence
**Status:** NEEDS INVESTIGATION during Phase 2 implementation
**Note:** Plan assumes Option 1 (shared policy), but may need adjustment.

### PD2: Exact Line Numbers for mark_rejected() Calls
**Question:** What are the exact line numbers for the 5 call sites?
**Status:** NEEDS GREP during Phase 2 Task 2.5
**Blocked by:** Codebase audit

---

## Decision Timeline

```
2026-02-04 Iteration 0: D1-D4 (initial plan)
2026-02-04 Iteration 1: D5-D13 (plan revision from critical analysis)
```
