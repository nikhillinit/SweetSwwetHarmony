# PRD: Learning-Loop-Only Operator Workflow

Date: 2026-04-06
Mode: short consensus draft
Requirements source:
- user request: `Create the initial consensus plan draft for the learning-loop-only branch`
Context snapshot:
- `.omx/context/learning-loop-only-20260406T093427Z.md`
Diagnostic basis:
- `artifacts/router-diagnostic/2026-04-06/summary.md`
- `artifacts/router-diagnostic/2026-04-06/summary.json`
Front-door branch:
- `no_routing_problem_detected / learning-loop-only`
Normative branch-predicate source:
- `.omx/plans/sweetharmony-executive-decision-layer.md` Gate B + Three-State Recommendation Tree

## Problem Statement

The current repo already has the core quality primitives needed for operator learning:
- `ops/quality_cli.py` exposes `quality label`, `quality thesis-disagreement-report`, `quality adj-review`, and `quality thesis-refresh-latest`
- `ops/quality/labels.py` persists manual labels through append-only `quality_feedback` plus latest resolved label upsert in `signal_quality_metrics`
- `ops/quality/thesis.py` already provides disagreement reporting and batch thesis refresh support

What is missing is a coherent operator loop that turns those isolated surfaces into one repeatable cycle:
1. disagreement review
2. batch/manual labeling
3. periodic rerun of the router diagnostic

The current `2026-04-06` diagnostic says the branch is `No routing problem detected`, so this lane must improve evidence generation and labeling quality without lowering `HIGH_CONFIDENCE_THRESHOLD=0.7` or planning pre-review loosening.

## Goals

1. Turn the existing quality surfaces into one small, repeatable operator workflow.
2. Reuse current CLI/modules as the primary implementation substrate.
3. Keep v1 small: operator-facing workflow, not broad new infra.
4. Preserve the current router-diagnostic framing so later reruns remain comparable to `2026-04-06`.
5. Improve labeling throughput and auditability without weakening downstream strictness.

## Non-Goals

1. Lowering `HIGH_CONFIDENCE_THRESHOLD=0.7`.
2. Planning or shipping pre-review loosening in this lane.
3. Building a durable workflow service, background scheduler, or new queue storage subsystem in v1.
4. Replacing the current quality-label storage model in `signal_quality_metrics` / `quality_feedback`.
5. Reframing the router diagnostic into a new branch tree or new threshold logic.

## Evidence Snapshot

1. `ops/quality_cli.py` already has operator-relevant entry points:
   - `quality label`
   - `quality thesis-disagreement-report`
   - `quality adj-review`
   - `quality thesis-refresh-latest`
2. `ops/quality/labels.py` already gives the loop a durable labeling spine:
   - append-only feedback events in `quality_feedback`
   - latest resolved label in `signal_quality_metrics`
   - manual labels always win
3. `ops/quality/thesis.py` already gives the loop a review-input surface:
   - `generate_disagreement_report()`
   - `batch_refresh_latest_missing_provenance()`
4. The current router-diagnostic artifact already defines the comparison basis:
   - 90-day window
   - TP/FP join coverage against latest thesis rows
   - branch criteria for `score collapse`, `threshold ceiling`, and `no routing problem detected`
5. No reusable repo-local router-diagnostic command was found during inspection; only the existing artifact and downstream plan references are visible.
6. The current machine-readable contract is visible in `artifacts/router-diagnostic/2026-04-06/summary.json`, including:
   - top-level metadata (`date`, `db_path`, `window_days`)
   - quality stats
   - join coverage
   - discrimination metrics
   - branch recommendation
   - reproduction metadata
7. Concrete repo homes for the planned provider-first implementation already exist:
   - disagreement logic home: `ops/quality/thesis.py`
   - manual/ADJ label semantics home: `ops/quality/labels.py`
   - overall quality stats home: `ops/quality/stats.py`
   - CLI wrapper home: `ops/quality_cli.py`
   - threshold source: `verification/verification_gate_v2.py`

## RALPLAN-DR Summary

### Principles

1. Reuse existing quality storage and CLI surfaces before adding new state.
2. Prefer a thin operator front door over broad infrastructure.
3. Keep the diagnostic rerun comparable to the existing `2026-04-06` basis.
4. Separate evidence-generation improvements from any loosening decision.
5. Fail closed if the diagnostic rerun cannot credibly reproduce the current baseline framing.

