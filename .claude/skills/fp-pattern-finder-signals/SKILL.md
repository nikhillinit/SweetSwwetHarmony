---
name: fp-pattern-finder-signals
description: 'End-to-end workflow: detect FP patterns from labels, investigate examples,
  and generate a tuning proposal.'
allowed-tools:
- Bash
- Read
---

# fp-pattern-finder-signals

## When to use
- You want to reduce FP rate and need concrete, data-driven tuning ideas.
- You have new labels (manual or outcome backfill) and want to mine for patterns.

## Inputs
- days window and thresholds for pattern detection.
- output paths for patterns JSON and proposal YAML.

## Workflow
1. 1) Ensure labels exist: run outcome backfill and/or manual labeling.
2. 2) Detect patterns: `python -m ops.cli quality find-patterns --days 30 --out /tmp/patterns.json`.
3. 3) Generate tuning proposal: `python -m ops.cli quality propose-tuning --patterns /tmp/patterns.json --out /tmp/proposal.yaml`.
4. 4) Review proposal notes + actions, then (optionally) apply safe patches with `quality apply-tuning`.

## Outputs
- patterns JSON report + tuning proposal YAML.

## Guardrails
- Do not auto-apply tuning without review; most actions are suggestions, not deterministic fixes.
- Validate impact with `quality stats` after deploying changes.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
