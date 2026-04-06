# PRD: DB Hardening Follow-up

Date: 2026-04-04
## Supersession

This artifact is historical. The active execution source of truth is the remaining-delta set:
- `.omx/plans/prd-db-hardening-remaining-delta.md`
- `.omx/plans/test-spec-db-hardening-remaining-delta.md`
- `.omx/plans/db-hardening-remaining-delta-spec-ralplan.md`

Do not treat this file as the current DB-hardening lane.

Source prompt:
- root cause remains only partially narrowed
- several ad hoc scripts still target `signals.db` directly

Mode: deliberate consensus plan

## Problem Statement

The immediate restore is complete, but the follow-up incident review still leaves two concrete repo-local risks:

1. a small set of ad hoc scripts bypass shared DB-path handling and still open production DB paths directly,
2. `scripts/restore_db.py` still treats restore as a main-file copy problem even though the incident proved WAL/SHM sidecars can determine the effective DB state.

The repo cannot fully prove or disprove manual/external SQLite operations, so the next hardening step should reduce recurrence risk and improve repo-local attribution without drifting into a broad DB-platform rewrite.

## Goals

1. Remove the known unsafe repo-local direct production DB entrypoints.
2. Make restore/maintenance flows sidecar-safe or explicitly refusing under ambiguity.
3. Add enforceable, reviewable rules for the targeted script class.
4. Improve repo-local attribution only where it materially helps future incident response.

## Non-Goals

1. Repo-wide cleanup of every parser-based tool that merely defaults to `"signals.db"`.
2. Full DB access unification across scripts and libraries.
3. Claiming universal attribution for external/manual DB operations.

## Scope Boundary

### Tranche 1: Must-Have

- exact prioritized ad hoc scripts with direct hard-coded DB access
- `scripts/restore_db.py`
- targeted CI/test guardrails for the same script class and restore surface
- operator doc/policy for manual/external DB operations

### Tranche 1 Explicitly Out Of Scope

- broad repo-wide cleanup of parser-based tools that merely default to `"signals.db"`
- docs/examples/help text that mention `signals.db` but do not open it directly
- full DB access unification

### Tranche 2: Optional After Tranche 1

- repo-local DB-ops evidence/ledger for destructive tools
- broader reusable lock extraction if tranche-1 results justify it

## RALPLAN-DR Summary

### Principles

1. Extend current safety surfaces before building new ones.
2. Prioritize destructive and ambiguous DB entrypoints before read-only convenience tools.
3. Separate repo-local attribution improvements from external/manual policy.
4. Enforce production DB access rules with tests/CI, not just convention.
5. Keep the hardening diff bounded and class-based.

### Decision Drivers

1. Highest recurrence-risk reduction per line changed.
2. Lowest new ambiguity in future restore/maintenance flows.
3. Better repo-local evidence without pretending to control external tools.

### Alternatives Considered

#### Option A: Narrow script cleanup only

Pros:
- smallest diff
- fixes the most obvious unsafe entrypoints

Cons:
- leaves restore-sidecar gap untouched
- does less than the incident evidence supports

#### Option B: Balanced safety-surface hardening

Pros:
- matches the actual incident lessons
- reuses `db_path_helper`, `MonitorLock` semantics, `db_maintenance`, restore/preflight surfaces, and existing CI-rule patterns
- supports both recurrence reduction and bounded attribution improvement

Cons:
- moderate scope
- requires strict boundary control to avoid framework creep

#### Option C: Full DB access unification

Pros:
- strongest long-term consistency

Cons:
- too broad for this follow-up
- high churn and migration risk

### Decision

Choose Option B, split into two tranches.

Tranche 1:
- harden the exact prioritized ad hoc scripts
- make `restore_db.py` sidecar-safe or explicitly refusing
- add targeted CI/test guardrails for hard-coded production DB access

Tranche 2:
- add repo-local DB-ops evidence for destructive tools
- broaden reusable lock extraction if tranche-1 results justify it

## Acceptance Criteria

1. The prioritized scripts no longer hard-code `signals.db` or absolute production DB paths:
   - `scripts/e2e_batch_check.py`
   - `scripts/e2e_batch_approve.py`
   - `scripts/export_labeling_review.py`
   - `scripts/run_backfill.py`
2. Destructive ad hoc scripts adopt an explicit safety contract:
   - shared DB-path resolution or explicit flag
   - advisory lock or equivalent exclusivity check when selected by the implementation contract
   - explicit intent flag / confirmation when appropriate
   - minimum destructive-script scope: `scripts/e2e_batch_approve.py`, `scripts/run_backfill.py`
3. `scripts/restore_db.py` has a concrete sidecar contract: when `-wal` / `-shm` are present, restore must either:
   - checkpoint/clear/swap them safely after exclusivity passes, or
   - refuse with an explicit operator-facing error
4. A manual/external DB-ops policy artifact exists stating what repo code can and cannot guarantee.
5. Tranche 2 only: a repo-local DB-ops evidence surface exists for prioritized destructive tools outside `signals.db`.
6. CI/tests reject new targeted hard-coded production DB access patterns in the prioritized script class, including:
   - literal direct opens of `signals.db`
   - absolute production DB paths such as `C:/dev/Harmonic/signals.db`
7. Focused tests cover:
   - script path resolution / explicit flag behavior
   - destructive-script lock or exclusivity behavior
   - restore-sidecar behavior
   - tranche 2 only: DB-ops evidence emission

## Implementation Plan

### Step 1: Classify DB entrypoints by risk