### Decision Drivers

1. Smallest path to a real operator loop using already-shipped quality code.
2. Lowest risk of introducing a second workflow state model beside `quality_feedback` and `signal_quality_metrics`.
3. Highest chance of preserving auditability and rerun comparability for later branch decisions.

### Viable Options

#### Option A: Thin operator workflow surface on top of existing quality commands

First extract shared read-only structured providers, then add one small operator-oriented surface in `ops/quality_cli.py` that:
- materializes a review set from structured disagreement and ADJ candidate providers
- supports batch label application by calling the existing manual-label helper
- reruns the router diagnostic through a structured summary provider using the current artifact basis

Pros:
- aligns with the existing CLI
- reuses `label_signal_manual()` and thesis helpers directly
- keeps state in the current DB tables
- provides a concrete operator loop with minimal new surface area

Cons:
- still adds a new command namespace or wrapper
- requires one thin implementation path for diagnostic rerun because no reusable command was found yet

#### Option B: Runbook-only loop using existing discrete commands

Keep the workflow entirely manual:
- generate disagreement report
- call `quality label` one-by-one
- use `quality adj-review`
- rerun the diagnostic via a documented manual procedure

Pros:
- smallest code change
- zero new operator surface

Cons:
- weak repeatability
- higher operator error rate
- no single workflow artifact tying review, labeling, and rerun together
- harder to hand off to `ralph` or `team` as an execution lane

#### Option C: Lightweight persisted queue/orchestration layer

Add new queue persistence and workflow state on top of existing quality primitives.

Pros:
- strongest future workflow control
- richer batching and work tracking

Cons:
- adds a second state model not yet justified
- larger test and migration surface
- conflicts with the explicit preference for a small operator workflow over broad infrastructure

### Decision

Choose **Option A: thin operator workflow surface on top of existing quality commands**.

### Why Chosen

It is the smallest option that still creates a real operator loop rather than a document-only procedure. It reuses the actual repo surfaces already present in `ops/quality_cli.py`, `ops/quality/labels.py`, and `ops/quality/thesis.py`, while avoiding premature queue infrastructure. The provider-first split also avoids coupling the workflow to Markdown/text output and leaves the diagnostic rerun comparable to the `2026-04-06` artifact instead of redefining the branch logic.

## Requirements Summary

1. Provide one small operator front door that can assemble the current review workload from:
   - structured thesis disagreement candidates
   - structured ADJ follow-up candidates
   - optional thesis-refresh-latest output when provenance refresh is needed before rerun
2. Extract shared read-only providers before adding the front door:
   - a disagreement-candidate provider
   - an ADJ-candidate provider
   - a diagnostic-summary provider
   These providers must return structured data and must not depend on Markdown report parsing.
   Pinned homes:
   - disagreement provider home: `ops/quality/thesis.py`
   - ADJ provider home: `ops/quality/labels.py`
   - diagnostic-summary provider home: `ops/quality/stats.py`
   Reuse points:
   - disagreement provider reuses the latest-thesis/disagreement logic now surfaced by `generate_disagreement_report()`
   - ADJ provider reuses the current `quality adj-review` SQL shape while relocating that read path out of `ops/quality_cli.py`
   - diagnostic-summary provider reuses `get_overall_stats()` from `ops/quality/stats.py` for the `quality_stats` block and reads the threshold from `verification/verification_gate_v2.py` rather than re-declaring `0.7`
3. Preserve manual labeling through `label_signal_manual()` so:
   - append-only audit trail remains
   - latest resolved label remains in `signal_quality_metrics`
   - manual labels continue to win
4. Support both:
   - one-by-one labeling via existing `quality label`
   - bounded batch labeling via a thin wrapper over the same helper path
5. Add a reproducible router-diagnostic rerun path that:
   - uses the same branch framing as `artifacts/router-diagnostic/2026-04-06/summary.md`
   - preserves `HIGH_CONFIDENCE_THRESHOLD=0.7`
   - writes a new dated artifact under `artifacts/router-diagnostic/`
   - fails closed if the join/baseline comparison is not credible
