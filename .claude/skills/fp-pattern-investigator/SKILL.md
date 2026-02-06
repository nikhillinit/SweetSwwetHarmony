---
name: fp-pattern-investigator
description: Investigate detected FP patterns by pulling example signals and summarizing
  likely root causes + next steps.
user-invocable: false
allowed-tools:
- Bash
- Read
---

# fp-pattern-investigator

## When to use
- A pattern report flags an issue and you need to understand 'why' before tuning.

## Inputs
- patterns JSON file.
- optional: output markdown path.

## Workflow
1. Open the pattern JSON and pick the highest impact patterns (high count / high fp_rate).
2. For each pattern, inspect example signal_ids (raw_data, source_api, canonical_key).
3. Write a short markdown investigation: root cause hypotheses + concrete fixes.

## Outputs
- A human-readable investigation writeup (markdown).

## Guardrails
- Avoid broad suppressions unless you can bound impact (category, collector, confidence threshold).

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
