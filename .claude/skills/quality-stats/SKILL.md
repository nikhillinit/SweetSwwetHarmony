---
name: quality-stats
description: Compute FP/TP rates over a time window (overall and by source_api) from
  signal_quality_metrics.
user-invocable: true
allowed-tools:
- Bash
- Read
---

# quality-stats

## When to use
- You want a quick read of recent FP rate after a tuning change.
- You need to identify the noisiest collectors (highest FP rate).

## Inputs
- days window (default 30).
- min_labeled threshold for per-source display.

## Workflow
1. Run: `python -m ops.cli quality stats --days 30 --min-labeled 10`.
2. If numbers look off, confirm labels exist (backfill outcomes / manual labels).

## Outputs
- Overall JSON + per-source list with fp_rate.

## Guardrails
- Rates are computed over *labeled* signals only; unlabeled signals are ignored.
- Use consistent windows when comparing before/after changes.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
