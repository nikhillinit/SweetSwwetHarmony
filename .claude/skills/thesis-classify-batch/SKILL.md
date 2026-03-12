---
name: thesis-classify-batch
description: Batch classify recent signals missing thesis_classifications rows using
  the LLM classifier.
allowed-tools:
- Bash
- Read
---

# thesis-classify-batch

## When to use
- You want to enrich the dataset with LLM classifications for analysis/patterns.

## Inputs
- days window, limit, model.

## Workflow
1. Run: `python -m ops.cli quality thesis-classify-batch --days 30 --limit 200`.
2. If errors occur, re-run with --stop-on-error to debug the first failure.

## Outputs
- JSON summary: attempted, succeeded, failed.

## Guardrails
- Be mindful of LLM API quotas; batch sizes should be conservative.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
