# Drift Alert Escalation Runbook

## Overview

When the SPC monitor detects an out-of-control condition, a drift alert is created. This runbook covers how to triage, escalate, and resolve drift alerts.

## Alert Lifecycle

```
open → acknowledged → resolved
open → snoozed → open (auto-reopen) → acknowledged → resolved
```

## Severity Levels

| Severity | Meaning | Response Time |
|----------|---------|---------------|
| critical | Pass rate below threshold or sustained SPC violation | Same day |
| warning | Approaching threshold or transient SPC violation | Within 48h |

## Triage Steps

### 1. Acknowledge the alert

```bash
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift ack <alert_id> --reason "Investigating"
```

### 2. Assess impact

- Check the metric and drift category:
  - `concept_drift` (pass_rate_drop) — scoring model may need recalibration
  - `model_drift` (individual_drift) — specific signals drifting from golden set
  - `data_drift` (archetype_regression) — input data distribution shifting
- Review SPC charts in the dashboard: **Drift Monitoring > SPC Charts**
- Check recent collector activity: `python run_pipeline.py metrics --limit 7`

### 3. Determine root cause

| Symptom | Likely Cause | Action |
|---------|-------------|--------|
| FP rate spike | New collector producing low-quality signals | Review collector, adjust thresholds |
| Pass rate drop | Golden set stale or scoring model drift | Update golden set, retrain |
| Collector volume drop | API key expired or rate limited | Check API health, renew key |
| Archetype regression | Category distribution shift | Expand golden set for archetype |

### 4. Resolve or snooze

```bash
# Resolve with explanation
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift resolve <alert_id> --reason "Root cause: stale golden set. Updated via canary rebuild."

# Snooze for known transient issue (max 168 hours)
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift snooze <alert_id> --hours 24
```

## Escalation Path

1. **Operator** (ANALYST role): Ack + investigate + resolve routine alerts
2. **GP** (admin role): Escalate if alert persists >48h or involves scoring model changes
3. **Manual intervention**: If automated recommendations insufficient

## "Why No Alert?" Troubleshooting

If you expect a drift alert but none appears:

1. **Feature flag disabled**: Check `DRIFT_MONITORING_ENABLED` — must be `active`
2. **Min-N gates not met**: SPC requires at least 14 valid baseline days AND 100 total labeled samples. Check: `python run_pipeline.py drift check`
3. **Stale aggregation**: If daily aggregator hasn't run in >48h, no new metrics exist. Check last aggregation timestamp in `quality_metrics_daily`.
4. **Sparse label data**: If `MIN_LABELED_PER_DAY` (10) not met for recent days, those days store NULL values and are excluded from SPC baseline.
5. **Sigma=0 edge case**: If all baseline values are identical, the fallback threshold (5% absolute) is used. Small variations may not trigger.
6. **Alert dedup**: If an identical alert (same signature_key) is already open/snoozed, the occurrence_count increments but no new row is created. Check existing alerts: `python run_pipeline.py drift alerts --status open`
7. **One-sided alerting**: For FP rate, only increases trigger alerts. A decrease is considered good and is not alerted.
