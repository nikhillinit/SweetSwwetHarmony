# Test Spec: Thesis Refresh Latest

Date: 2026-04-05
Companion PRD: `.omx/plans/prd-thesis-refresh-latest.md`

## Objective

Verify a safe append-only path to refresh stale latest thesis rows for the fixed v1 cohort, without conflating code-path support with live cleanup completion.

## Verification Contract

### V1 Cohort Contract

The v1 target is fixed:
- `signals.created_at` within the last `90` days
- latest thesis row exists
- latest thesis row has `model IS NULL OR prompt_version IS NULL`

The command or wrapper must not widen beyond that cohort in v1.

### Latest-Row Contract

Refresh must:
1. append a new row
2. make that row latest by both:
   - `classified_at`
   - `id`
3. avoid in-place updates for latest-state repair
4. forbid backdated refresh inserts

### Operational Contract

1. Scratch rehearsal is required before any bounded live run.
2. No scheduler hook or background automation is allowed in v1.
3. The cleanup lane is not complete until the live post-run stale-cohort recount is `0`.

## Unit Tests

1. Fixed-cohort selector includes stale latest-row signals in the 90-day `created_at` window.
2. Selector excludes:
   - signals with no thesis row
   - signals with fresh latest rows
   - signals outside the pinned 90-day window
3. Refresh helper appends a new row rather than updating historical rows.
4. Refresh helper ensures the new row is latest by both `classified_at` and `id`.
5. Backdated refresh inserts are rejected.

## Integration Tests

1. SQLite-backed refresh for a signal with older history:
   - stale latest row exists
   - refresh appends a row
   - appended row becomes latest by both orderings
2. CLI or thin-wrapper help/argument test proves the v1 path is fixed-cohort only.
3. Explicit test confirms no scheduler registration or background automation path was added.

## E2E Tests

1. Scratch-db rehearsal:
   - pre-run stale cohort count recorded
   - bounded refresh executed
   - post-run stale cohort count decreases
   - refreshed rows now have non-null `model` and `prompt_version`
2. Bounded live run plan is only exercised after scratch success.
3. Post-live recount contract is defined and read-only.

## Observability Checks

1. Operator output includes attempted / succeeded / failed counts.
2. Operator output includes auditable ids or a reproducible sample.
3. Runbook text explicitly states:
   - 90-day `created_at` cohort
   - append-only refresh
   - no scheduler in v1
   - completion depends on post-run recount

## Exit Gates

1. Shared append-only refresh logic exists for the fixed v1 cohort.
2. Latest-row ordering is verified by both `classified_at` and `id`.
3. No in-place historical rewrite is used for latest-state repair.
4. No scheduler/background automation path exists in v1.
5. Scratch rehearsal path is defined.
6. The thesis cleanup lane is not marked complete until the live stale cohort recount reaches `0`.

## Not-Tested / Deferred

1. Generic stale-taxonomy refresh flags.
2. Broader windows beyond the fixed 90-day `created_at` cohort.
3. Scheduler automation for refresh-latest.
4. Historical in-place metadata stamping as a substitute for latest-state repair.
