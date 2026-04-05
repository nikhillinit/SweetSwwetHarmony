# PRD: DB Hardening Remaining Delta

Date: 2026-04-05
Mode: deliberate consensus plan
Requirements source:
- `.omx/specs/deep-interview-next-work-priority.md`
- `.omx/interviews/next-work-priority-20260405T065802Z.md`
Context snapshot:
- `.omx/context/db-hardening-remaining-delta-20260405T070252Z.md`

## Supersession

This document is the active execution source of truth for the current DB hardening lane.

Superseded for execution purposes:
- `.omx/plans/prd-db-hardening-followup.md`
- `.omx/plans/test-spec-db-hardening-followup.md`
- `.omx/plans/db-hardening-followup-delta-spec-ralplan.md`

Those artifacts remain historical planning context only.
Back-annotating them is optional discoverability polish, not a required execution gate.

## Problem Statement

The repo no longer has a full tranche-1 DB hardening problem. Most of the originally planned hardening already landed, but the execution artifacts still describe tranche-1 as if it were mostly open.

The remaining risk is narrower:
1. stale approved plan artifacts can still send execution into replaying landed work,
2. `scripts/restore_db.py` still uses a one-off CLI DB-path contract (`--db`, `DEFAULT_DB`) instead of the repo’s shared `--db-path` / deprecated `--db` pattern,
3. the current CI guardrail catches literal hard-coded production DB paths in the priority script class, but it may miss realistic indirection or helper-bypass regressions if evidence shows such a bypass matters.

## Goals

1. Replace stale execution guidance with a delta-only source of truth.
2. Freeze already-landed tranche-1 work as closed inventory unless reconciliation finds a concrete bug.
3. Decide the remaining `restore_db.py` CLI contract cleanly and keep it bounded.
4. Strengthen guardrails only where current evidence justifies it.
5. Preserve a minimal-diff execution path suitable for a dirty worktree.

## Non-Goals

1. Replaying or re-hardening already-landed tranche-1 mechanics without evidence of a defect.
2. Repo-wide cleanup of every parser default or docs reference that mentions `signals.db`.
3. Reopening restore sidecar checkpoint/refusal logic unless reconciliation finds a specific defect.
4. Full DB access unification across the repo.
5. Optional tranche-2 expansion unless the delta execution proves it is still necessary.

## Current State Inventory

### Closed Inventory

These are treated as landed and closed unless reconciliation finds a concrete bug:
- priority scripts now resolve DB paths or avoid direct production-path literals:
  - `scripts/e2e_batch_check.py`
  - `scripts/e2e_batch_approve.py`
  - `scripts/export_labeling_review.py`
  - `scripts/run_backfill.py`
- destructive priority scripts already use explicit intent gates and `DBToolLock`
- repo-local DB-ops ledger and `scripts/db_ops_note.py` already exist
- DB-ops policy docs already exist at `docs/runbooks/db-ops-policy.md`
- targeted CI hardcoded-path guardrail already exists for the four priority scripts
- `scripts/restore_db.py` already has landed sidecar checkpoint/refusal behavior plus `DBToolLock`

### Remaining Open Work

1. port the sandbox-validated restore CLI normalization onto the main branch
2. port the sandbox-validated restore contract tests and targeted CI guardrail onto the main branch
3. port the operator-facing restore docs update onto the main branch
4. verify the main-branch delta without broadening CI beyond the validated scope unless new evidence appears

### Explicitly Deferred

1. repo-wide DB cleanup
2. full DB access unification
3. optional tranche-2 ledger/lock expansion
4. any broad cleanup of parser defaults outside this delta lane

## RALPLAN-DR Summary

### Principles

1. Prefer evidence-backed delta planning over replaying already-landed work.
2. Preserve bounded production-risk reduction scope.
3. Standardize CLI/operator contracts where that removes real ambiguity.
4. Prefer enforceable guardrails over narrative assurances.
5. Keep execution artifacts historically accurate and unambiguous.

### Decision Drivers

1. prevent execution against stale approved artifacts
2. reduce residual ambiguity in restore CLI semantics without reopening landed restore safety logic
3. catch realistic regressions only where the current guardrail is demonstrably too weak

### Viable Options

#### Option A: Amend the old PRD/test-spec/delta-spec in place

Pros:
- fewer new files
- one artifact family stays current

Cons:
- mixes closed tranche-1 history with the remaining delta
- makes approval lineage harder to read
- increases stale-plan replay risk

#### Option B: Create new delta-specific PRD/test-spec artifacts

