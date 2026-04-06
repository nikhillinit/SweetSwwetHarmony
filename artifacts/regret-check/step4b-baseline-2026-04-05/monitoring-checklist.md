# Step 4B Monitoring Checklist

Window baseline date: 2026-04-05

## Watch Through The Window

1. `MERGE_WRITES_ENABLED` governance trail
   - any newly recorded `feature_promote` or `regret_check` rows
   - any documented retroactive repair decision

2. Feature-gate baseline consistency
   - `MERGE_WRITES_ENABLED`
   - `DELIVERY_MODE`
   - related flag snapshot hash changes

3. Canary / drift state
   - open alert count
   - any non-info severity alerts
   - any newly acknowledged/resolved alerts tied to Step 4B

4. Operational incidents
   - rollbacks
   - DB restore or maintenance incidents
   - merge-write anomalies

5. Regret-check readiness
   - whether the audit-trail discrepancy is resolved
   - whether the authoritative store for verification is explicit and stable

## Baseline Files

- `feature-gate-snapshot.process-env.json`
- `feature-gate-snapshot.dotenv.json`
- `transport-evidence.json`
- `audit-events-merge-writes.json`
- `audit-events-recent-feature-events.json`
- `open-canary-drift-alerts.json`
- `summary.md`
