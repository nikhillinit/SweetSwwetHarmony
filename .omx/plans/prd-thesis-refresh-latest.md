# PRD: Thesis Refresh Latest

Date: 2026-04-05
Mode: deliberate consensus plan
Requirements source:
- user request: `thesis-refresh-latest quality command or equivalent one-off backfill path, then use that to clear the stale latest-row cohort before treating the thesis cleanup lane as done`
Context snapshot:
- `.omx/context/thesis-refresh-latest-20260405T075424Z.md`
Parent plan:
- `.omx/plans/post-v1.6.0-cleanup-validated-plan.md`

## Position In The Thesis Cleanup Lane

This plan is the execution source of truth for **Task 4b** of the thesis cleanup lane:
- refresh the stale latest-row thesis cohort
- then clear that cohort operationally

It supplements, rather than replaces, `.omx/plans/post-v1.6.0-cleanup-validated-plan.md`.

## Problem Statement

The current repo can classify signals that have **no** thesis row at all, but it cannot refresh signals whose **latest** thesis row is stale or missing provenance.

Current evidence:
- latest thesis rows with `model IS NULL OR prompt_version IS NULL`: `545`
- signals missing any thesis row in the same general cleanup lane: `26`

The `26` missing-any-row cohort is already covered by `thesis-classify-batch`. The `545` stale latest-row cohort is not, because current Quality Ops selection stops at signals with no thesis row.

## Goals

1. Add a supported append-only path to refresh stale latest thesis rows.
2. Keep v1 tightly scoped to the exact current cleanup cohort.
3. Separate code-path repair from live execution on `signals.db`.
4. Make the completion gate explicit: the thesis cleanup lane is not done until the live stale latest-row cohort is cleared for the pinned v1 window.

## Non-Goals

1. Reworking `thesis-classify-batch` to handle every stale-row scenario.
2. Introducing a generic stale-taxonomy or open-ended `--where` surface in v1.
3. Mutating historical thesis rows in place to claim latest-state repair.
4. Adding scheduler hooks, cron hooks, or background automation for refresh in v1.
5. Declaring the thesis cleanup lane complete as soon as the command exists.

## Evidence Snapshot

1. `ops/quality/thesis.py` currently supports only signals with no thesis row:
   - `iter_signals_missing_thesis()`
   - `batch_classify_missing_thesis()`
2. `ops/quality_cli.py` has `thesis-classify` and `thesis-classify-batch`, but no refresh-latest path.
3. `scripts/backfill_thesis_provenance.py` is historical provenance stamping via in-place `UPDATE`, not append-only latest-state repair.
4. Downstream readers commonly use the latest row per signal:
   - `MAX(id)`
   - or `classified_at DESC, id DESC`
5. Current tests already anchor quality-batch recency on `signals.created_at`, not `detected_at`.

## Fixed V1 Cohort Contract

V1 is pinned to the exact current cleanup cohort:
- signals where `signals.created_at` is within the last `90` days
- and the latest thesis row for that signal has:
  - `model IS NULL`
  - or `prompt_version IS NULL`

V1 does **not** generalize beyond this cohort.

## RALPLAN-DR Summary

### Principles

1. Preserve latest-state semantics by appending a new row, not rewriting history.
2. Keep v1 narrow and audit-friendly.
3. Separate reusable shared logic from operator-facing execution.
4. Treat live DB execution as a distinct, bounded lane after scratch proof.
5. Do not widen automation or taxonomy until a second real use case exists.

### Decision Drivers

1. Current batch behavior cannot clear the `545` stale latest rows.
2. The repo already treats latest thesis state as “latest row wins,” so repair must create a new effective latest row.
3. Live reclassification risk is materially different from metadata backfill risk.

### Viable Options

#### Option A: Narrow staged path

Build shared append-only refresh logic in `ops/quality/thesis.py`, then expose a fixed v1 operator path for the exact current cohort only.

Pros:
- matches current repo semantics
- keeps shared logic testable
- separates code repair from operations
- avoids premature generic taxonomy design

Cons:
- intentionally not generic
- still adds a new operator surface

#### Option B: Thin one-off wrapper over shared logic

Build the same shared append-only helper, but expose only a narrow one-off script for the current cohort.

Pros:
- smallest permanent CLI footprint
- acceptable if it stays thin

