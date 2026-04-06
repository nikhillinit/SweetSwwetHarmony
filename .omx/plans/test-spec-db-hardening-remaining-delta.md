# Test Spec: DB Hardening Remaining Delta

Date: 2026-04-05
Companion PRD: `.omx/plans/prd-db-hardening-remaining-delta.md`

## Objective

Verify the remaining DB hardening delta without replaying already-landed tranche-1 work.

## Verification Contract

### Execution-Artifact Contract

1. The new PRD/test-spec pair explicitly supersede:
   - `.omx/plans/prd-db-hardening-followup.md`
   - `.omx/plans/test-spec-db-hardening-followup.md`
   - `.omx/plans/db-hardening-followup-delta-spec-ralplan.md`
2. Back-annotation inside those stale artifacts is optional and not required for execution readiness.

### Closed-Inventory Contract

Treat these as closed unless reconciliation finds a concrete bug:
- priority-script DB-path hardening
- `DBToolLock` / DB-ops ledger surfaces
- restore sidecar checkpoint/refusal logic
- DB-ops policy docs

### Restore Contract

Sandbox validated the chosen CLI shape:
1. normalize `restore_db.py` onto shared `--db-path` / deprecated `--db`
2. keep sidecar checkpoint/refusal logic unchanged unless a concrete defect is discovered

### CI Contract

1. Default guardrail scope remains the four priority scripts.
2. Add the targeted restore CLI-contract guardrail validated in sandbox.
3. Guardrail broadening beyond that requires at least one new concrete bypass case.

## Unit Tests

1. New artifact supersession language is present.
2. Restore argument resolution behaves as designed under the normalized shared-helper contract.
3. The targeted restore CLI-contract guardrail is present and scoped correctly.

## Integration Tests

1. `scripts/restore_db.py --help` reflects the normalized shared-helper contract.
2. Deprecation/help behavior is explicit if both `--db-path` and deprecated `--db` are supported.
3. Guardrail tests remain scoped to the priority script class plus the targeted restore CLI-contract guardrail.

## E2E Tests

1. Representative `restore_db.py` invocation succeeds under the normalized contract on a scratch DB path.
2. Representative CI guardrail pass proves priority-script protection still holds, and the targeted restore-contract guardrail passes.
3. Execution evidence confirms no broad replay of already-landed tranche-1 work occurred.

## Observability Checks

1. New artifacts clearly identify themselves as the active execution source of truth.
2. Operator-facing docs still separate repo-local guarantees from external/manual DB actions.
3. Restore operator messaging remains explicit.

## Command Matrix

### Restore Contract Validation

Run the targeted restore help/CLI checks appropriate to the normalized contract.

### CI Scope Validation

Run the targeted CI guardrail tests for:
- the four priority scripts
- the targeted restore CLI-contract guardrail

## Exit Gates

1. Supersession language exists in the new artifact pair.
2. The closed-inventory table is explicit and respected.
3. The restore CLI-contract normalization is explicit and verified.
4. CI stays bounded to the four priority scripts plus the targeted restore-contract guardrail unless new evidence appears.
5. Docs/tests changes remain delta-sized.

## Not-Tested / Deferred

1. Repo-wide DB cleanup outside this delta lane.
2. Reopening landed sidecar safety logic without a concrete defect.
3. Optional tranche-2 expansion.