6. Freeze the diagnostic JSON contract for parity with `artifacts/router-diagnostic/2026-04-06/summary.json`:
   - `date`
   - `db_path`
   - `window_days`
   - `quality_stats.labeled`
   - `quality_stats.decided`
   - `quality_stats.tp`
   - `quality_stats.fp`
   - `quality_stats.unsure`
   - `quality_stats.adj`
   - `quality_stats.fp_rate`
   - `join_coverage.decisive_joined_rows`
   - `join_coverage.tp_rows`
   - `join_coverage.fp_rows`
   - `join_coverage.latest_row_mismatches`
   - `discrimination.auc`
   - `discrimination.tp_mean`
   - `discrimination.fp_mean`
   - `discrimination.mean_separation`
   - `discrimination.score_max`
   - `discrimination.threshold_0_7.tp`
   - `discrimination.threshold_0_7.fp`
   - `discrimination.threshold_0_7.fn`
   - `discrimination.threshold_0_7.tn`
   - `branch_recommendation.name`
   - `branch_recommendation.reason`
   - `reproduction.quality_stats_command`
   - `reproduction.notes`
   Invariants:
   - `window_days == 90`
   - `join_coverage.latest_row_mismatches == 0` for a computable parity run
   - `quality_stats.decided == quality_stats.tp + quality_stats.fp`
   - `join_coverage.decisive_joined_rows == join_coverage.tp_rows + join_coverage.fp_rows`
   - branch predicates are normative from `.omx/plans/sweetharmony-executive-decision-layer.md`:
     - `score_collapse_confirmed` when `discrimination.mean_separation < 0.05` or `discrimination.auc < 0.65`
     - `threshold_ceiling_only` when separation is acceptable but `discrimination.score_max < HIGH_CONFIDENCE_THRESHOLD`
     - `no_routing_problem_detected` when separation is acceptable and `discrimination.score_max >= HIGH_CONFIDENCE_THRESHOLD`
     - `diagnostic_cannot_be_computed` when join coverage or latest-row integrity is not credible enough to apply the predicates
7. Define explicit fail-closed behavior:
   - if decisive-label joins are insufficient or latest-row integrity is not credible, emit `branch_recommendation.name = diagnostic_cannot_be_computed`
   - do not infer another branch in that case
   - still write a dated artifact that records the failure reason
8. Canonical JSON schema for `review-set`:

```json
{
  "schema_version": "learning_loop_review_set.v1",
  "generated_at": "ISO-8601 timestamp",
  "db_path": "string",
  "window_days": 90,
  "sort_key": ["queue_type", "priority_rank", "detected_at", "signal_id"],
  "items": [
    {
      "signal_id": 123,
      "queue_type": "disagreement|adj",
      "canonical_key": "domain:example.com",
      "company_name": "Example Co",
      "source_api": "hn",
      "detected_at": "ISO-8601 timestamp",
      "priority_rank": 1,
      "reason_code": "kw_high_llm_low|kw_low_llm_high|adj_followup",
      "reason_summary": "string"
    }
  ]
}
```

Minimum required fields:
- top level: `schema_version`, `generated_at`, `db_path`, `window_days`, `sort_key`, `items`
- per item: `signal_id`, `queue_type`, `canonical_key`, `source_api`, `detected_at`, `priority_rank`, `reason_code`, `reason_summary`

Deterministic sort:
- canonical order is `queue_type ASC`, `priority_rank ASC`, `detected_at DESC`, `signal_id DESC`
- the serialized `sort_key` field must record that order explicitly

Failure behavior:
- if any required field is missing or a queue type is unknown, the provider must fail fast and emit no partial `review-set`
- if no rows qualify, emit a valid empty payload with `items: []`, not an error

9. Canonical JSON schema for `apply-labels` input:

```json
{
  "schema_version": "learning_loop_apply_labels.v1",
  "requested_by": "string",
  "requested_at": "ISO-8601 timestamp",
  "sort_key": ["signal_id"],
  "items": [
    {
      "signal_id": 123,
      "label": "TP|FP|UNSURE|ADJ",
      "created_by": "operator",
      "reason": "string",
      "notes": "optional string"
    }
  ]
}
```

Minimum required fields:
- top level: `schema_version`, `requested_by`, `requested_at`, `sort_key`, `items`
- per item: `signal_id`, `label`, `created_by`, `reason`

Deterministic sort:
- canonical order is `signal_id ASC`
- duplicate `signal_id` entries are invalid input, not last-write-wins

