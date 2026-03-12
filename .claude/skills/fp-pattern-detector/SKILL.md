---
name: fp-pattern-detector
description: Detect recurring FP patterns (collector/category concentration, duplicate
  descriptions, temporal hotspots, weak keys).
allowed-tools:
- Bash
- Read
---

# fp-pattern-detector

## When to use
- You need a machine-readable pattern report to drive tuning proposals.

## Inputs
- days window, min_count, fp_rate_threshold.
- out path for JSON.

## Workflow
1. Run: `python -m ops.cli quality find-patterns --days 30 --min-count 10 --out /tmp/patterns.json`.
2. Open the JSON and review the top patterns; validate with a few example signal_ids.

## Outputs
- JSON with config and patterns list.

## Guardrails
- Patterns are heuristics; always validate with examples before changing code/config.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
