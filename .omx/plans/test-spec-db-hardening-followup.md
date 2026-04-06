# Test Spec: DB Hardening Follow-up

Date: 2026-04-04
## Supersession

This artifact is historical. The active execution source of truth is the remaining-delta set:
- `.omx/plans/prd-db-hardening-remaining-delta.md`
- `.omx/plans/test-spec-db-hardening-remaining-delta.md`
- `.omx/plans/db-hardening-remaining-delta-spec-ralplan.md`

Do not use this file as the current DB-hardening test spec.

Companion PRD: `.omx/plans/prd-db-hardening-followup.md`

## Objective

Verify that tranche-1 DB hardening reduces recurrence risk in the exact surfaces implicated by the incident, while keeping the scope bounded and the contract reviewable.

## Verification Contract

### Tranche 1 Required

1. Prioritized ad hoc scripts no longer open production DB paths through hard-coded literals or absolute paths.
2. Destructive prioritized scripts require explicit DB path/intent and whichever lock or exclusivity contract the implementation chooses.
3. `scripts/restore_db.py` either handles WAL/SHM sidecars safely after exclusivity passes or refuses explicitly.
4. CI/test guardrails catch new targeted hard-coded production DB access patterns in the prioritized script class.
5. Operator docs state the limit of repo-local guarantees versus external/manual SQLite operations.

### Tranche 2 Optional

1. Repo-local DB-ops evidence is emitted for destructive prioritized tools only.
2. The evidence trail is clearly documented as repo-local, not universal.

## Unit Tests

1. Shared DB-path resolution for hardened scripts.
2. Destructive-script intent/confirmation parsing.
3. Chosen lock/exclusivity helper behavior, including stale-lock handling where applicable.
4. Restore-sidecar helper logic or refusal-path logic.
5. Tranche 2 only: DB-ops ledger payload shape and file path.

## Integration Tests

1. `scripts/restore_db.py` with sidecars present:
   - safe checkpoint/clear/swap after exclusivity passes, or
   - explicit refusal with operator-facing error
2. `scripts/db_maintenance.py` checkpoint behavior remains compatible with restore hardening.
3. Destructive prioritized scripts respect DB path and confirmation/lock contract on a scratch DB.
4. CI/test rule catches:
   - literal `signals.db` direct-open patterns in targeted scripts
   - absolute production DB paths such as `C:/dev/Harmonic/signals.db`

## E2E Tests

1. Read-mostly prioritized script against a scratch DB path.
2. Destructive prioritized script against a scratch DB with explicit intent/confirmation.
3. Restore against a DB with WAL/SHM sidecars and a verified readable final state.
4. If lock/exclusivity is adopted, stale-lock or active-writer handling behaves as documented.

## Observability Checks

1. Operator-facing restore refusal message is explicit when sidecar ambiguity remains unresolved.
2. Operator-facing docs state what repo-local hardening can and cannot prove.
3. Tranche 2 only: repo-local DB-ops evidence contains script, pid, db path, mode/action, timestamp, and result.

## Command/Artifact Expectations

### Core Surfaces

- prioritized ad hoc scripts
- `scripts/restore_db.py`
- `scripts/db_maintenance.py`
- `utils/db_path_helper.py`
- `utils/monitor_lock.py` or a thin generalized equivalent if adopted

### Guardrail Pattern

Reuse the existing rule style from:
- `tests/ops/test_scheduler_quality.py`

But scope the new rule to:
- real code paths in prioritized scripts
- not docs/examples/help text

## Exit Gates

1. Tranche-1 verification passes.
2. No targeted script still hard-codes live DB access.
3. Restore-sidecar behavior is covered and reviewable.
4. CI/test guardrail exists and is scoped correctly.
5. Operator docs for manual/external DB ops exist.
6. Only after 1-5 may tranche-2 evidence work be treated as in-scope optional expansion.

## Not-Tested / Deferred

1. Full repo-wide cleanup of all parser defaults to `"signals.db"`.
2. Universal attribution for external/manual DB operations.
3. Full DB access unification across the repo.