Failure behavior:
- reject the entire batch before any DB writes if schema validation fails, a label is outside `TP|FP|UNSURE|ADJ`, the sort order is non-canonical, or duplicate `signal_id` values are present
- once validation passes, per-item persistence still reuses `label_signal_manual()`; any runtime write failure must return an explicit itemized error report and leave previously committed successful items auditable
10. Keep v1 intentionally small:
   - no new DB tables
   - no scheduler/daemon
   - no broad queue subsystem

## Recommended V1 Shape

Implement the lane as a two-layer move.

### Layer 1: Shared read-only structured providers

1. disagreement-candidate provider
   - returns structured disagreement rows for review
   - derived directly from thesis/latest-row data, not Markdown report parsing
   - home: `ops/quality/thesis.py`
2. ADJ-candidate provider
   - returns structured ADJ follow-up rows for review
   - derived directly from current quality-label tables
   - home: `ops/quality/labels.py`
3. diagnostic-summary provider
   - computes the structured router-diagnostic summary
   - emits the frozen JSON contract above
   - owns the explicit `diagnostic_cannot_be_computed` fallback behavior
   - home: `ops/quality/stats.py`
   - computes structured summary only; it does not write files or render Markdown

### Layer 2: Thin `quality` front door

1. `review-set`
   - aggregates disagreement and ADJ candidates from the structured providers
   - outputs a review artifact with JSON as the canonical representation and Markdown as derived presentation for an operator session
2. `apply-labels`
   - consumes a bounded operator-prepared file or list
   - calls `label_signal_manual()` for every item
   - keeps the same audit semantics as manual labeling
3. `rerun-diagnostic`
   - refreshes any required thesis provenance first when needed
   - recomputes the same 90-day diagnostic framing through the diagnostic-summary provider
   - writes a comparable `summary.md` and `summary.json`
   - artifact writing/rendering stays in `ops/quality_cli.py`, not in the provider

The exact command spelling can be decided during execution, but the front door should stay inside or immediately adjacent to `quality` rather than becoming a separate service.

## Implementation Plan

### Step 1: Extract shared read-only structured providers

Touchpoints:
- `ops/quality/thesis.py`
- `ops/quality/labels.py`
- `ops/quality/stats.py`

Actions:
- extract a disagreement-candidate provider from the existing thesis/latest-row data path
- extract an ADJ-candidate provider from the existing quality-label data path
- extract a diagnostic-summary provider in `ops/quality/stats.py` that computes the frozen JSON contract directly and reuses `get_overall_stats()`
- keep all three providers read-only and structured; no Markdown/report parsing dependencies

Acceptance criteria:
- all three providers return structured data
- providers are reusable outside a single CLI surface
- providers introduce no new persistent storage
- provider homes are pinned to `ops/quality/thesis.py`, `ops/quality/labels.py`, and `ops/quality/stats.py`

### Step 2: Define the thin operator front door

Touchpoints:
- `ops/quality_cli.py`

Actions:
- add one small learning-loop namespace or equivalent thin wrapper under `quality`
- keep the front door narrow to the three operator steps above
- make existing subcommands remain callable directly
- make the front door consume the new structured providers rather than report text

Acceptance criteria:
- the operator front door is discoverable in CLI help
- it does not replace or break existing `quality` commands
- it introduces no new persistent storage

### Step 3: Materialize the review set from structured providers

Touchpoints:
- `ops/quality_cli.py`
- `ops/quality/thesis.py`

Actions:
- reuse the disagreement-candidate provider as the primary disagreement queue input
- reuse the ADJ-candidate provider as the secondary revisit queue input
- define deterministic ordering and artifact output for operator sessions
- validate against the canonical `review-set` JSON schema before writing any derived Markdown

Acceptance criteria:
- disagreement candidates are emitted from structured thesis data
- ADJ candidates can be included in the same operator review pass
- the review artifact is reproducible and auditable
- the JSON payload is the canonical source; any Markdown view is derived only

### Step 4: Add bounded batch-label application on top of `label_signal_manual()`

Touchpoints:
- `ops/quality_cli.py`
- `ops/quality/labels.py`

Actions:
- add a thin batch path that parses operator-prepared inputs
- reuse `label_signal_manual()` for persistence and policy
- preserve per-label reason/notes/by metadata
- validate the canonical `apply-labels` schema before any write is attempted

Acceptance criteria:
- batch labeling and single labeling go through the same manual-label semantics
- manual labels still win over inferred labels
- audit rows are emitted for each applied label
- duplicate or malformed batch inputs fail before partial writes begin

### Step 5: Add a reproducible router-diagnostic rerun path