Priority classes:
- read-mostly: `scripts/e2e_batch_check.py`, `scripts/export_labeling_review.py`
- destructive: `scripts/e2e_batch_approve.py`, `scripts/run_backfill.py`
- restore/maintenance: `scripts/restore_db.py`, `scripts/db_maintenance.py`, `scripts/preflight_check.py`

Goal:
- keep tranche 1 tightly bounded to the scripts that matter most

### Step 2: Harden prioritized ad hoc scripts

Actions:
- route DB path handling through `utils/db_path_helper.py` or explicit `--db-path` / `--db`
- remove the absolute production DB path in `scripts/export_labeling_review.py`
- add explicit intent/confirmation surfaces for destructive prioritized scripts
- keep read-only scripts lighter than destructive ones

### Step 3: Make restore/maintenance sidecar-safe

Actions:
- extend `scripts/restore_db.py` so sidecars are part of the restore contract
- reuse or integrate `scripts/db_maintenance.py` checkpoint behavior when helpful
- make refusal semantics explicit if active writer ambiguity remains unresolved

### Step 4: Reuse or extract advisory locking for destructive tools

Actions:
- decide one implementation contract:
  - thin generalized wrapper built from `MonitorLock` semantics, or
  - defer broader lock reuse if restore exclusivity plus explicit confirmations already cover tranche-1 risk
- apply that contract only to destructive prioritized scripts and restore surfaces

### Step 5: Optional tranche-2 repo-local DB-ops evidence

Actions:
- add a small file-based DB-ops ledger for prioritized destructive repo-local scripts only
- record script name, pid, db path, mode/action, timestamp, result
- explicitly document that this is repo-local exclusion/inclusion evidence, not universal attribution

### Step 6: Add CI/test guardrails and operator docs

Actions:
- add CI/test coverage rejecting new targeted hard-coded production DB connection patterns
- scope the rule to real code paths in prioritized scripts, not docs/examples/help text
- add a DB-ops policy/runbook note for manual/external operations

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| One dangerous direct writer remains outside the prioritized set | recurrence risk remains partially hidden | bounded inventory first, CI rule for targeted class |
| Over-hardening read-only tools creates friction | operators bypass safer paths | keep script classes distinct and tranche 1 narrow |
| Restore sidecar logic is wrong under active writes | future recovery corrupts or masks state | explicit exclusivity/refusal contract |
| Repo-local ledger is mistaken for universal attribution | false confidence | pair with explicit external/manual policy language |
| Lock reuse becomes inconsistent | fragmented safety surface | choose one explicit lock contract or defer broader reuse |

## ADR

### Decision

Adopt a tranche-aware balanced safety-surface hardening package:
- tranche 1: harden prioritized ad hoc DB scripts, make restore sidecar-aware, enforce targeted CI/tests, and document external/manual policy
- tranche 2: optionally add repo-local DB-ops evidence and broader reusable lock extraction

### Drivers

- reduce recurrence risk where the incident actually exposed weakness
- improve future repo-local attribution without over-claiming visibility
- keep scope moderate and reviewable

### Alternatives

- script-only cleanup with no restore/lock changes
- full DB access unification across the repo

### Consequences

- moderate multi-file work across scripts, restore tooling, tests, and docs
- lower-risk `default="signals.db"` surfaces remain explicitly out of scope for tranche 1
- external/manual DB operations still require explicit operator discipline

### Follow-Ups

- decide later whether repo-wide `"signals.db"` defaults should be reduced further
- decide whether tranche-2 DB-ops evidence should feed richer ops visibility

## Available-Agent-Types Roster

- `architect`
- `debugger`
- `executor`
- `test-engineer`
- `verifier`
- `writer`

## Follow-Up Staffing Guidance

### `$ralph`

- one owner, sequential gates:
  - classify/bound scope
  - prioritized script hardening
  - restore-sidecar hardening
  - CI/docs enforcement
  - optional tranche-2 evidence work only if still justified

### `$team`

- Lane 1: prioritized script entrypoint hardening
- Lane 2: restore-sidecar and destructive-tool lock/exclusivity hardening
- Lane 3: CI/test guardrails and operator-doc lane

Suggested reasoning by lane:
- Lane 1: medium
- Lane 2: high
- Lane 3: medium

## Launch Hints

### `$ralph`

```text
$ralph .omx/plans/prd-db-hardening-followup.md
```

### `$team`

```text
$team "Execute .omx/plans/prd-db-hardening-followup.md and .omx/plans/test-spec-db-hardening-followup.md. Use lanes for prioritized script DB-path hardening, restore-sidecar plus destructive-tool exclusivity hardening, and CI/docs enforcement. Keep tranche 1 bounded to the exact targeted script class and restore surfaces."
```

```text
omx team 3:executor "Execute the DB hardening follow-up plan: lane 1 prioritized ad hoc script hardening, lane 2 restore-sidecar plus destructive-tool exclusivity hardening, lane 3 CI/tests/docs enforcement. Avoid repo-wide DB access refactors."
```

## Team Verification Path

Before team shutdown, require proof of:

1. prioritized scripts no longer hard-code live DB paths
2. destructive prioritized scripts use the agreed DB path + explicit-intent + lock/exclusivity contract
3. restore sidecar behavior is tested and safe/refusal semantics are explicit
4. if tranche 2 is in scope, repo-local DB-ops evidence is emitted outside the DB
5. CI/tests prevent new literal or absolute production DB path regressions in the targeted script class
6. operator docs clearly state the limit of repo-local attribution
