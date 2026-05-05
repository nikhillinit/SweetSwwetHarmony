# Step 4B regret check — postponement record

This artifact records that the **2026-04-18 Step 4B regret check did not run on
fresh data** because the collection pipeline was silently frozen between
2026-03-01 and 2026-04-29. The check is therefore marked **postponed**, not
retroactively passed. R19 recovery (option A — restore + restart + reconcile)
ran on 2026-04-29 to clear the precondition.

## Required fields

| Field | Value |
|---|---|
| `postponement_date` | 2026-04-29 |
| `original_target_date` | 2026-04-18 |
| `reason` | R19 — collection pipeline silently frozen since 2026-03-01; live signals.db was truncated to 4 signals / 0 thesis_classifications. The 2026-04-18 Step 4B regret check would have evaluated `batch_publish` / `MERGE_WRITES_ENABLED` stability against frozen data. Per the canonical plan (`PLAN.corrected.md`) and the risk register row R19, this is recorded as postponed rather than passed. See `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` row R19. |
| `branch_taken` | `restore` (skip-restore conditions all failed: live DB was exactly `4 / 0`, schema v26 ≠ `CURRENT_SCHEMA_VERSION` v53, both watchdogs exited 1) |
| `freshness_36h_json` | see "Freshness 36h watchdog output" below — **exit 0 / OK** |
| `freshness_120h_json` | see "Freshness 120h watchdog output" below — **exit 0 / OK** |
| `pre_restore_signal_count` | 4 |
| `post_restore_signal_count` | 612 (immediately after restore); 766 after the keep-alive `-TestRun` collect added 154 fresh signals |
| `commit_hash` | `5715430762ebd00ed50b95987053280e2d57ccc0` (`git rev-parse HEAD` at start of session, branch `main`) |
| `signer` | nikhil@narralytics.ai (operator) |

## Evidence pointers

| Item | Path |
|---|---|
| Preflight snapshot | `artifacts/regret-check/preflight-2026-04-29-0050.txt` |
| Pre-restore safety backup | `pre-restore-20260429-075534.db` (1.40 MB — the truncated 4-row DB) |
| Backup used for restore | `signals.db.pre-step4b-promotion-20260404` (9.30 MB · 612 signals · 2593 thesis_classifications · schema v53) |
| `db_ops_ledger` entry | `.omx/logs/db_ops_ledger.jsonl` — 2026-04-29T07:55:35.161+00:00, tool=`restore_db`, status=`success` |
| First keep-alive artifact (also satisfies STEP 4 liveness gate) | `artifacts/keepalive/2026-04-29.json` |
| Risk register row | `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` (R19) |
| State document callout | `.planning/STATE.md` (dated body callout pointing at this artifact) |
| Plan executed | `C:\Users\nikhi\Downloads\PLAN.corrected.md` |

## Restore validation (STEP 3a)

| Validation item | Required | Observed | Result |
|---|---|---|---|
| Restore exit code | `0` | `0` | PASS |
| Pre-restore safety backup | `pre-restore-*.db` captured | `pre-restore-20260429-075534.db` (1.40 MB) | PASS |
| `db_ops_ledger` status | `success` row for `restore_db` | success @ 2026-04-29T07:55:35 | PASS |
| `PRAGMA integrity_check` | `ok` | `ok` | PASS |
| Restored signals count | 612 | 612 | PASS |
| Restored `thesis_classifications` | 2593 | 2593 | PASS |
| Schema version | 53 (= `storage.signal_store.CURRENT_SCHEMA_VERSION`) | 53 | PASS |
| API reachability at restore time | down (no `--force` needed) | `WinError 10061` (refused) | PASS |

## Liveness evidence (STEP 4)

The keep-alive scheduled task (registered in STEP 7) ran the same
`collect ... + freshness_watchdog --json` chain that STEP 4 specifies, so the
keep-alive artifact `artifacts/keepalive/2026-04-29.json` is the canonical
liveness evidence. It is reproduced inline below as `freshness_36h_json`.

Supporting (post-collect) DB state:

