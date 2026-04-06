# Post-Restore Verification - 2026-04-04

## Restore Outcome

`signals.db` now reads back the restored production dataset:

```json
{
  "signals": 612,
  "schema_version": 53,
  "integrity": "ok",
  "processing": {
    "held": 464,
    "pending": 32,
    "pushed": 15,
    "queued": 2,
    "rejected": 98
  },
  "audit_events": 20,
  "suppression_cache": 589,
  "thesis_classifications": 2593,
  "pipeline_runs": 48
}
```

## Command Evidence

### `health-json-pure`

- Result: `ok: true`
- Core status: `HEALTHY`
- Key metrics:
  - `schema_version: 53`
  - `signal_count: 612`

### `health --json --core-only --allow-external-failures`

- Result: `overall_status: HEALTHY`
- Key metrics:
  - `Suppression Cache: 589 entries`
  - `Signal Health: pass`

Note:
- The command still attempts a Notion warmup during pipeline initialization in the sandbox and logs `All connection attempts failed`, but with `--core-only` the reported core status remains healthy and the local DB-backed suppression metrics are valid.

### `pipeline status`

```text
Qualified: 0
Held: 464
Rejected: 98
Pushed: 15
Pending: 32
```

This matches the restored distribution from the verified backup.

### `canary-preflight`

- Result: `PASS`
- Report: [canary_preflight_post_restore.json](/C:/dev/Harmonic/docs/plans/2026-04-04-next-dev-priority/canary_preflight_post_restore.json)
- Key metrics:
  - `schema_version_pre: 53`
  - `schema_version_expected: 53`
  - `writer_exclusivity: ok`

### Regret-Check Visibility

- Overdue regret checks: `0`

## Sync Evidence

`python run_pipeline.py sync --db-path signals.db`

- First attempt inside sandbox failed with `All connection attempts failed`.
- Re-run outside the sandbox succeeded.
- Successful sync output:
  - `Entries synced: 503`
  - warmup fetch: `599` Notion pages
  - processed cache entries: `598`

### Watermark Seed

`python run_pipeline.py sync --db-path signals.db --recovery-override`

- Result: succeeded outside the sandbox
- Outcome:
  - audited recovery override path exercised successfully
  - external watermark file now exists at `.omx/state/signal-count-watermark-a30063c3d0de.json`
  - seeded baseline signal count: `612`

## Remaining Critical Path

The restore-and-evidence branch is complete enough to proceed.

The remaining blocker before secondary cleanup is the recurrence guard:

1. implement external watermark persistence outside `signals.db`
2. enforce warn/continue for read commands
3. enforce fail-closed semantics for write/state-mutating commands
4. add tests for bypass and recovery override behavior
