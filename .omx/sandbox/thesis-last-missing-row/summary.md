# Thesis Last Missing Row Cleanup Summary

Date: 2026-04-05

## Goal

Close the final known missing-any-thesis-row item in the last-90-day cohort using the existing `thesis-classify-batch` path only.

## Live Target Before Execution

- missing-any-thesis-row cohort in last 90 days: `1`
- target signal:
  - `signal_id=438`
  - `company_name=Mercari`
  - `source_api=greenhouse_jobs`
  - `created_at=2026-02-26T06:33:29.255596+00:00`
- stale latest-row cohort: `0`
- overdue regret checks: `0`

## Scratch Rehearsal

WAL-safe scratch backup:
- `.omx/sandbox/thesis-last-missing-row/signals-20260405-213256.db`

Scratch preflight:
- candidate count: `1`
- candidate id: `438`

Scratch run:

```powershell
python -m ops.cli quality --db .\.omx\sandbox\thesis-last-missing-row\signals-20260405-213256.db thesis-classify-batch --days 90 --limit 1 --model gemini-2.0-flash --prompt-version v1.6.0 --stop-on-error
```

Scratch result:
- attempted: `1`
- succeeded: `1`
- failed: `0`

Scratch verification:
- `signal_id=438` thesis rows: `1`
- scratch missing-any-row cohort: `0`

## Live Execution

Immediate live preflight:
- candidate count: `1`
- candidate id: `438`

Live run:

```powershell
python -m ops.cli quality --db signals.db thesis-classify-batch --days 90 --limit 1 --model gemini-2.0-flash --prompt-version v1.6.0 --stop-on-error
```

Live result:
- attempted: `1`
- succeeded: `1`
- failed: `0`

## Post-Run Verification

- `signal_id=438` thesis rows: `1`
- latest row:
  - `model='gemini-2.0-flash'`
  - `prompt_version='v1.6.0'`
- missing-any-thesis-row cohort in last 90 days: `0`
- stale latest-row cohort in last 90 days: `0`
- overdue regret checks: `0`

## Outcome

The final operational missing-any-thesis-row cleanup item is closed.