| Field | Value |
|---|---|
| signals (total) | 766 (+154 new from this collect) |
| thesis_classifications | 2593 (unchanged — `collect` does not classify) |
| schema | 53 |
| `max(created_at)` | `2026-04-29T08:04:39.789096+00:00` |
| `max(detected_at)` | `2026-04-29T07:30:00+00:00` |

Per-operational-collector row counts (cumulative, including pre-existing rows from the restored backup):

| Collector | Rows | `max(created_at)` |
|---|---|---|
| hacker_news | 236 | 2026-04-29T08:04:27.907 |
| arxiv | 373 | 2026-04-29T08:04:26.813 |
| rss_feeds | 95 | 2026-04-29T08:04:28.363 |
| news_api | 18 | 2026-04-29T08:04:39.789 |

## Freshness 36h watchdog output (STEP 4 primary gate)

Source: `artifacts/keepalive/2026-04-29.json` (written by the `-TestRun` of
`HarmonicKeepAlive` scheduled task, which runs the watchdog at default 36h
threshold).

```json
{
  "checked_at": "2026-04-29T08:07:57.365950+00:00",
  "threshold_hours": 36,
  "exit_code": 0,
  "status": "OK",
  "collectors": [
    {"source_api": "arxiv",          "category": "operational",   "last_created": "2026-04-29T08:04:26.813557+00:00", "age_hours": 0.06,    "status": "FRESH"},
    {"source_api": "ashby_jobs",     "category": "informational", "last_created": "2026-02-26T06:33:29.281237+00:00", "age_hours": 1489.57, "status": "UNKNOWN"},
    {"source_api": "github",         "category": "informational", "last_created": "2026-01-10T12:18:09.019836+00:00", "age_hours": 2611.83, "status": "UNKNOWN"},
    {"source_api": "greenhouse_jobs","category": "informational", "last_created": "2026-02-26T06:33:29.275988+00:00", "age_hours": 1489.57, "status": "UNKNOWN"},
    {"source_api": "hacker_news",    "category": "operational",   "last_created": "2026-04-29T08:04:27.907072+00:00", "age_hours": 0.06,    "status": "FRESH"},
    {"source_api": "lever_jobs",     "category": "informational", "last_created": "2026-02-26T06:33:29.284244+00:00", "age_hours": 1489.57, "status": "UNKNOWN"},
    {"source_api": "manual_seed_buzz","category": "informational","last_created": "2026-02-26T07:31:31.247155+00:00", "age_hours": 1488.61, "status": "UNKNOWN"},
    {"source_api": "news_api",       "category": "operational",   "last_created": "2026-04-29T08:04:39.789096+00:00", "age_hours": 0.05,    "status": "FRESH"},
    {"source_api": "product_hunt",   "category": "informational", "last_created": "2026-01-10T12:18:09.035890+00:00", "age_hours": 2611.83, "status": "UNKNOWN"},
    {"source_api": "rss_feeds",      "category": "operational",   "last_created": "2026-04-29T08:04:28.363218+00:00", "age_hours": 0.06,    "status": "FRESH"}
  ],
  "failures": []
}
```

## Freshness 120h watchdog output (STEP 5 — Step 4B regret-check freshness)

Command: `python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 120`