Touchpoints:
- `ops/quality_cli.py`
- `artifacts/router-diagnostic/2026-04-06/summary.md`
- `artifacts/router-diagnostic/2026-04-06/summary.json`

Actions:
- implement reruns through the diagnostic-summary provider
- implement the current 90-day query/metric basis in provider/query logic, using the existing artifact only as the parity oracle
- preserve the same branch criteria and comparison framing
- write rerun artifacts under a new dated folder
- make the rerun fail closed if join coverage or baseline comparability is insufficient
- emit `diagnostic_cannot_be_computed` explicitly when parity cannot be established
- keep file writing in the CLI wrapper so the provider remains a pure structured-summary surface

Acceptance criteria:
- rerun output contains the same core fields as the current `summary.json`
- branch selection still uses the same three computable branches plus one fail-closed fallback
- non-computable runs emit the explicit fallback branch instead of silently selecting another branch
- no threshold lowering or pre-review-loosening logic is introduced
- the threshold comparison is sourced from `verification/verification_gate_v2.py`, not duplicated ad hoc

### Step 6: Write the operator runbook and cadence contract

Touchpoints:
- `docs/runbooks/learning-loop-only.md`

Actions:
- define the operator cycle:
  1. build review set
  2. label or batch label
  3. refresh thesis provenance if needed
  4. rerun diagnostic on a bounded cadence
- define the cadence trigger for rerun:
  - after each meaningful batch labeling session, or
  - on a fixed weekly/manual checkpoint

Acceptance criteria:
- the runbook states when to rerun the diagnostic
- the runbook names the canonical artifact location
- the runbook location is fixed under `docs/runbooks/`
- the runbook explicitly preserves the current branch framing and threshold

## Acceptance Criteria

1. The repo has shared read-only structured providers for:
   - disagreement candidates
   - ADJ candidates
   - diagnostic-summary computation
2. The repo has one small operator workflow surface for the learning-loop lane built on those providers.
3. Review-set generation reuses existing disagreement and ADJ surfaces rather than new queue storage.
4. Batch labeling reuses `label_signal_manual()` and preserves audit/upsert semantics.
5. Existing direct commands remain available:
   - `quality label`
   - `quality thesis-disagreement-report`
   - `quality adj-review`
   - `quality thesis-refresh-latest`
6. Router-diagnostic reruns write dated artifacts that match the current frozen JSON contract closely enough to compare branch outcomes.
7. Non-computable diagnostic runs emit `diagnostic_cannot_be_computed` and do not silently select another branch.
8. The rerun path does not lower `HIGH_CONFIDENCE_THRESHOLD=0.7`.
9. The rerun path does not plan or encode pre-review loosening.
10. No new DB table, scheduler, or durable queue subsystem is added in v1.
11. `review-set` and `apply-labels` each have one canonical JSON schema with deterministic ordering and explicit failure behavior.
12. The diagnostic-summary provider computes structured data only; CLI wrapper owns artifact writing.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Batch path diverges from manual labeling semantics | inconsistent operator outcomes | force all batch writes through `label_signal_manual()` |
| Review-set artifact becomes an untrusted second source of truth | operator confusion | keep truth in DB tables; artifact is derived and reproducible only |
| Diagnostic rerun drifts from the `2026-04-06` basis | branch comparisons become invalid | freeze the JSON contract/invariants and fail closed on non-computable runs |
| Queue/orchestration scope grows during implementation | avoidable infra work | explicit v1 ban on new persistent queue storage and scheduler automation |
| Provenance gaps distort rerun inputs | low-confidence branch decision | allow thesis-refresh-latest as a bounded prerequisite before rerun |

## Verification Steps

1. CLI verification:
   - help text exposes the learning-loop workflow surface
   - existing quality commands still parse and run
2. Provider verification:
   - disagreement and ADJ providers return structured candidate rows directly
   - diagnostic provider emits the frozen contract directly
3. Storage verification:
   - batch path yields the same `quality_feedback` and `signal_quality_metrics` behavior as direct manual labeling
4. Review-set verification:
   - disagreement and ADJ rows appear in the derived operator artifact
   - ordering and filtering are deterministic
5. Diagnostic verification:
   - rerun output includes the frozen summary fields
   - numerical invariants match the current contract
   - branch logic remains the same
   - non-computable runs emit `diagnostic_cannot_be_computed` instead of silently producing another branch
