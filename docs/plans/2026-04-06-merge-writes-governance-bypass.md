# MERGE_WRITES Governance Bypass Status

Date: `2026-04-06`

## Purpose

Document the current Step 4B governance state for `MERGE_WRITES_ENABLED` so the SweetSweetHarmony front door reflects the repo's actual condition rather than the earlier unresolved discrepancy.

## Current State

The original governance bypass did happen, but the repo no longer sits in the unresolved state described by the older scrutiny sections.

As of this document:

- `MERGE_WRITES_ENABLED` has a recorded `feature_promote` row in `audit_events`
- the repair was written through the governance CLI
- the repaired metadata records:
  - `from_state=shadow`
  - `to_state=active`
  - `effective_at=2026-04-04T00:00:00Z`
  - `regret_due_at=2026-04-18`
  - `repair_source=artifacts/regret-check/step4b-repair-2026-04-05/summary.md`
- current repo state remains `MERGE_WRITES_ENABLED=active`
- `get_overdue_regret_checks('signals.db')` currently reports `count=0`

## Evidence

- Repair summary: [summary.md](C:\dev\Harmonic\artifacts\regret-check\step4b-repair-2026-04-05\summary.md)
- Audit row: [post-repair-audit-events.json](C:\dev\Harmonic\artifacts\regret-check\step4b-repair-2026-04-05\post-repair-audit-events.json)
- Overdue check: [overdue-status.json](C:\dev\Harmonic\artifacts\regret-check\step4b-repair-2026-04-05\overdue-status.json)

## Operational Interpretation

Gate A is no longer "decide whether to repair the bypass."

Gate A is now:

1. preserve the repaired governance state as the current source of truth
2. do not ship additional loosening before the regret window resolves
3. run and record the supervised regret check on or after `2026-04-18`

## Residual Risk

If a reader follows the older ADR-only recommendation without noticing the repaired audit row, they may duplicate governance work or incorrectly treat the diagnostic refresh as blocked on an already-resolved repair question.
