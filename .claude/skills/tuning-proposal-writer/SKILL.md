---
name: tuning-proposal-writer
description: Convert FP pattern reports into a structured tuning proposal YAML (safe
  auto-actions + human suggestions).
user-invocable: false
allowed-tools:
- Bash
- Read
---

# tuning-proposal-writer

## When to use
- You want a reviewable artifact that proposes changes based on observed FP patterns.

## Inputs
- patterns JSON path.
- out YAML path.

## Workflow
1. Run: `python -m ops.cli quality propose-tuning --patterns /tmp/patterns.json --out /tmp/proposal.yaml`.
2. Review the proposal: actions (auto-applicable) + notes (manual).

## Outputs
- YAML tuning proposal file.

## Guardrails
- Proposal generation is heuristic; treat it as a starting point, not truth.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
