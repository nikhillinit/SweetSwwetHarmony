# Canary Failure Runbook

## Overview

The canary checker re-scores a golden set of labeled signals to detect scoring drift. When the pass rate drops below threshold, a canary failure is logged.

## Canary Verdicts

| Verdict | Meaning | Action |
|---------|---------|--------|
| `pass` | Pass rate >= 80% | No action needed |
| `fail` | Pass rate < 80% | Investigate immediately |

## Diagnosis Steps

### 1. Run a canary check

```bash
python run_pipeline.py canary --db signals.db
```

### 2. Check stratified results

The canary produces stratified breakdowns by:
- **Recency bucket**: 30/60/90 day cohorts — identifies if drift is recent or long-standing
- **Archetype**: Per-category breakdown — identifies which category regressed

### 3. Identify failing signals

Look at individual drift alerts in the canary run output. Each failing signal shows:
- Signal ID and company name
- Expected vs actual score
- Drift magnitude

### 4. Common failure causes

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| Golden set stale | Many old signals failing | Refresh golden set with recent labeled data |
| Thesis matcher change | Sudden drop after code change | Review recent thesis matcher commits |
| Collector data change | Specific collector cohort failing | Check collector output format changes |
| Label quality issue | Random failures, no pattern | Audit recent labels for consistency |

### 5. Recovery

1. If golden set is stale: Run `python -m ops.cli quality export` to get recent labels, rebuild golden set
2. If code change caused regression: Revert the change, verify canary passes
3. If label quality issue: Flag labels for review, exclude disputed labels

## "Why No Alert?" Troubleshooting

If canary fails but no drift alert appears:

1. **Feature flag disabled**: `DRIFT_MONITORING_ENABLED` must be `active` for alerts to be stored
2. **Alert dedup**: A previous canary failure alert with the same signature may already be open. Check: `python run_pipeline.py drift alerts --status open`
3. **Drift detector not wired**: Verify `store_drift_alerts()` is called after canary run in the pipeline
4. **Min-N gates**: SPC alerts require baseline data. If this is the first canary run, no baseline exists yet
5. **Pass rate still above SPC bounds**: A single fail verdict doesn't always trigger SPC — the SPC looks at the metric trend, not individual runs
