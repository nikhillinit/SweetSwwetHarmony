# Risk Register: Thesis Matcher Recall Improvement

## Risk Summary Matrix

| ID | Risk | L | I | Status |
|----|------|---|---|--------|
| R1 | Hard/soft negative split miscategorizes terms | M | H | OPEN - Explicit criteria defined |
| R2 | Normalization change breaks existing matches | L | M | OPEN |
| R3 | Dilution from new keywords lowers existing scores | H | H | MITIGATED - Saturation caps |
| R4 | ThesisFilter changes break pipeline integration | M | H | MITIGATED - Feature flag |
| R5 | Precision drops below acceptable threshold (55%) | M | H | OPEN |
| R6 | 7+ tests fail after hard/soft split | H | M | OPEN - Parameterized tests planned |
| R7 | 5 files call mark_rejected() inconsistently | H | H | MITIGATED - Single handler |
| R8 | Dual filtering systems diverge | M | M | MITIGATED - Shared policy module |
| R9 | Soft-rejected signals enter suppression cache | H | H | MITIGATED - HELD_SOFT routing |
| R10 | Stale status data causes wrong override | M | H | **NEW** - Freshness check added |
| R11 | Re-processing loop for soft rejects | H | M | **NEW** - Escalation counter added |
| R12 | DEFAULT_CAP is arbitrary/wrong | M | M | **NEW** - Derive from actual data |
| R13 | Existing false negatives stuck in REJECTED | H | H | **NEW** - Migration step added |
| R14 | Contradictory soft-reject semantics | H | H | **CLOSED** - HELD_SOFT explicit routing |
| R15 | RejectionController adds complexity layer | M | M | **CLOSED** - Extend existing instead |

**Legend:** L=Likelihood, I=Impact, H=High, M=Medium, L=Low

---

## Detailed Risk Analysis

### R1: Hard/Soft Negative Split Miscategorizes Terms
**Description:** Incorrectly categorizing a hard negative as soft could let B2B companies through.
**Mitigation:**
- Explicit criteria table defined (see 02-technical-plan.md)
- HARD = unambiguous (blockchain, crypto, b2b)
- SOFT = ambiguous (platform, enterprise, saas)
- Rule: When in doubt, classify as SOFT
**Status:** OPEN - criteria defined, implementation pending

### R3: Dilution Risk
**Description:** The formula `score = total_weight / (sum(keywords) * 0.15)` means adding keywords grows the denominator, lowering all existing scores.
**Mitigation:**
- Implement THESIS_SATURATION_CAPS based on POST-normalization state
- Derive DEFAULT_CAP from median of actual caps (not arbitrary 5.0)
- Sequence: normalization → eval → caps (not parallel)
**Status:** MITIGATED by plan change

### R7: Five mark_rejected() Call Sites
**Description:** Inconsistent rejection handling across 5 files leads to split-brain behavior.
**Original files:**
- `workflows/pipeline.py`
- `workflows/notion_pusher.py`
- `collectors/curated_scout.py`
- `consumer/consumer_pipeline.py`
- `consumer/pusher.py`
**Mitigation:**
- Single `handle_rejection()` function in `negative_keyword_policy.py`
- All 5 sites MUST use this function
- Deprecation warning on direct `mark_rejected()` calls
**Status:** MITIGATED by plan change

### R10: Stale Status Data (NEW)
**Description:** Status override relies on Notion status being current. Stale sync could protect things that should be rejected or reject things that should be protected.
**Mitigation:**
- Add freshness check: `status_updated_at > (now - 48h)`
- If status is older than 48h, don't trust override
- Run suppression sync before filtering batch
**Status:** MITIGATED by plan change

### R11: Re-processing Loop for Soft Rejects (NEW)
**Description:** If soft-rejected items stay "pending" and aren't suppressed, they'll be re-collected and re-processed every pipeline run.
**Mitigation:**
- Add `soft_reject_count` metadata
- Increment on each soft-reject
- After 3 soft-rejects, escalate to hard reject
**Status:** MITIGATED by plan change

### R12: DEFAULT_CAP Arbitrary (NEW)
**Description:** Original plan used arbitrary `5.0` for unknown thesis cap.
**Mitigation:**
- Calculate actual thesis keyword sums AFTER normalization
- Use `statistics.median()` of actual caps
- Document derivation in code comments
**Status:** MITIGATED by plan change

### R13: Existing False Negatives Stuck (NEW)
**Description:** The 39 confirmed consumer companies are already in REJECTED state and suppression cache.
**Mitigation:**
- Phase 4 includes migration step
- Batch update: mark_pending() + remove_from_suppression_cache()
- One-time script, run before final eval
**Status:** MITIGATED by plan change

### R14: Contradictory Soft-Reject Semantics (CLOSED)
**Description:** Original plan used `routing=REJECTED` + `rejection_type=soft` which is contradictory.
**Resolution:**
- Use explicit `HELD_SOFT` routing value
- No overloading of REJECTED
- Semantic is clear in enum, not hidden in metadata
**Status:** CLOSED by plan revision

### R15: RejectionController Adds Complexity (CLOSED)
**Description:** Original plan added new `rejection_controller.py` on top of existing systems.
**Resolution:**
- Extend `negative_keyword_policy.py` instead
- Reuse existing infrastructure (categories, weights, validation)
- No new abstraction layers
**Status:** CLOSED by plan revision

---

## Monitoring Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Recall | <30% | <20% | Review hard/soft classification |
| Precision | <55% | <50% | Tighten hard negative list |
| Soft-reject rate | >40% | >60% | Review soft negative list |
| HELD queue size | >200/day | >500/day | Add more keywords or tighten threshold |