6. Runbook verification:
   - cadence and artifact location are explicit
   - threshold and no-loosening constraints are explicit

## ADR

### Decision

Adopt a thin operator workflow surface on top of the current quality commands and storage model for the learning-loop-only branch.

### Drivers

- existing CLI/modules already cover most of the needed behavior
- the branch needs a real operator loop, not a bigger routing program
- preserving diagnostic comparability matters more than adding workflow infrastructure

### Alternatives Considered

- runbook-only composition of the current commands
- lightweight persisted queue/orchestration layer

### Why Chosen

It is the smallest implementation that gives operators one repeatable loop while reusing the repo's current label and thesis infrastructure.

### Consequences

- shared read-only providers are introduced before the front door
- one thin workflow front door is added
- diagnostic rerun becomes an explicit supported operator path
- v1 remains intentionally non-general and non-scheduled

### Follow-Ups

- reconsider persistent queue state only if operator sessions prove the derived review-set artifact is insufficient
- reconsider scheduler automation only after the manual cadence proves stable and worth automating
- only revisit loosening branches if a later rerun changes the active diagnostic branch

## Available-Agent-Types Roster

- `architect`
- `executor`
- `test-engineer`
- `verifier`
- `writer`
- `debugger`

## Follow-Up Staffing Guidance

### `$ralph`

Recommended default for this lane.

Suggested sequence:
1. extract read-only providers for disagreement, ADJ, and diagnostic-summary data
2. add the thin workflow front door on top
3. wire batch labeling through existing manual-label semantics
4. add diagnostic rerun path with explicit fail-closed fallback
5. add tests and runbook under `docs/runbooks/learning-loop-only.md`
6. verify artifact comparability and no-threshold-change guardrails

Suggested reasoning by phase:
- workflow/front-door design: medium
- labeling and diagnostic semantics: high
- verification and docs: medium

Launch hint:

```text
$ralph ".omx/plans/prd-learning-loop-only.md and .omx/plans/test-spec-learning-loop-only.md. Implement the thin learning-loop operator surface using existing quality commands and storage. Do not add persistent queue infra, do not lower HIGH_CONFIDENCE_THRESHOLD=0.7, and keep router-diagnostic reruns comparable to artifacts/router-diagnostic/2026-04-06."
```

### `$team`

Use when you want delivery and verification lanes in parallel.

Suggested lanes:
1. provider extraction for disagreement, ADJ, and diagnostic-summary data
2. workflow front door + batch-label path using `label_signal_manual()`
3. diagnostic rerun path + frozen artifact contract
4. tests/runbook/verification evidence

Suggested reasoning by lane:
- Lane 1: medium
- Lane 2: high
- Lane 3: high
- Lane 4: medium

Launch hints:

```text
$team "Execute .omx/plans/prd-learning-loop-only.md and .omx/plans/test-spec-learning-loop-only.md. Use lanes for workflow front door and review-set assembly, batch labeling through existing manual-label semantics, router-diagnostic rerun parity with artifacts/router-diagnostic/2026-04-06, and tests/runbook. No persistent queue infra and no threshold change."
```

```text
omx team 4:executor "Execute the learning-loop-only plan: lane 1 extract read-only providers for disagreement, ADJ, and diagnostic-summary data, lane 2 thin quality workflow front door plus batch-label path via label_signal_manual, lane 3 router-diagnostic rerun parity and diagnostic_cannot_be_computed fail-closed behavior against the 2026-04-06 artifact basis, lane 4 tests and docs/runbooks/learning-loop-only.md. Preserve HIGH_CONFIDENCE_THRESHOLD=0.7 and do not add new queue tables or scheduler automation."
```

## Team Verification Path

Before team shutdown or Ralph completion, require proof of:

1. Existing direct quality commands still function.
2. Batch labels and single labels share the same persistence semantics.
3. Review-set artifacts are derived from current thesis disagreement and ADJ surfaces, not new state.
4. Router-diagnostic reruns produce comparable `summary.md` / `summary.json` outputs under a new dated artifact folder.
5. Non-computable runs emit `diagnostic_cannot_be_computed`.
6. The branch logic otherwise remains `score collapse` / `threshold ceiling` / `no routing problem detected`.
7. `HIGH_CONFIDENCE_THRESHOLD=0.7` remains unchanged.
8. No pre-review loosening logic or persistent queue subsystem was introduced.
