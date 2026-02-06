---
name: quality-export-dataset
description: Export labeled signals (and optional thesis metadata) to CSV/JSONL for
  offline evaluation and analysis.
user-invocable: true
allowed-tools:
- Bash
- Read
---

# quality-export-dataset

## When to use
- You want a dataset for evaluation harnesses, labeling tools, or notebooks.
- You need to share labeled examples with collaborators.

## Inputs
- days window (default 90).
- format: csv or jsonl.
- out path.

## Workflow
1. Run: `python -m ops.cli quality export --days 90 --format csv --out /tmp/quality.csv`.
2. Spot-check the output to ensure title/description extraction looks reasonable.

## Outputs
- CSV or JSONL file containing labeled rows + extracted text fields.

## Guardrails
- raw_data fields vary by collector; extraction is best-effort and may be empty.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
