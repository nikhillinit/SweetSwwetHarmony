---
name: canonical-key-remediator
description: Suggest stronger canonical keys for weak name_loc keys by extracting
  domains from raw_data (dry-run report).
allowed-tools:
- Bash
- Read
---

# canonical-key-remediator

## When to use
- FP patterns suggest weak canonical keys are causing duplicates/mis-merges.
- You want a report of candidate domain-based keys for migration planning.

## Inputs
- min_signals threshold, limit, optional fp_only flag.
- optional markdown output path.

## Workflow
1. Run: `python -m ops.cli quality key-suggestions --min-signals 5 --out /tmp/key_suggestions.md`.
2. Inspect suggested domains and validate they correspond to the intended entity.
3. Plan a controlled migration (not auto-applied).

## Outputs
- Markdown report of suggested key upgrades.

## Guardrails
- Do not mass-update canonical keys without a migration plan; many tables reference canonical_key.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