Pros:
- clean separation between historical tranche-1 planning and current delta work
- clearer execution scope
- safer handoff to `$ralph` or `$team`

Cons:
- adds a new artifact pair
- requires explicit supersession language

#### Option C: Skip new plan artifacts and hand straight to verification

Pros:
- smallest planning overhead

Cons:
- does not repair stale approved execution guidance
- premature while restore CLI normalization and CI guardrail scope remain unresolved

### Decision

Choose Option B.

Create a new delta-only PRD/test-spec pair that explicitly supersedes the stale execution artifacts while freezing landed tranche-1 work as closed inventory.

### Deliberate-Mode Pre-Mortem

#### Scenario 1: Stale-plan replay

- executor follows April 4 artifacts and re-implements landed tranche-1 mechanics
- impact: noisy diff, duplicate tests/docs, wasted review time
- mitigation: delta-only execution artifacts with explicit closed inventory

#### Scenario 2: Restore CLI drift

- `restore_db.py` remains a one-off interface without an explicit intentional-exception rationale
- impact: operator confusion and inconsistent deprecation/help behavior
- mitigation: port the sandbox-validated shared helper contract and its help/deprecation checks

#### Scenario 3: CI false confidence

- literal-path guardrail passes while a concrete indirection reintroduces risky DB access in the priority script class
- impact: regression lands despite guardrail coverage
- mitigation: keep the validated targeted restore-contract guardrail; require new concrete evidence before any broader CI widening

## Sandbox Validation

Validated in sandbox worktree:
- `.worktrees/db-hardening-remaining-delta-sandbox`

Validated implementation choices:
1. `scripts/restore_db.py` was normalized onto `add_db_path_args()` / `resolve_db_path()` while keeping deprecated `--db` support through the shared helper.
2. A targeted restore-contract CI guardrail was added at `tests/ci/test_restore_db_cli_contract.py`.
3. `tests/scripts/test_db_hardening_priority_scripts.py` was updated to exercise canonical `--db-path` usage plus help/deprecation behavior.
4. `docs/operator-quickstart.md` was updated to use the canonical restore command form.

Sandbox verification:
- `pytest tests/scripts/test_db_hardening_priority_scripts.py tests/scripts/test_backup_restore.py tests/ci/test_no_hardcoded_production_db_access.py tests/ci/test_no_default_signals_db_parser_fallbacks.py tests/ci/test_restore_db_cli_contract.py tests/utils/test_db_tool_lock.py -q`
- Result: `35 passed`

Implication:
- main-branch execution should port this exact validated delta
- no broader non-literal CI widening is currently justified beyond the targeted restore contract guardrail

## Requirements Summary

1. Produce a delta-only plan that is safe to execute on a dirty worktree.
2. Make the already-landed tranche-1 inventory explicit and closed by default.
3. Treat restore sidecar behavior as closed inventory unless a specific bug is found.
4. Port the sandbox-validated `restore_db.py` shared helper contract onto the main branch.
5. Keep CI/test strengthening bounded to the validated restore contract and the existing priority-script guardrail unless new evidence justifies more.

## Acceptance Criteria

1. The new PRD/test-spec pair explicitly distinguishes:
   - closed inventory
   - remaining open work
   - intentionally deferred work
2. The new PRD/test-spec explicitly supersede all three stale execution artifacts for execution purposes.
3. Back-annotating the stale artifacts themselves is treated as optional polish, not a required gate.
4. `scripts/restore_db.py` uses shared `--db-path` / deprecated `--db` helper semantics.
5. Restore sidecar logic is treated as closed inventory unless reconciliation identifies a concrete defect.
6. The existing priority-script guardrail remains bounded to the four priority scripts, and restore gains only the targeted CLI-contract guardrail validated in sandbox.
7. Broader non-literal CI expansion remains out of scope unless new concrete bypass evidence appears.
8. Docs/tests scope is limited to porting the validated restore help/deprecation and operator quickstart changes.
9. Execution remains a minimal-diff delta pass, not a tranche-1 replay.

## Implementation Plan

### Step 1: Reconcile code reality against stale artifacts

Produce a short evidence table with:
- closed inventory already landed
- remaining open work
- intentionally deferred work
- any concrete bug found in closed inventory

### Step 2: Decide restore CLI contract

Port the validated sandbox choice:
- normalize `scripts/restore_db.py` to shared DB-path helper semantics via `add_db_path_args()` / `resolve_db_path()`
- preserve deprecated `--db` support through the shared helper

Constraint:
- do not reopen checkpoint/refusal sidecar behavior unless Step 1 finds a defect

