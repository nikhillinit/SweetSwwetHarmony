# Step 4B Governance Repair Summary

Date: 2026-04-05

## Goal

Close the `MERGE_WRITES_ENABLED` audit gap without using raw SQL or pretending the
promotion happened at repair-write time.

## Repair Action

- Recorded a `feature_promote` event for `MERGE_WRITES_ENABLED` through
  `python -m governance feature promote`.
- Used:
  - `from_state=shadow`
  - `to_state=active`
  - `regret_due_at=2026-04-18`
  - `effective_at=2026-04-04T00:00:00Z`
  - this summary path as `repair_source`

## Why `effective_at` Uses Day Precision

The repo evidence establishes the Step 4B promotion day but not a trustworthy
exact timestamp:

- `.env` has `MERGE_WRITES_ENABLED=active`
- the best restore candidate is named `signals.db.pre-step4b-promotion-20260404`
- the April 4 next-priority notes treat Step 4B as already active and due for a
  regret window ending on 2026-04-18

Using `2026-04-04T00:00:00Z` preserves the known date while avoiding a false
claim about the exact promotion minute.

## Result

- `audit_events` now contains a governance `feature_promote` row for
  `MERGE_WRITES_ENABLED`
- the repair was recorded at current write time while preserving the earlier
  effective promotion date in metadata
- `monitoring.feature_gate.get_overdue_regret_checks('signals.db')` remains
  `count=0`, which is expected because the repaired regret window is still open
  through 2026-04-18

## Evidence

- `feature-promote-output.json`
- `post-repair-audit-events.json`
- `overdue-status.json`
