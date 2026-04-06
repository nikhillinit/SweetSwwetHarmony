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

## Evidence Window

The repo does not contain a trustworthy exact promotion minute, but it does
bound the promotion to this UTC evidence window:

- not before `2026-04-04T07:20:49.900541+00:00`
  - the `DELIVERY_MODE` regret check completed then, and Step 4B logically
    follows that gate
- not after `2026-04-04T13:38:54.380441+00:00`
  - the first `alert_acknowledged` row explicitly references "Step 4B promotion"

See `effective-window.json` for the machine-readable window.

## Why `effective_at` Uses Day Precision

The repo evidence establishes the Step 4B promotion day but not a trustworthy
exact timestamp:

- `.env` has `MERGE_WRITES_ENABLED=active`
- the best restore candidate is named `signals.db.pre-step4b-promotion-20260404`
- the April 4 next-priority notes treat Step 4B as already active and due for a
  regret window ending on 2026-04-18

Using `2026-04-04T00:00:00Z` preserves the known date while avoiding a false
claim about the exact promotion minute.

The exact-minute risk is therefore reduced to an evidence-window question, not
an undocumented guess. Future repairs should carry the narrower window when the
source artifacts prove one.

## Result

- `audit_events` now contains a governance `feature_promote` row for
  `MERGE_WRITES_ENABLED`
- the repair was recorded at current write time while preserving the earlier
  effective promotion date in metadata
- `monitoring.feature_gate.get_overdue_regret_checks('signals.db')` remains
  `count=0`, which is expected because the repaired regret window is still open
  through 2026-04-18

## Evidence

- `effective-window.json`
- `feature-promote-output.json`
- `post-repair-audit-events.json`
- `overdue-status.json`