```json
{
  "checked_at": "2026-04-29T08:10:46.758719+00:00",
  "threshold_hours": 120,
  "exit_code": 0,
  "status": "OK",
  "collectors": [
    {"source_api": "arxiv",          "category": "operational",   "last_created": "2026-04-29T08:04:26.813557+00:00", "age_hours": 0.11,    "status": "FRESH"},
    {"source_api": "ashby_jobs",     "category": "informational", "last_created": "2026-02-26T06:33:29.281237+00:00", "age_hours": 1489.62, "status": "UNKNOWN"},
    {"source_api": "github",         "category": "informational", "last_created": "2026-01-10T12:18:09.019836+00:00", "age_hours": 2611.88, "status": "UNKNOWN"},
    {"source_api": "greenhouse_jobs","category": "informational", "last_created": "2026-02-26T06:33:29.275988+00:00", "age_hours": 1489.62, "status": "UNKNOWN"},
    {"source_api": "hacker_news",    "category": "operational",   "last_created": "2026-04-29T08:04:27.907072+00:00", "age_hours": 0.11,    "status": "FRESH"},
    {"source_api": "lever_jobs",     "category": "informational", "last_created": "2026-02-26T06:33:29.284244+00:00", "age_hours": 1489.62, "status": "UNKNOWN"},
    {"source_api": "manual_seed_buzz","category": "informational","last_created": "2026-02-26T07:31:31.247155+00:00", "age_hours": 1488.65, "status": "UNKNOWN"},
    {"source_api": "news_api",       "category": "operational",   "last_created": "2026-04-29T08:04:39.789096+00:00", "age_hours": 0.10,    "status": "FRESH"},
    {"source_api": "product_hunt",   "category": "informational", "last_created": "2026-01-10T12:18:09.035890+00:00", "age_hours": 2611.88, "status": "UNKNOWN"},
    {"source_api": "rss_feeds",      "category": "operational",   "last_created": "2026-04-29T08:04:28.363218+00:00", "age_hours": 0.11,    "status": "FRESH"}
  ],
  "failures": []
}
```

## Recurrence install (STEP 7)

`HarmonicKeepAlive` registered via the canonical PowerShell installer:

```
powershell -ExecutionPolicy Bypass -File scripts/red-team-hybrid/install_keepalive_task.ps1 -ProjectRoot C:\dev\Harmonic -TestRun
```

| Item | Value |
|---|---|
| Task name | `HarmonicKeepAlive` |
| Trigger | Daily at `08:00` local |
| Inner script | `scripts/red-team-hybrid/_keepalive_daily.cmd` |
| Last run (the `-TestRun`) | `4/29/2026 1:07:26 AM` local |
| Last task result | `0` |
| Next scheduled run | `4/29/2026 8:00:00 AM` local |
| First artifact | `artifacts/keepalive/2026-04-29.json` (2,149 bytes) |

**Recurrence claim:** **partial.** A `-TestRun` produced one artifact and the
task is registered with a daily 08:00 trigger. The plan requires a *second*
artifact from a non-`-TestRun` scheduled run (and a successful `LastTaskResult`
from that run) before recurrence is fully proved. The next scheduled run is
2026-04-29 08:00 local; verify after that:
- `Get-ScheduledTaskInfo -TaskName HarmonicKeepAlive` shows `LastRunTime` >= 2026-04-29 08:00 local and `LastTaskResult = 0`
- A second artifact `artifacts/keepalive/2026-04-30.json` (or the next-day stamp) exists

Until then, **R19 remains partially closed** per the plan's definition of
done item (f).

## What this artifact does NOT claim

- It does **not** retroactively pass the 2026-04-18 Step 4B regret check.
- It does **not** evaluate `batch_publish` or `MERGE_WRITES_ENABLED` stability against this newly-fresh data.
- It does **not** unfreeze Vault Wave 2.2 (still observation-only).
- It does **not** push anything to Notion. `DELIVERY_MODE=batch_publish` continues to gate that path; the keep-alive only runs `collect`, not `process`.

## Next actions

1. Wait for the 2026-04-29 08:00 local scheduled run.
2. Verify a second `artifacts/keepalive/*.json` lands and `LastTaskResult = 0`.
3. Append that confirmation to this artifact (or a successor) to fully close R19 per DoD item (f).
4. After R19 is fully closed, plan and execute a *fresh* Step 4B regret check
   evaluation window using only post-restore data.

## Closure

R19 closed 2026-04-30 (data) / 2026-05-01 (verification commit). The 2026-04-30 scheduled keep-alive run fired on time at 2026-04-30T15:00:01Z and produced `artifacts/keepalive/2026-04-30.json` with `exit_code=0`, `status="OK"`, all 4 operational collectors `FRESH`, and `checked_at` (`2026-04-30T15:01:38Z`) inside the `[14:30Z, 23:00Z]` closure window. See `closure-2026-04-30.md` in this directory for the full per-criterion check and the explanation of why the closure was applied manually after the remote verifier (`trig_01P3begEKbbRsZCUkijhNRpd`) correctly took its Branch C precondition stop on Issue #146.
