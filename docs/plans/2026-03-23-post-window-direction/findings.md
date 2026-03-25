# Post-Window Direction: Strategic Assessment

**Date:** 2026-03-23
**Context:** Step 4A observation window CLOSED (PASS). 7-day wait until regret check (2026-03-30).

## Situation

We have a clean window close, stable canary (93.14%, zero variance across 5 days), and 98.4% pytest pass rate. Step 4B is gated until 2026-03-30. The question is: **what's the best use of the next 7 days?**

## Options Evaluated

### Option A: HN FP Fix (LLM_THESIS_MODE=active)
- **Impact:** HIGH — HN has 98.69% FP rate (151/157 signals over 90 days)
- **Root cause:** All HN signals get `thesis_category=UNKNOWN` because LLM classification is shadow-only
- **Evidence:** When LLM *does* classify, thesis filtering achieves ~32% FP rate vs 85% unclassified
- **Effort:** LOW — env var flip + one collection cycle + measurement
- **Risk:** LOW — LLM classification is already running in shadow mode; we're just promoting it
- **Blockers:** None (observation window is closed)

### Option B: Step 4B Prep/Early Promotion
- **Impact:** MEDIUM — enables live merge writes, but shadow mode already captures data
- **Effort:** LOW (governance CLI command)
- **Risk:** MEDIUM — skipping regret check requires written sign-off
- **Blockers:** Regret check gate until 2026-03-30. No compelling reason to override.
- **Verdict:** Wait. The 7-day window is cheap insurance.

### Option C: KG Validation (Execute Designed Runbook)
- **Impact:** MEDIUM — validates knowledge graph ETL pipeline end-to-end
- **Effort:** MEDIUM — 5 phases in runbook, ~2-3 hours execution
- **Risk:** LOW — operates on snapshot copy, no prod impact
- **Blockers:** None
- **Verdict:** Good use of waiting time, but lower priority than HN fix.

### Option D: Step 3B Tier 3 (Multi-Source Convergence / NER)
- **Impact:** HIGH (long-term) — enables entity resolution across collectors
- **Effort:** HIGH — architectural work, needs design phase
- **Risk:** MEDIUM — large scope, could distract from activation cadence
- **Blockers:** Needs NER infrastructure that doesn't exist yet
- **Verdict:** Start design only if HN fix + KG validation leave spare time. Don't implement.

### Option E: Monitoring Hardening
- **Impact:** LOW-MEDIUM — fixes collector_volume LCL blind spot, adds zero-volume alert
- **Effort:** LOW
- **Risk:** LOW
- **Verdict:** Good housekeeping, do after primary work.

### Option F: Branch Cleanup (merge obs branch to main)
- **Impact:** LOW (hygiene) — but important to not accumulate drift
- **Effort:** MINIMAL
- **Verdict:** Do first as a clean starting point.

## Recommendation

**Sequence for the next 7 days:**

```
Day 0 (Mar 23): Branch cleanup — merge obs branch to main, clean start
Day 1 (Mar 24): HN FP fix — flip LLM_THESIS_MODE=active, run HN collection, measure
Day 2 (Mar 25): Measure HN results, backfill 25 unlabeled signals with LLM classification
Day 3-4 (Mar 26-27): KG validation — execute the designed runbook
Day 5-6 (Mar 28-29): HN parser improvements if LLM alone insufficient; monitoring hardening
Day 7 (Mar 30): Regret check → if clear, promote Step 4B
```

**Why this order:**
1. HN fix is the single highest-ROI change — 151 FP signals per quarter is noise that degrades everything downstream
2. KG validation proves out the knowledge graph pipeline while we wait
3. Step 4B happens naturally when the regret check clears
4. Step 3B design can wait — we're still stabilizing the activation ladder

## Key Decision

The one question I'd ask you before proceeding:

**Do you want to merge the obs branch to main first, or keep working on it?**

The branch has served its purpose (observation window tracking). Merging gives us a clean main to branch from for the HN fix work.
