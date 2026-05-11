# Monday AM Freeze Drill Readout

Use this checklist for the Monday, 2026-05-11 induced-freeze readout.

Do not invoke `scripts/red-team-hybrid/install_keepalive_task.ps1` against the
live repo/task with `-TaskName HarmonicFreezeDrill` before this readout is
complete. That can rewrite the live wrapper and/or re-register the active task,
contaminating the in-flight drill.

## 1. Host Opportunity Gate

```powershell
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-ScheduledTaskInfo -TaskName "HarmonicFreezeDrill"
Get-ScheduledTask -TaskName "HarmonicFreezeDrill" | Select-Object TaskName, State
Get-ScheduledTask -TaskName "HarmonicKeepAlive" -ErrorAction SilentlyContinue | Select-Object TaskName, State
```

Check for sleep, resume, unexpected shutdown, or reboot events during the
observation window:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ID=1,42,107,6008; StartTime=(Get-Date).AddHours(-18)} |
  Select-Object TimeCreated, Id, LevelDisplayName, Message |
  Format-Table -AutoSize
```

If the host slept, hibernated, powered off, rebooted after the drill start, or
missed the scheduled run, classify the readout as `AMBIGUOUS`.

## 2. Freshness Source Of Record

Run the watchdog with the 12-hour drill threshold:

```powershell
python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 12 --operational rss_feeds,greenhouse_jobs,ashby_jobs
```

The actual JSON contract is:

```json
{
  "checked_at": "2026-05-11T...",
  "threshold_hours": 12,
  "exit_code": 1,
  "status": "FAIL",
  "collectors": [
    {
      "source_api": "rss_feeds",
      "category": "operational",
      "last_created": "...",
      "age_hours": 12.34,
      "status": "STALE"
    }
  ],
  "failures": ["rss_feeds: ..."]
}
```

`collectors` is a list of records, not an object keyed by source name.

## 3. Positive Peer DB Gate

The peer rows must be newer than the observed scheduled run start, not merely
under the 12-hour threshold from the Sunday baseline.

```powershell
$runStartLocal = (Get-ScheduledTaskInfo -TaskName "HarmonicFreezeDrill").LastRunTime
$env:RUN_START_UTC = $runStartLocal.ToUniversalTime().ToString("o")
@'
import os
import sqlite3
from datetime import datetime

def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

peers = ("greenhouse_jobs", "ashby_jobs")
run_start = parse_ts(os.environ["RUN_START_UTC"])
conn = sqlite3.connect("signals.db")
rows = dict(conn.execute(
    "SELECT source_api, MAX(created_at) "
    "FROM signals "
    "WHERE source_api IN ('greenhouse_jobs','ashby_jobs') "
    "GROUP BY source_api"
))
print(rows)
missing = [
    peer for peer in peers
    if peer not in rows or parse_ts(rows[peer]) <= run_start
]
raise SystemExit(1 if missing else 0)
'@ | python -
```

If either required peer is absent or its `MAX(created_at)` is not after the
scheduled run start, classify the result as `AMBIGUOUS`.

## 4. Interpretation

Pass only if all are true:

- Host opportunity is proven for at least 12 availability-adjusted hours.
- `HarmonicFreezeDrill` ran during the observation window.
- Broad `HarmonicKeepAlive` did not overlap.
- Watchdog output uses `--threshold-hours 12`.
- `rss_feeds` is `STALE`, not `MISSING`.
- Required peer source APIs from the baseline-positive `job_postings` run are
  fresh and have `MAX(created_at)` after the scheduled run start.

Non-pass if any are true:

- Broad `HarmonicKeepAlive` overlapped.
- The default 36-hour threshold is used.
- `collector_health` is used as primary freshness proof.
- Any config override, DB corruption, heartbeat manipulation, or synthetic
  canary substitutes for real collector peer freshness.

Ambiguous if any are true:

- Host slept, hibernated, powered off, rebooted, or missed the task.
- Host opportunity cannot be proven.
- `rss_feeds` or a required peer is `MISSING`.
- `job_postings` produced no post-run peer DB rows.

GitHub is auth/plumbing corroboration unless it inserts rows during the window.
Scheduler, wrapper, file, and runner witnesses are corroboration only; the
freshness verdict comes from `signals.created_at`.
