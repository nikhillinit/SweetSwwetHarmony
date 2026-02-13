# SPC Out-of-Control Runbook

## Overview

The SPC (Statistical Process Control) monitor detects when quality metrics exceed their control limits. An "out-of-control" condition means a metric has moved beyond 3 standard deviations from the baseline mean (or Wilson interval bounds for small samples).

## Metrics Monitored

| Metric | Alert Direction | What It Means |
|--------|----------------|---------------|
| `overall_fp_rate` | One-sided (increase only) | False positive rate rising above baseline |
| `collector_volume` | Two-sided | Unexpected drop or spike in collector output |
| `quarantine_regret` | Two-sided | Deferred signals later approved (regret rate) |
| `confidence_calibration_ece` | Two-sided | Confidence scores becoming miscalibrated |

## SPC Methods

| Condition | Method | Bounds |
|-----------|--------|--------|
| Per-day `n >= 30` | 3-sigma (normal approximation) | mean +/- 3*sigma, clamped [0,1] |
| Per-day `n < 30` | Wilson interval | Wilson score bounds for binomial proportion |
| All values identical (sigma=0) | Fallback | mean +/- 5% absolute |

## Diagnosis Steps

### 1. Run SPC check

```bash
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift check
```

### 2. Review control chart

Open the dashboard: **Drift Monitoring > SPC Charts**. Look for:
- Points outside UCL/LCL bands
- Sustained trends (7+ consecutive points above/below mean)
- Sudden shifts vs gradual drift

### 3. Investigate by metric

#### `overall_fp_rate` above UCL
- A cluster of false positives entered the pipeline
- Check recent collector output quality
- Review thesis matcher changes
- Run: `python -m ops.cli quality find-patterns --days 7`

#### `collector_volume` below LCL
- Collector stopped producing signals
- Check API key status: `python run_pipeline.py health --json`
- Check collector logs for errors
- Verify external API availability

#### `collector_volume` above UCL
- Collector producing unusually many signals (possible data quality issue)
- Check if a new data source was added or a filter was relaxed

#### `quarantine_regret` above UCL
- Too many deferred signals are being later approved
- Triage criteria may be too aggressive
- Review deferral reasons and adjust thresholds

#### `confidence_calibration_ece` above UCL
- Confidence scores no longer match actual outcomes
- Scoring model needs recalibration
- Check if input data distribution has shifted

### 4. Resolution

After identifying root cause:

```bash
# Resolve the alert
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift resolve <alert_id> --reason "Fixed: [description]"
```

## Recommendations Integration

After SPC violations, check automated recommendations:

```bash
python run_pipeline.py drift recommend
```

The recommendation engine detects patterns across recent alerts and suggests specific actions.

## "Why No Alert?" Troubleshooting

If metrics appear abnormal but no SPC alert fires:

1. **Feature flag disabled**: `DRIFT_MONITORING_ENABLED` must be `active`
2. **Insufficient baseline data**: SPC requires `MIN_BASELINE_DAYS` (14) valid days AND `MIN_TOTAL_SAMPLES_FOR_SPC` (100) total labeled samples. Check current data:
   ```bash
   DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift check
   ```
   Look for `insufficient_data` verdicts.
3. **Stale aggregation (>48h)**: The daily aggregator must run to populate `quality_metrics_daily`. Check:
   ```sql
   SELECT MAX(updated_at) FROM quality_metrics_daily;
   ```
   If stale, run: `DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift aggregate`
4. **Sparse label data**: Days with fewer than `MIN_LABELED_PER_DAY` (10) labels store NULL values and are excluded from SPC. Increase labeling cadence.
5. **Sigma=0 fallback**: If all baseline values are identical, the 5% absolute fallback threshold is used. Very small variations may not trigger.
6. **One-sided alerting**: For `overall_fp_rate`, decreases are NOT alerted (they're improvements). Only increases above UCL trigger alerts.
7. **Value within bounds**: The metric may look high visually but still be within 3-sigma control limits. Check the actual UCL/LCL values in the SPC Charts dashboard tab.