Cons:
- weaker Quality Ops ergonomics
- can become invisible/redundant if a second cohort appears soon

#### Option C: Generic reusable refresh command

Add a broad `thesis-refresh-latest` surface with open-ended stale predicates.

Pros:
- most flexible long-term

Cons:
- over-scoped for the current evidence
- weaker auditability
- invites premature selector design

### Decision

Choose **Option A: narrow staged path**.

Why chosen:
- It solves the actual repo gap while keeping v1 bounded to the exact cohort already validated in the cleanup plan.
- It avoids duplicating selection/persistence logic.
- It gives the cleanup lane a supported path without prematurely creating a general stale-refresh framework.

### Deliberate-Mode Pre-Mortem

#### Scenario 1: Selector drift

- Failure: v1 refreshes a broader set than the current missing-provenance latest cohort.
- Mitigation: fix v1 to the last-90-days `created_at` cohort with latest-row missing provenance only; prove the count on scratch first.

#### Scenario 2: Latest-row ambiguity

- Failure: a refresh row is inserted but does not become newest for all consumers.
- Mitigation: require refresh rows to be newest by both `classified_at` and `id`; forbid backdated inserts; test both orderings.

#### Scenario 3: Premature closure

- Failure: code ships, but the live stale cohort is still non-zero.
- Mitigation: completion gate requires both shipped code-path support and an empirically verified post-run cohort count of `0`.

## Recommendation

Implement a shared selector/helper in `ops/quality/thesis.py` and expose a fixed v1 operator path for the exact current cohort only.

V1 shape:
- append-only latest-row refresh
- `signals.created_at` 90-day window
- latest row missing provenance only
- no scheduler / no background automation

## Implementation Plan

### Step 1: Add a shared stale-latest selector

In `ops/quality/thesis.py`, add a selector for the fixed v1 cohort:
- `signals.created_at >= cutoff_90_days`
- latest thesis row exists
- latest row has `model IS NULL OR prompt_version IS NULL`

Requirements:
- distinct from `iter_signals_missing_thesis()`
- no hidden widening of missing-any-row behavior

Acceptance criteria:
- signals with no thesis row are excluded
- signals with a stale latest row are included
- signals with a fresh latest row are excluded
- the window is explicitly based on `signals.created_at`

### Step 2: Add shared append-only refresh logic

Add shared refresh logic that:
- selects the fixed v1 cohort
- reclassifies through the normal classification path
- appends a new row
- ensures the new row is latest by both `classified_at` and `id`
- forbids backdated refresh inserts
- reports attempted / succeeded / failed counts plus auditable ids

Acceptance criteria:
- refreshed signals gain a new latest row
- historical rows are untouched
- failures remain visible

### Step 3: Expose a fixed operator path

Expose either:
- a narrow `quality thesis-refresh-latest` command
- or a thin one-off wrapper script

V1 constraints:
- fixed cohort only
- no generic stale taxonomy
- no scheduler registration
- no background automation

Acceptance criteria:
- help text or docstring makes v1 scope explicit
- no scheduler hook is introduced

### Step 4: Add the operational runbook

Document:
1. scratch DB rehearsal
2. bounded live run
3. post-run read-only recount

Completion rule:
- scratch rehearsal passes first
- live stale latest-row cohort for the pinned 90-day `created_at` window is reduced to `0`

## Acceptance Criteria

1. The repo has a supported append-only latest-row refresh path for the fixed v1 cohort.
2. V1 is explicitly pinned to:
   - `signals.created_at` last `90` days
   - latest row missing `model` or `prompt_version`
3. Refresh rows are newest by both `classified_at` and `id`.
4. Historical rows are not updated in place to claim latest-state repair.
5. No scheduler or background automation is added in v1.
6. The plan separates code readiness from live execution readiness.
7. The thesis cleanup lane is not considered complete until the live stale cohort count for the pinned v1 window is `0`.

## Expanded Verification Plan

### Unit

1. Selector tests:
   - no-row signal excluded
   - stale latest-row signal included
   - fresh latest-row signal excluded
   - `created_at` window governs inclusion
2. Refresh helper tests:
   - append-only behavior
   - latest by `classified_at`
   - latest by `id`
   - no backdated insert
   - summary accounting

### Integration

1. SQLite-backed latest-row ordering test:
   - stale latest row exists
   - refresh inserts a row
   - new row wins by both `classified_at` and `id`
