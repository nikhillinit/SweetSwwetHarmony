---
name: tuning-proposal-apply
description: 'Safely apply deterministic tuning proposal actions (currently: add/update
  v2 negative keywords).'
allowed-tools:
- Bash
- Read
- Write
---

# tuning-proposal-apply

## When to use
- You have a reviewed tuning proposal and want to apply safe config edits.

## Inputs
- proposal YAML path.
- apply flag (otherwise dry-run).

## Workflow
1. Dry-run: `python -m ops.cli quality apply-tuning --proposal /tmp/proposal.yaml`.
2. If output looks correct, apply: `python -m ops.cli quality apply-tuning --proposal /tmp/proposal.yaml --apply`.
3. Commit the config changes and measure impact with `quality stats`.

## Outputs
- JSON summary with applied/skipped actions.

## Guardrails
- Auto-apply scope is intentionally narrow. For broader changes, treat the proposal as guidance and make PRs.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
