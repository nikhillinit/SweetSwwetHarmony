# Pre-Review Loosening Plan

Date: `2026-04-06`

## Current Branch

The current SweetSweetHarmony branch is `No routing problem detected` based on the refreshed router diagnostic at [summary.md](C:\dev\Harmonic\artifacts\router-diagnostic\2026-04-06\summary.md).

This is therefore a hold-state plan, not an immediate loosening rollout plan.

## Why Loosening Is Deferred

- the current scoring surface is computable
- TP/FP separation is strong
- threshold reachability exists
- the branch logic does not support broader routing expansion right now

## What Stays Ready

If a future branch does justify expansion, these remain the approved pre-review-only loosening moves:

1. `THESIS_REJECTED_TO_THIN_FILES=true`
2. per-source thesis loosening for `HN`, `arxiv`, and `rss_feeds`
3. widening the `NEEDS_REVIEW` upper edge from `0.7` to `0.75`
4. lowering the `NEEDS_REVIEW` lower edge from `0.4` to `0.3`
5. reducing thin-file promotion strictness so HELD items can corroborate into the queue

Do not lower `HIGH_CONFIDENCE_THRESHOLD=0.7` and do not widen any Notion/CRM bypass path.

## What To Execute Now

1. Continue the learning-loop work
2. Keep the MERGE_WRITES regret-window supervision active through `2026-04-18`
3. Re-run the diagnostic if:
   - label volume changes materially
   - routing architecture changes
   - new pre-review sources materially change queue composition

## Working Calibration

These numbers remain working queue assumptions, not permanent truths:

- `K = 200-500` reviewable signals per cycle
- `~10%` contact-worthy floor after review
- `50/session` labeling cap
- `>=10` surfaced candidates by `2026-05-03`

If a future expansion branch opens, validate these against actual queue behavior before rollout.

## Exit Condition

This hold-state plan is superseded when one of the following happens:

1. a future diagnostic lands on `Score collapse confirmed`
2. a future diagnostic lands on `Threshold ceiling only`
3. the learning loop produces enough new evidence to justify a different branch
