---
name: enrichment-run-async
description: Run placeholder async enrichments (brand sentiment, community metrics)
  for selected signals.
allowed-tools:
- Bash
- Read
---

# enrichment-run-async

## When to use
- You want to experiment with enrichment features for ranking/scoring.
- You’re prototyping Phase 3 enrichment hooks.

## Inputs
- signal_ids to enrich.

## Workflow
1. Run: `python -m ops.cli quality enrich <signal_id> [<signal_id> ...]`.
2. Review returned JSON; decide whether to persist to a new enrichment table.

## Outputs
- JSON results (best-effort).

## Guardrails
- Current enrichment clients are placeholders and do not call real APIs.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
