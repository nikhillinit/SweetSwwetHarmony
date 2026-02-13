# Canary Monitor Scheduler — Operator Guide

Automated 6-hour heartbeat that runs canary checks, drift monitoring, and activation-readiness gates during the shadow monitoring window.

## Windows Task Scheduler Setup

### 1. Register the schedule (idempotent)

```
python -m ops.cli --db C:\dev\Harmonic\signals.db schedule add-canary-monitor
python -m ops.cli --db C:\dev\Harmonic\signals.db schedule list   # verify
```

### 2. Create Scheduled Task (every 5 minutes for resilience)

```
Program/script:     C:\Python311\python.exe   (or your python path)
Add arguments:      -m ops.cli --db C:\dev\Harmonic\signals.db schedule tick --name canary-monitor-6h
Start in:           C:\dev\Harmonic
```

Trigger: Every 5 minutes (the scheduler cron handles 6h dedup)

**Notes:**
- `--db` MUST come before the subcommand (top-level argparse arg)
- `--db` controls which DB the scheduler reads schedules from AND which DB subprocesses use for canary/drift/activation checks (R4-1 unification)
- "Start in" MUST point to the project root
- The 5-minute polling ensures missed 6h windows recover within 5 minutes
- The scheduler's idempotency_key prevents duplicate runs in the same cron slot

### 3. Verify manually

```
python -m ops.cli --db signals.db schedule tick --name canary-monitor-6h
```

### 4. Check history

```
python -m ops.cli --db signals.db schedule history <id>
```

### 5. Check artifacts

```
dir artifacts\cadence\latest_summary.json
type artifacts\cadence\cadence_ledger.jsonl
```

## Steps Executed

| Step | Command | Policy | Timeout |
|------|---------|--------|---------|
| canary | `monitoring.canary_checker run --db {db} --store-results` | required | 300s |
| drift | `run_pipeline.py drift check --db-path {db}` | required | 300s |
| activation | `run_pipeline.py activation-check --step 2 --json --db-path {db}` | required | 300s |
| shadow-export | `scripts/shadow_report.py export --db-path {db} --since-days 1 --limit 2000` | optional | 300s |

The shadow-export step is only included when `LLM_THESIS_MODE` is `shadow` or `active`.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CANARY_STEP_TIMEOUT_SECONDS` | 300 | Per-step subprocess timeout |
| `LLM_THESIS_MODE` | off | Gates shadow-export step |
| `DISCOVERY_DB_PATH` | signals.db | Set automatically from `--db` |

## Troubleshooting

- **"Schedule not found"**: Run `add-canary-monitor` first
- **Timeout errors**: Increase `CANARY_STEP_TIMEOUT_SECONDS`
- **Artifact missing**: Check `artifacts/cadence/` directory exists
- **Duplicate runs**: The idempotency_key prevents re-runs in the same cron slot
