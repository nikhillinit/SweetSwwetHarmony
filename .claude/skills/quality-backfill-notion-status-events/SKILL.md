---
name: quality-backfill-notion-status-events
description: Sync Notion suppression statuses and record diffs as notion_status_events
  (event log) for downstream outcome labeling.
allowed-tools:
- Bash
- Read
---

# quality-backfill-notion-status-events

## When to use
- You need a time-series of Notion status changes (Passed/Funded/etc).
- You are bootstrapping the quality flywheel and want outcome labels derived from Notion.
- A stakeholder asks: "When did this deal get marked Passed*" (approx via observed time).

## Inputs
- Path to the signals SQLite DB (default: $DISCOVERY_DB_PATH or signals.db).
- Notion env vars (same as pipeline sync): NOTION_TOKEN, NOTION_DB_ID.

## Workflow
1. Run the status sync + event capture: `python -m ops.cli quality sync-status-events --db <db>`.
2. Verify events were recorded (row count increases in `notion_status_events`).
3. If this is the first run, expect mostly baseline events for new keys; subsequent runs capture changes.

## Outputs
- JSON summary including events_inserted, new_keys, changed_keys, observed_at.

## Guardrails
- This captures *observed* transitions (based on sync cadence), not Notion's true change timestamp.
- If sync cadence is low, outcomes may be delayed; keep your SLA window conservative.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
