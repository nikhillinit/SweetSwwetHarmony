# Step 4A Observation Window — Closing Summary

**Window:** 2026-03-19 to 2026-03-23 (5 days)
**Branch:** `obs/step4a-window-mar19-23`
**Promotion date:** 2026-03-16T19:15Z

---

## Outcome: PASS

The Step 4A observation window closed on 2026-03-23 with all five daily checkpoints passing and no rollback triggers fired at any point.

---

## Daily Checkpoint Log

| Day | Date | Canary | Gate | Drift | SPC | Notes |
|-----|------|--------|------|-------|-----|-------|
| 1 | 2026-03-19 | pass 93.14% (#48) | ready | in_control | proceed_with_exception | Conditional pass; SPC exception load-bearing; caveats documented |
| 2 | 2026-03-20 | pass 93.14% (#51) | ready | in_control | proceed_with_exception | No variance from Day 1 |
| 3 | 2026-03-21 | pass 93.14% (#52) | ready | in_control | proceed_with_exception | No variance from Days 1/2 |
| 4 | 2026-03-22 | pass 93.14% (#53) | ready | in_control | proceed_with_exception | No variance from Days 1/2/3 |
| 5 | 2026-03-23 | pass 93.14% (#54) | ready | in_control | proceed_with_exception | No variance; full pytest pass; window closed |

---

## Metrics Stability

All monitored metrics were stable across all 5 days with no movement:

| Metric | Value | Status |
|--------|-------|--------|
| Canary pass rate | 93.14% (163/200) | in_control |
| collector_volume | mean=17.17, UCL=110.50, LCL=-76.16 | in_control |
| overall_fp_rate | mean=0.9269, UCL=0.9962, LCL=0.3773 | in_control |
| confidence_calibration_ece | mean=0.5868, UCL=0.9197, LCL=0.1498 | in_control |
| publish_fp_rate | — | insufficient data |
| quarantine_regret | — | insufficient data |
| Activation gate | verdict=ready, can_proceed=true | clear |
| Overdue regret items | 0 | clear |

---

## Full Pytest (Day 5)

```
9402 passed, 112 failed, 31 errors, 3 skipped in 5543.24s
Pass rate: 98.4% (9402/9548)
```

Zero regressions introduced during the observation window. All 112 failures and 31 errors are pre-existing. All tests added during the window pass.

---

## Work Completed During Window

| Day | Commits | Highlights |
|-----|---------|-----------|
| 1 (Mar 19) | 4 | 320 new tests (d26e3d2), HN FP investigation (22d2aef), experiment design + checklist (b29c7bd), summary + cleanup (bb9a62c) |
| 2 (Mar 20) | 6 | Step 4B governance CLI reconciliation, activation docs two-layer gate model, inbox page tests, hunter error detail, KG sanity tests, KG validation skeleton (d51cdbb–47f3715) |
| 3 (Mar 21) | 1 | Checkpoint tick (3191d5c) |
| 4 (Mar 22) | 1 | Checkpoint tick (ba9ba19) |
| 5 (Mar 23) | 1 | Final checkpoint + pytest + this summary |

---

## Standing Caveats (unchanged throughout window)

1. **SPC exception is load-bearing.** Bootstrap overrides (SPC_MIN_BASELINE_DAYS=7, SPC_MIN_LABELED_PER_DAY=3) remain active. Default SPC would show Step 4 as blocked on overall_fp_rate. These overrides were documented as a promotion exception, not permanent policy.
2. **collector_volume LCL blind spot.** LCL=-76.1617 means zero collector volume would not trigger an alert.
3. **HN collector at ~100% FP.** Root cause: LLM_THESIS_MODE=shadow, so all HN signals remain thesis_category=UNKNOWN. Fix is enabling LLM_THESIS_MODE=active post-window.

---

## Step 4B Status

| Gate | Status |
|------|--------|
| Repo-enforced (7 clean days on 4A) | **CLEARED** (2026-03-23) |
| Default local policy (regret check) | **PENDING** — due 2026-03-30T19:13:54Z |
| Early promotion option | Requires written sign-off in `2026-03-19-step4b-promotion-checklist.md` |

**Default path:** wait for regret check on 2026-03-30, then promote MERGE_WRITES_ENABLED from shadow → active.

---

## Artifacts

- Checkpoint summaries: `checkpoints/checkpoint-2026-03-19-summary.md` through `checkpoint-2026-03-23-summary.md`
- HN FP investigation: `hn-fp-investigation-2026-03-19.md`
- Monitoring checklist (updated throughout): `monitoring-checklist.md`
- Promotion artifacts root: `artifacts/activation/step4a_promotion_2026-03-16T19-05-16/`
