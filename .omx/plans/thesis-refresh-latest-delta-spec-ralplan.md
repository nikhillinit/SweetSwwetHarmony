# Thesis Refresh Latest: RALPLAN Delta Spec

Date: 2026-04-05
Status: approved consensus plan

## Final Consensus

The next thesis cleanup step is not a generic stale-refresh framework and not a provenance-only historical stamp.

Approved shape:
1. add shared append-only refresh logic for the fixed v1 cohort
2. expose a fixed operator path for that exact cohort only
3. keep v1 unscheduled and scratch-first
4. treat the thesis cleanup lane as incomplete until the live stale latest-row cohort for the pinned window reaches `0`

## Why This Plan, Not A Bigger One

Current repo evidence supports one exact gap:
- latest rows missing provenance in the last 90 days by `signals.created_at`

It does not yet justify:
- a generic stale-taxonomy command
- scheduler automation
- in-place historical stamping as latest-state repair

## Active Artifacts

- `.omx/plans/prd-thesis-refresh-latest.md`
- `.omx/plans/test-spec-thesis-refresh-latest.md`

## Key Constraints

1. Append-only latest-state repair only
2. Fixed 90-day `created_at` cohort in v1
3. No scheduler / no background automation in v1
4. Scratch rehearsal before any bounded live run
5. Live post-run recount to `0` before declaring thesis cleanup complete

## Execution Handoff

Recommended:
- `$ralph` for one owner to keep the code-path lane and the completion evidence lane tightly coupled

Alternative:
- `$team` if you want code, tests, and operational evidence split into separate lanes

## Verification Focus

1. selector is pinned to the fixed v1 cohort
2. refresh rows are latest by both `classified_at` and `id`
3. no in-place history rewrite is used
4. no scheduler path is introduced
5. cleanup completion is tied to verified live recount, not just command existence
