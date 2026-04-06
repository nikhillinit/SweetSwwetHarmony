# DB Hardening Follow-up: RALPLAN Delta Spec

Date: 2026-04-04
Status: approved consensus plan

## Supersession

This original follow-up delta spec is historical. The active execution source of truth is the remaining-delta set:
- `.omx/plans/prd-db-hardening-remaining-delta.md`
- `.omx/plans/test-spec-db-hardening-remaining-delta.md`
- `.omx/plans/db-hardening-remaining-delta-spec-ralplan.md`

## Final Consensus

The follow-up hardening work should not be a repo-wide DB rewrite.

Approved shape:

1. Tranche 1:
   - harden the exact prioritized ad hoc scripts with direct hard-coded DB access
   - make `scripts/restore_db.py` sidecar-safe or explicitly refusing
   - add targeted CI/test guardrails for hard-coded production DB access
   - add explicit operator docs for manual/external DB operations
2. Tranche 2:
   - optional repo-local DB-ops evidence for destructive prioritized tools
   - optional broader reusable lock extraction if still justified

## Why This Plan, Not A Bigger One

Architect review pushed the plan to separate must-fix recurrence reducers from optional attribution/polish work.

The incident evidence most strongly supports:
- fixing the known direct-open scripts
- fixing restore-sidecar behavior

The broader ledger/lock work is useful, but only after the first-order gaps are closed.

## Key Changes From Initial Draft

1. Split Option B into tranche 1 vs tranche 2.
2. Tightened the destructive-script contract so it applies only to the actual mutating prioritized scripts.
3. Added an explicit tranche-1 out-of-scope boundary for parser-based tools that merely default to `"signals.db"`.
4. Made the restore-sidecar contract concrete and testable.
5. Expanded the CI rule to catch both literal and absolute production DB access patterns.

## Execution Handoff

Primary artifacts:
- `.omx/plans/prd-db-hardening-followup.md`
- `.omx/plans/test-spec-db-hardening-followup.md`

Recommended execution:
- `$ralph` for a bounded single-owner hardening pass
- `$team` for three lanes: prioritized scripts, restore/exclusivity, CI/docs

## Implementation Validation

Tranche 1 was implemented with these concrete choices:

1. prioritized scripts now resolve DB paths explicitly instead of hard-coding production DB access
2. destructive scripts use explicit `--yes` intent gates plus `BEGIN IMMEDIATE` exclusivity checks
3. `scripts/restore_db.py` chose the refusal branch for live WAL/SHM sidecars rather than trying to checkpoint/clear/swap them implicitly
4. CI/test coverage now catches both literal `signals.db` direct-open patterns and absolute production DB paths in the targeted script set

Implication:
- broader reusable lock extraction and repo-local DB-ops ledger remain optional tranche-2 work, not prerequisites for the now-validated tranche-1 hardening set

## Residual Risk After Planning

Even after this follow-up, manual/external SQLite tools outside the repo will still require explicit operator discipline and cannot be fully governed by repo code alone.
