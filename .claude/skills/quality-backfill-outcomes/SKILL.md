---
name: quality-backfill-outcomes
description: Infer TP/FP labels for pushed signals from notion_status_events and store
  them in signal_quality_metrics.
allowed-tools:
- Bash
- Read
---

# quality-backfill-outcomes

## When to use
- You want automatic TP/FP labels from CRM outcomes (Passed/Funded) within a time window.
- You need to populate labels before running FP pattern mining.

## Inputs
- signals DB path.
- days_to_count window (default 30 days from push).
- Optional: since_days to limit scanning.

## Workflow
1. Ensure you have status events (run `quality sync-status-events` at least once).
2. Run outcome backfill: `python -m ops.cli quality backfill-outcomes --days-to-count 30`.
3. Validate: sample a few signals and confirm label_source='notion_status_event' and notion_status matches the triggering outcome.

## Outputs
- JSON summary: scanned, labeled, fp, tp, skipped_no_events.

## Guardrails
- Manual labels are preserved unless `--override-manual` is set.
- This is conservative by default: only Passed => FP and Funded => TP (not Source).

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
