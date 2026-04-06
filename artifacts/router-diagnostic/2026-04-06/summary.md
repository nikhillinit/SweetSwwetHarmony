# Router Diagnostic Refresh

Date: `2026-04-06`

## Goal

Refresh the current diagnostic against `signals.db` using the existing `2026-02-28` baseline approach, determine whether the branch logic is computable, and select the current SweetSweetHarmony execution branch.

## Inputs

- DB: `signals.db`
- Window: `90` days
- Quality stats command:

```powershell
python -m ops.cli quality --db signals.db stats --days 90
```

## Current Label State

- labeled: `210`
- decisive labels (`TP` + `FP`): `205`
- `TP`: `19`
- `FP`: `186`
- `UNSURE`: `5`
- FP rate: `90.73%`

This is a real quality issue, but the branch question depends on whether the scoring surface actually separates TP from FP and whether the threshold is reachable.

## Join Coverage

The decisive cohort is computable.

- TP/FP rows joined to latest thesis classification with non-null `thesis_fit_score`: `205 / 205`
- latest-row integrity mismatches between max-id and max-classified-at: `0`

## Discrimination Metrics

- `AUC = 0.954867`
- `mean(TP) = 0.802632`
- `mean(FP) = 0.162903`
- `mean separation = 0.639728`
- `score max = 0.9`

Threshold sanity at `0.7`:

- TP above threshold: `19`
- FP above threshold: `29`
- FN below threshold: `0`
- TN below threshold: `157`

## Branch Decision

Current branch: `No routing problem detected`.

Why:

1. mean separation is far above `0.05`
2. `AUC` is far above `0.65`
3. the observed score max is `0.9`, so this is not a threshold-ceiling case
4. the diagnostic is computable; the fallback branch does not apply

Interpretation:

- the system still has a quality problem
- but the quality problem is not explained by the branch conditions that justify the broader SweetSweetHarmony routing/loosening program
- the current front-door branch should therefore stay on the `learning-loop-only` path rather than enabling broader pre-review expansion now

## Recommended Immediate Action

1. Keep the three-state tree, but mark the current branch as `No routing problem detected`
2. Defer pre-review loosening work for now
3. Limit immediate execution to the learning-loop lane plus the still-open MERGE_WRITES regret-window gate

The machine-readable version of this artifact is [summary.json](C:\dev\Harmonic\artifacts\router-diagnostic\2026-04-06\summary.json).