2. CLI or wrapper help/argument parsing test
3. Explicit verification that no scheduler hook or automation path was added

### E2E

1. Scratch-db rehearsal on a copy of `signals.db`:
   - pre-run stale cohort count recorded
   - bounded refresh run executed
   - post-run stale cohort count decreases
   - refreshed rows now have non-null `model` and `prompt_version`
   - refreshed rows are latest by both orderings
2. Bounded live run only after scratch proof
3. Post-live recount confirms the pinned 90-day cohort is `0`

### Observability

1. Operator output emits attempted / succeeded / failed counts
2. Operator output emits auditable ids or a reproducible sample
3. Runbook explicitly states:
   - scratch-first
   - no scheduler in v1
   - completion depends on post-run recount, not command success alone

## Completion Rule

The thesis cleanup lane is complete only when both are true:

1. The repo ships the supported append-only refresh path for the fixed v1 cohort.
2. The live stale latest-row cohort for the last `90` days by `signals.created_at` is empirically reduced to `0`.

Until then:
- `thesis-classify-batch` only closes the missing-any-row cohort
- `scripts/backfill_thesis_provenance.py` remains historical provenance stamping only

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Selector refreshes too much | unintended reclassification | fixed v1 cohort + scratch count verification |
| New row not truly latest everywhere | inconsistent readers | require latest by both `classified_at` and `id` |
| One-off wrapper drifts from shared logic | inconsistent behavior | wrapper only allowed if thin over shared helper |
| Command exists but lane still marked done | false closure | live post-run recount is mandatory |

## ADR

### Decision

Adopt a narrow staged path: shared append-only refresh logic plus a fixed v1 operator path for the exact current stale latest-row cohort.

### Drivers

- current batch logic cannot refresh latest rows
- latest-row semantics require append-only repair
- live mutation risk requires scratch-first separation

### Alternatives Considered

- thin one-off wrapper over shared logic
- generic reusable stale-taxonomy command

### Why Chosen

It is the smallest supported path that fixes the real repo gap without over-generalizing the operator surface.

### Consequences

- new shared refresh helper logic is added
- v1 operator path is intentionally narrow
- future generalization is deferred until another real use case appears

### Follow-Ups

- consider broader stale-refresh predicates only after a second concrete cohort appears
- decide later whether the v1 operator path should stay in the CLI or migrate to a thinner wrapper

## Available-Agent-Types Roster

- `architect`
- `debugger`
- `executor`
- `test-engineer`
- `verifier`
- `writer`

## Follow-Up Staffing Guidance

### `$ralph`

Recommended default.

Sequential gates:
1. selector/helper implementation
2. operator path implementation
3. scratch rehearsal evidence
4. bounded live execution guidance and recount plan
5. verification close-out

### `$team`

Use only if you want code and operational evidence lanes split in parallel.

Suggested lanes:
1. selector/helper + refresh semantics
2. CLI or thin wrapper + tests
3. runbook / scratch rehearsal / live recount evidence

Suggested reasoning by lane:
- Lane 1: high
- Lane 2: medium
- Lane 3: medium

## Launch Hints

`$ralph ".omx/plans/prd-thesis-refresh-latest.md and .omx/plans/test-spec-thesis-refresh-latest.md"`

`$team ".omx/plans/prd-thesis-refresh-latest.md and .omx/plans/test-spec-thesis-refresh-latest.md. Use lanes for selector/refresh semantics, operator path/tests, and scratch/live evidence planning. Keep v1 pinned to the 90-day created_at cohort and do not add scheduler automation."`

`omx team 3:executor "Execute the thesis-refresh-latest plan: lane 1 shared selector and append-only refresh logic, lane 2 fixed v1 operator path and tests, lane 3 scratch rehearsal/runbook and live recount evidence. No scheduler in v1."`

## Team Verification Path

1. Merge lane outputs back into the PRD/test-spec acceptance criteria.
2. Verify:
   - selector is pinned to the fixed 90-day created_at cohort
   - refresh rows win by both latest-row orderings
   - no in-place historical rewrite is used for latest-state repair
   - no scheduler automation is added
   - scratch-first and live recount guidance is explicit
3. Do not mark the thesis cleanup lane complete until a verified live recount shows the stale latest-row cohort is `0`.
