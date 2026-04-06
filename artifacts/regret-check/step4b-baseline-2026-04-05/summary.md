# Step 4B Baseline Summary

Date: 2026-04-05

## Goal

Capture a read-only baseline for the `MERGE_WRITES_ENABLED` Step 4B regret window and explain the current audit-trail discrepancy.

## Authoritative Store

- Read-side verification used `signals.db`.
- `DISCOVERY_DB_PATH` was not set in the shell, but the fail-closed resolver succeeded when pointed at `signals.db`.
- `.env` shows:
  - `MERGE_WRITES_ENABLED=active`
  - `DELIVERY_MODE=batch_publish`
- `.env` did not expose a non-empty `DISCOVERY_API_URL` in the captured transport evidence.

## Baseline Snapshot

- `monitoring.feature_gate snapshot --json` ran successfully in the current shell, but returned an empty `flags` object because it reads process env only.
- A dotenv-loaded snapshot captured the intended active flag state and produced:
  - `MERGE_WRITES_ENABLED=active`
  - `DELIVERY_MODE=batch_publish`
  - snapshot hash `970a3c74a0ae7c61`

Interpretation:
- the feature-gate snapshot surface is real and working
- for this environment, meaningful baseline capture requires either an env-loaded shell or an explicit dotenv-loaded helper path

## Audit Evidence

- `audit_events` currently has `20` rows total and `MAX(id)=20`
- there are no rows where:
  - `entity_id='MERGE_WRITES_ENABLED'`
  - or metadata mentions `MERGE_WRITES_ENABLED`
- the most recent feature-governance rows on `2026-04-04` are:
  - `regret_check` for `DELIVERY_MODE`
  - `regret_check` for `LLM_THESIS_MODE`
- later rows on `2026-04-04` are only `alert_acknowledged` events referencing Step 4B promotion in the reason text

## Current Assessment

Current evidence supports:

1. `MERGE_WRITES_ENABLED` is active in `.env`
2. no corresponding `feature_promote` or `regret_check` audit row exists in `audit_events`
3. there is no evidence of an alternate entity/action naming convention for this promotion in the local DB

Most likely explanation:
- the Step 4B activation happened through a path that bypassed the governance writer, most likely a manual `.env` flip or equivalent process outside the audited governance CLI path

What is not proven:
- whether there is some external system-of-record outside `signals.db`
- whether a retroactive governance event is the correct repair

## Canary / Alert Baseline

- open `canary_drift_alerts`: `9`
- all currently open alerts captured in `open-canary-drift-alerts.json`
- the sampled open alerts are informational improvement alerts, not active critical drift incidents

## Recommended Outcome

Lane `#1` should be treated as:
- discrepancy confirmed
- baseline captured
- repair handoff required

Recommended next step before any future `regret_check` write:

1. decide whether to record a retroactive `feature_promote` event for `MERGE_WRITES_ENABLED`
2. or explicitly document that the Step 4B activation was env-only and outside the governance audit trail
3. only after that decision, decide how the eventual regret-check should be recorded
