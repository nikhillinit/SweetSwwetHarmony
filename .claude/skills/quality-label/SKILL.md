---
name: quality-label
description: Apply manual TP/FP/UNSURE labels to signals and persist an audit trail
  for quality metrics and pattern mining.
allowed-tools:
- Bash
- Read
---

# quality-label

## When to use
- A human reviewer wants to mark a signal as a false positive or true positive.
- You are curating a gold dataset for evaluation.

## Inputs
- signal_id
- label (TP|FP|UNSURE)
- optional: reason, notes, labeled_by

## Workflow
1. Inspect the signal context (raw_data + why it surfaced).
2. Label it: `python -m ops.cli quality label <signal_id> <TP|FP|UNSURE> --reason "..." --notes "..."`.
3. Confirm: `signal_quality_metrics` has one row for the signal with label_source='manual'.

## Outputs
- feedback_id and label summary.

## Guardrails
- If you’re not confident, use UNSURE rather than guessing.
- Manual labels override inferred labels; that’s intentional.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
