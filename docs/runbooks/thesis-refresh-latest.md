# Thesis Refresh Latest Runbook

## Scope

This runbook covers v1 of the stale latest-row thesis refresh lane.

Fixed v1 cohort:
- `signals.created_at` within the last `90` days
- latest thesis row exists
- latest thesis row has `model IS NULL OR prompt_version IS NULL`

V1 does not support generic stale-taxonomy predicates.

## Safety Rules

1. Append-only latest-state repair only.
2. Do not update historical thesis rows in place to claim latest-state repair.
3. Scratch rehearsal is required before any bounded live run.
4. No scheduler hook or background automation in v1.

## Scratch Rehearsal

1. Create a scratch DB copy using existing backup/snapshot tooling.
2. Record the pre-run stale latest-row count for the fixed 90-day `created_at` cohort.
3. Run the refresh operator path on the scratch DB.
4. Recount the stale cohort and inspect a sample of refreshed rows to confirm:
   - latest row now has non-null `model` and `prompt_version`
   - refreshed row is latest by both `classified_at` and `id`

## Bounded Live Run

Only after scratch rehearsal succeeds:

1. take a fresh DB backup
2. record the live pre-run stale latest-row count
3. run the refresh path with an explicit limit
4. record the live post-run stale latest-row count

## Completion Gate

The thesis cleanup lane is not complete until:
- the append-only refresh path exists
- and the live stale latest-row cohort for the fixed 90-day `created_at` window is `0`
