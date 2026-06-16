# Learning-Loop-Only Runbook

Date: `2026-04-06`

## Purpose

Run the current SweetSweetHarmony learning loop without changing routing thresholds or enabling any pre-review loosening.

Current branch:

- `no_routing_problem_detected`
- keep `HIGH_CONFIDENCE_THRESHOLD = 0.7`
- do not lower downstream push/CRM thresholds

## Operator Cycle

1. Build a review set from disagreement and ADJ candidates.
2. Review the generated JSON and optional Markdown view.
3. Apply a bounded batch of labels using the canonical `apply-labels` JSON schema.
4. Refresh thesis provenance first if needed.
5. Rerun the router diagnostic on a bounded cadence.

Recommended rerun triggers:

- after each meaningful batch-label session
- or on a weekly/manual checkpoint

## Commands

### 1. Build review set

```powershell
python -m ops.cli quality --db signals.db learning-loop review-set `
  --days 30 `
  --adj-days 90 `
  --limit 200 `
  --out-json .omx\learning-loop\review-set.json `
  --out-md .omx\learning-loop\review-set.md
```

Canonical artifact:

- JSON is the source of truth
- Markdown is a derived operator view

### 2. Apply labels

Prepare a canonical JSON payload with schema `learning_loop_apply_labels.v1`, then run:

```powershell
python -m ops.cli quality --db signals.db learning-loop apply-labels `
  --in-json .omx\learning-loop\apply-labels.json
```

### 3. Refresh thesis provenance if needed

```powershell
python -m ops.cli quality --db signals.db thesis-refresh-latest `
  --limit 200 `
  --model gemini-3.5-flash `
  --prompt-version quality-ops-v1
```

### 4. Rerun router diagnostic

```powershell
python -m ops.cli quality --db signals.db learning-loop rerun-diagnostic `
  --days 90 `
  --out-dir artifacts\router-diagnostic\YYYY-MM-DD
```

Do not overwrite `artifacts\router-diagnostic\2026-04-06`. That directory is the read-only parity baseline for this lane. Each rerun must write to a fresh dated folder.

## Diagnostic Parity Rules

The rerun must stay comparable to:

- [summary.md](C:\dev\Harmonic\artifacts\router-diagnostic\2026-04-06\summary.md)
- [summary.json](C:\dev\Harmonic\artifacts\router-diagnostic\2026-04-06\summary.json)

Required behavior:

- same frozen JSON contract
- same threshold source from `verification/verification_gate_v2.py`
- same branch predicates
- explicit fail-closed branch: `diagnostic_cannot_be_computed`

## Guardrails

- Do not lower `HIGH_CONFIDENCE_THRESHOLD = 0.7`
- Do not encode or ship pre-review loosening in this lane
- Do not add a persistent queue table or scheduler in v1

## Outputs To Watch

- review set JSON/Markdown artifact paths
- batch-label summary JSON printed to stdout
- rerun-diagnostic `summary.json` / `summary.md`
- current branch recommendation from rerun output

## Exit Conditions

This runbook stays current until:

1. a rerun lands on `score_collapse_confirmed`
2. a rerun lands on `threshold_ceiling_only`
3. the learning-loop branch is superseded by a new front-door decision