### Step 3: Tighten CI/test guardrails only if justified

Port the validated sandbox coverage:
- keep the existing hardcoded-path guardrail scope on the four priority scripts
- add the targeted restore CLI-contract guardrail validated in sandbox
- do not broaden beyond that unless Step 1 produces a new concrete bypass case

### Step 4: Close narrow docs/test gaps

Port the validated narrow gaps:
- update help/runbook text for the normalized restore contract
- add or adjust the targeted restore tests and restore CI guardrail already proven in sandbox

## Expanded Verification Plan

### Unit

1. supersession declaration presence in the new PRD/test-spec
2. restore argument-resolution behavior under the normalized shared-helper contract
3. targeted restore CLI-contract guardrail logic

### Integration

1. `scripts/restore_db.py --help` and deprecation/help behavior under the normalized contract
2. guardrail tests for the existing priority-script rule plus the new restore CLI-contract test
3. verify restore participates in the same helper contract as the other normalized scripts

### E2E

1. targeted CLI invocation for `restore_db.py` under the normalized argument contract
2. one representative priority-script guardrail pass plus the targeted restore-contract guardrail pass
3. no broad replay of already-landed priority-script hardening

### Observability

1. operator-facing restore messages remain explicit
2. docs continue to separate repo-local guarantees from external/manual DB actions
3. supersession language in the new artifacts makes the active execution boundary unambiguous

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Executors still follow stale April 4 artifacts | replay of landed work | explicit supersession in new artifacts |
| Restore contract stays inconsistent | operator confusion / uneven future guardrails | port the sandbox-validated shared-helper contract |
| CI broadens without evidence | false positives / scope creep | keep only the validated restore-contract guardrail unless new evidence appears |
| Closed inventory is reopened casually | unnecessary churn in dirty tree | minimal-diff constraint + closed inventory table |

## ADR

### Decision

Adopt a delta-only execution plan for the remaining DB hardening lane, backed by a new PRD/test-spec pair that supersedes the stale execution artifacts.

### Drivers

- stale approved artifacts are now the main execution hazard
- restore sidecar safety is already landed; sandbox validated the remaining shared-helper contract choice
- CI strengthening should stay at the validated restore-contract guardrail unless new evidence appears

### Alternatives Considered

- amend the old PRD/test-spec/delta-spec in place
- skip new artifacts and move straight to verification

### Why Chosen

The new delta-only artifact pair is the safest way to preserve historical context while preventing execution against stale tranche-1 assumptions.

### Consequences

- one new artifact pair becomes the active execution source of truth
- already-landed tranche-1 mechanics are frozen as closed inventory by default
- restore and CI work remain tightly bounded to the sandbox-validated remaining delta

### Follow-Ups

- consider later whether broader repo-wide DB-path normalization is still worth doing
- consider later whether the existing CI guardrail pattern should be generalized beyond this lane

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
1. evidence reconciliation and closed-inventory table
2. restore CLI-contract decision and bounded implementation
3. port the validated restore-contract guardrail and targeted tests
4. narrow docs/tests finish-up
5. verification and artifact close-out

### `$team`

Use only if the user wants parallel delivery despite the small delta.

Suggested lanes:
1. restore CLI contract + tests
2. targeted restore guardrail + verification
3. docs/artifact supersession + verification evidence

Suggested reasoning by lane:
- Lane 1: high
- Lane 2: medium
- Lane 3: medium

## Launch Hints

`$ralph ".omx/plans/prd-db-hardening-remaining-delta.md and .omx/plans/test-spec-db-hardening-remaining-delta.md"`

`$team ".omx/plans/prd-db-hardening-remaining-delta.md and .omx/plans/test-spec-db-hardening-remaining-delta.md. Use lanes for restore CLI normalization, the validated restore-contract guardrail, and docs/verification. Keep execution minimal-diff and do not reopen closed tranche-1 inventory without evidence."`

`omx team 3:executor "Execute the DB hardening remaining-delta plan: lane 1 restore CLI contract and tests, lane 2 validated restore-contract guardrail plus verification, lane 3 docs/supersession and verification evidence. Minimal diff only."`

## Team Verification Path

1. Lane outputs are merged back into the delta PRD/test-spec acceptance criteria.
2. Verification lane confirms:
   - supersession language exists in the new artifacts
   - restore behavior matches the chosen CLI contract
   - CI scope stayed bounded and matched the sandbox-validated restore-contract guardrail
   - no already-landed tranche-1 surfaces were reopened without a concrete defect
3. Final owner runs the targeted verification commands and summarizes any residual risk that remains intentionally deferred.
