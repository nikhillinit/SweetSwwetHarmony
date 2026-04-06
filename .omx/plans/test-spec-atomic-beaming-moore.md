# Test Spec: Atomic Beaming Moore Proposal Revision

Date: 2026-04-05
Companion PRD:
- `.omx/plans/prd-atomic-beaming-moore.md`

## Objective

Prove that the external proposal is revised into an executor-safe, live-tree-grounded next-priority guide without reintroducing stale top priorities or non-runnable verification steps.

## Verification Contract

### Ranking contract

1. `MERGE_WRITES_ENABLED` audit-trail investigation plus baseline snapshot is `#1`.
2. support-tooling quarantine decision is `#2`.
3. DB-hardening stale-artifact reconciliation is `#3`.
4. deferred thesis classifier expansion remains deferred.

### Lane `#1` contract

1. The proposal cites the direct DB query absence for `MERGE_WRITES_ENABLED` as the primary live discrepancy.
2. The lane orders work as:
   - authoritative store
   - bypassed governance flow
   - naming/schema fallback
3. The lane includes an executable authoritative-store branch:
   - named API read surface, or
   - explicit readable DB-path derivation/export, or
   - documented transport blocker
4. The lane includes a baseline feature snapshot step.

### Lane `#2` contract

1. The proposal frames support-tooling as a decision lane.
2. Allowed outcomes are promote-with-guardrails, quarantine, or retire.
3. It does not default to implementing or promoting all tooling.

### Lane `#3` contract

1. The proposal references live evidence that `restore_db.py` already landed the shared DB-path contract.
2. The lane is artifact reconciliation / residual-close work only.
3. It does not instruct executors to re-implement already-landed DB hardening.

### Verification-shape contract

1. No `sqlite3` CLI dependency remains.
2. `python -m monitoring.feature_gate snapshot --json` appears as the baseline capture surface.
3. The direct-store query examples are executable in PowerShell.
4. There is no implicit or explicit hard-coded fallback to `signals.db`.

## Concrete Checks

### Live evidence checks

```text
python -m monitoring.feature_gate snapshot --json
```

```text
PowerShell here-string audit queries using GOV_DB_PATH
```

```text
PowerShell/Python readability check for GOV_DB_PATH
```

### Document checks

Confirm the revised external proposal:
- no longer claims `restore_db.py` needs DB-path normalization
- no longer ranks DB-hardening as `#1`
- includes the explicit authoritative-store branch
- includes the support-tooling boundary-decision framing

## Exit Gates

1. The revised external proposal reflects the approved ranking.
2. The revised external proposal contains only executable verification guidance for this environment.
3. The proposal remains a delta over `C:\Users\nikhi\.claude\plans\atomic-beaming-moore.md`, not a new unrelated strategy.
4. All cited live-evidence claims are traceable to the current repo state or direct read-only command evidence.

## Not-Tested / Deferred

1. Actual implementation of the next-priority lane.
2. Support-tooling promotion or quarantine execution.
3. DB-hardening artifact reconciliation execution.
4. Thesis classifier expansion execution.
